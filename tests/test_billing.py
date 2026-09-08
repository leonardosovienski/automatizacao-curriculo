"""Regressões financeiras: nenhuma cobrança duplicada ou consumo sem autorização."""

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import stripe
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from api import billing
from api.billing_models import BillingControleDB, EventoStripeDB, UsoMensalDB
from api.database import AssinaturaDB, Base, BuscaDB, Usuario, sessao


@pytest.fixture
def banco(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'billing.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 20},
    )
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_local")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_plano")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_local")
    monkeypatch.setenv("TRIAGEM_MONTHLY_SEARCH_LIMIT", "30")
    monkeypatch.setenv("TRIAGEM_MONTHLY_ANALYSIS_LIMIT", "300")
    monkeypatch.setattr(
        billing,
        "_plano",
        lambda: {
            "nome": "Plano",
            "valor_centavos": 3900,
            "moeda": "brl",
            "intervalo": "month",
            "intervalo_contagem": 1,
        },
    )
    with fabrica() as db:
        usuario = Usuario(id="u1", email="cliente@example.test", senha_hash="irrelevante")
        db.add(usuario)
        db.flush()
        db.add(
            AssinaturaDB(
                usuario_id="u1",
                stripe_customer_id="cus_1",
                stripe_subscription_id="sub_1",
                status="active",
                preco_id="price_plano",
                periodo_atual_fim=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        db.commit()
    yield fabrica
    engine.dispose()


def assinatura(status="active", id="sub_1", created=1, price="price_plano"):
    return {
        "id": id,
        "customer": "cus_1",
        "status": status,
        "created": created,
        "items": {
            "data": [{"price": {"id": price}, "current_period_end": int(time.time()) + 86_400}]
        },
    }


def evento(id="evt_1", tipo="customer.subscription.updated", snapshot=None):
    return {"id": id, "type": tipo, "livemode": False, "data": {"object": snapshot or assinatura()}}


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("status", "past_due"),
        ("status", "unpaid"),
        ("status", "canceled"),
        ("preco_id", "price_outro"),
        ("stripe_subscription_id", None),
        ("periodo_atual_fim", None),
        ("periodo_atual_fim", datetime(2020, 1, 1)),
    ],
)
def test_uso_pago_exige_status_preco_periodo_e_assinatura(banco, campo, valor):
    with banco() as db:
        registro = db.get(AssinaturaDB, "u1")
        setattr(registro, campo, valor)
        db.commit()
        with pytest.raises(HTTPException) as erro:
            billing.reservar_busca(db, db.get(Usuario, "u1"), 5)
        assert erro.value.status_code == 402
        assert db.scalar(select(func.count()).select_from(UsoMensalDB)) == 0


def test_plano_sem_preco_configurado_nao_libera_uso(banco, monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_ID")
    with banco() as db:
        assert billing.assinatura_ativa(db.get(AssinaturaDB, "u1")) is False


def test_quota_reserva_pior_caso_e_reverte_se_job_nao_for_aceito(banco, monkeypatch):
    monkeypatch.setenv("TRIAGEM_MONTHLY_ANALYSIS_LIMIT", "6")
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        billing.reservar_busca(db, usuario, 5)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(UsoMensalDB)) == 0
        billing.reservar_busca(db, usuario, 5)
        db.commit()
        with pytest.raises(HTTPException) as erro:
            billing.reservar_busca(db, usuario, 2)
        assert erro.value.status_code == 429
        db.rollback()
        uso = billing._uso(db, usuario)
        assert (uso["buscas_utilizadas"], uso["analises_reservadas"]) == (1, 5)


def test_material_reserva_duas_analises_sem_gastar_busca(banco, monkeypatch):
    monkeypatch.setenv("TRIAGEM_MONTHLY_SEARCH_LIMIT", "1")
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        billing.reservar_busca(db, usuario, 1)
        db.commit()
        billing.reservar_material(db, usuario)
        db.commit()
        assert billing._uso(db, usuario)["buscas_utilizadas"] == 1
        assert billing._uso(db, usuario)["analises_reservadas"] == 3


def test_quota_reinicia_por_mes_utc_e_isola_contas(banco, monkeypatch):
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        monkeypatch.setattr(
            billing, "_agora", lambda: datetime(2026, 1, 31, 23, 59, tzinfo=timezone.utc)
        )
        billing.reservar_busca(db, usuario, 4)
        db.commit()
        monkeypatch.setattr(billing, "_agora", lambda: datetime(2026, 2, 1, tzinfo=timezone.utc))
        assert billing._uso(db, usuario)["buscas_utilizadas"] == 0
        db.add(Usuario(id="u2", email="outro@example.test", senha_hash="irrelevante"))
        db.commit()
        assert billing._uso(db, db.get(Usuario, "u2"))["analises_reservadas"] == 0


def test_buscas_simultaneas_nao_ultrapassam_cota(banco, monkeypatch):
    monkeypatch.setenv("TRIAGEM_MONTHLY_SEARCH_LIMIT", "3")

    def reservar(_):
        with banco() as db:
            try:
                billing.reservar_busca(db, db.get(Usuario, "u1"), 1)
                db.commit()
                return 202
            except HTTPException as erro:
                db.rollback()
                return erro.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        respostas = list(executor.map(reservar, range(8)))
    assert respostas.count(202) == 3
    assert respostas.count(429) == 5
    with banco() as db:
        assert billing._uso(db, db.get(Usuario, "u1"))["buscas_utilizadas"] == 3


def test_busca_ativa_e_reserva_sao_atomicas(banco):
    def iniciar(_):
        with banco() as db:
            try:
                billing.reservar_busca(db, db.get(Usuario, "u1"), 1)
                db.add(BuscaDB(usuario_id="u1", pedido="Python remoto", limite=1))
                db.commit()
                return 202
            except HTTPException as erro:
                db.rollback()
                return erro.status_code

    with ThreadPoolExecutor(max_workers=4) as executor:
        respostas = list(executor.map(iniciar, range(4)))
    assert sorted(respostas) == [202, 409, 409, 409]


def test_evento_duplicado_e_reentrega_ignoram_payload_antigo(banco, monkeypatch):
    consultar = Mock(return_value={"data": [assinatura("canceled")], "has_more": False})
    monkeypatch.setattr(stripe.Subscription, "list", consultar)
    with banco() as db:
        billing._processar_evento(db, evento(snapshot=assinatura("active")))
        assert db.get(AssinaturaDB, "u1").status == "canceled"
        assert billing._processar_evento(db, evento())["duplicado"] is True
        assert consultar.call_count == 1
        assert db.scalar(select(func.count()).select_from(EventoStripeDB)) == 1


def test_exclusao_atrasada_nao_cancela_nova_assinatura(banco, monkeypatch):
    monkeypatch.setattr(
        stripe.Subscription,
        "list",
        lambda **kw: {
            "data": [
                assinatura("canceled", id="sub_antiga", created=1),
                assinatura(id="sub_nova", created=2),
            ],
            "has_more": False,
        },
    )
    with banco() as db:
        billing._processar_evento(
            db,
            evento(
                tipo="customer.subscription.deleted",
                snapshot=assinatura("canceled", id="sub_antiga"),
            ),
        )
        registro = db.get(AssinaturaDB, "u1")
        assert registro.stripe_subscription_id == "sub_nova"
        assert billing.assinatura_ativa(registro)


def test_falha_stripe_reverte_evento_e_permite_retry(banco, monkeypatch):
    consultar = Mock(side_effect=stripe.APIConnectionError("falha simulada"))
    monkeypatch.setattr(stripe.Subscription, "list", consultar)
    with banco() as db:
        with pytest.raises(HTTPException) as erro:
            billing._processar_evento(db, evento())
        assert erro.value.status_code == 503
        assert db.get(EventoStripeDB, "evt_1") is None
        consultar.side_effect = None
        consultar.return_value = {"data": [assinatura()], "has_more": False}
        assert billing._processar_evento(db, evento())["recebido"]


def test_periodo_basil_e_versao_anterior(banco):
    with banco() as db:
        objeto = assinatura()
        billing._aplicar_evento_assinatura(db, objeto)
        db.commit()
        assert billing.assinatura_ativa(db.get(AssinaturaDB, "u1"))
        objeto["current_period_end"] = objeto["items"]["data"][0].pop("current_period_end")
        billing._aplicar_evento_assinatura(db, objeto)
        db.commit()
        assert billing.assinatura_ativa(db.get(AssinaturaDB, "u1"))
        objeto.pop("current_period_end")
        billing._aplicar_evento_assinatura(db, objeto)
        db.commit()
        assert not billing.assinatura_ativa(db.get(AssinaturaDB, "u1"))


@pytest.mark.parametrize(
    "status", ["active", "trialing", "past_due", "unpaid", "incomplete", "paused"]
)
def test_checkout_recusa_segunda_assinatura(banco, monkeypatch, status):
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": [assinatura(status)]})
    criar = Mock()
    monkeypatch.setattr(stripe.checkout.Session, "create", criar)
    with banco() as db:
        with pytest.raises(HTTPException) as erro:
            billing.criar_checkout(db.get(Usuario, "u1"), db)
        assert erro.value.status_code == 409
        criar.assert_not_called()


def test_checkout_recupera_sessao_aberta(banco, monkeypatch):
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": []})
    monkeypatch.setattr(
        stripe.checkout.Session,
        "list",
        lambda **kw: {
            "data": [
                {
                    "id": "cs_existente",
                    "mode": "subscription",
                    "url": "https://checkout.stripe.com/test",
                }
            ]
        },
    )
    criar = Mock()
    monkeypatch.setattr(stripe.checkout.Session, "create", criar)
    with banco() as db:
        assert billing.criar_checkout(db.get(Usuario, "u1"), db)["url"].endswith("/test")
        assert db.get(BillingControleDB, "u1").checkout_session_id == "cs_existente"
        criar.assert_not_called()


def test_checkout_idempotencia_e_urls_do_operador(banco, monkeypatch):
    monkeypatch.setenv("TRIAGEM_PUBLIC_URL", "https://app.example.test")
    monkeypatch.delenv("STRIPE_SUCCESS_URL", raising=False)
    monkeypatch.delenv("STRIPE_CANCEL_URL", raising=False)
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": []})
    monkeypatch.setattr(stripe.checkout.Session, "list", lambda **kw: {"data": []})
    criar = Mock(
        return_value={"id": "cs_nova", "status": "open", "url": "https://checkout.stripe.com/nova"}
    )
    monkeypatch.setattr(stripe.checkout.Session, "create", criar)
    with banco() as db:
        billing.criar_checkout(db.get(Usuario, "u1"), db)
        parametros = criar.call_args.kwargs
        assert parametros["idempotency_key"].startswith("checkout-")
        assert parametros["success_url"] == "https://app.example.test/?sucesso=1"
        assert parametros["line_items"] == [{"price": "price_plano", "quantity": 1}]


def test_exclusao_cancela_cobranca_e_falha_preserva_conta(banco, monkeypatch):
    cancelar = Mock(side_effect=stripe.APIConnectionError("falha simulada"))
    monkeypatch.setattr(stripe.Customer, "delete", cancelar)
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        with pytest.raises(HTTPException):
            billing.encerrar_cobranca_para_exclusao(db, usuario)
        db.rollback()
        assert db.get(Usuario, "u1") is not None
        cancelar.side_effect = None
        billing.encerrar_cobranca_para_exclusao(db, usuario)
        cancelar.assert_called_with("cus_1")
        assert db.get(AssinaturaDB, "u1").status == "canceled"


def test_webhook_verifica_assinatura_e_modo(banco, monkeypatch):
    app = FastAPI()
    app.include_router(billing.router)

    def sessao_teste():
        with banco() as db:
            yield db

    app.dependency_overrides[sessao] = sessao_teste
    cliente = TestClient(app)
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": [assinatura()]})
    assert cliente.post("/billing/webhook", content=b"{}").status_code == 400

    def enviar(dados):
        payload = json.dumps(dados).encode()
        timestamp = str(int(time.time()))
        assinatura_header = hmac.new(
            b"whsec_local", timestamp.encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        return cliente.post(
            "/billing/webhook",
            content=payload,
            headers={"stripe-signature": f"t={timestamp},v1={assinatura_header}"},
        )

    assert enviar(evento()).status_code == 200
    errado = evento("evt_modo")
    errado["livemode"] = True
    assert enviar(errado).status_code == 400


def test_status_expoe_plano_e_uso_sem_ids_stripe(banco):
    with banco() as db:
        status = billing.status_assinatura(db.get(Usuario, "u1"), db)
        assert status["ativa"] is True
        assert status["plano"]["valor_centavos"] == 3900
        assert status["uso"]["analises_limite"] == 300
        assert "stripe_customer_id" not in status


def test_objetos_reais_sdk_no_checkout_e_sincronizacao(banco, monkeypatch):
    def sdk(dados):
        return stripe.StripeObject.construct_from(dados, "sk_test_local")

    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: sdk({"data": []}))
    monkeypatch.setattr(stripe.checkout.Session, "list", lambda **kw: sdk({"data": []}))
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **kw: sdk(
            {"id": "cs_sdk", "status": "open", "url": "https://checkout.stripe.com/sdk"}
        ),
    )
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        assert billing.criar_checkout(usuario, db)["url"].endswith("/sdk")
        monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: sdk({"data": [assinatura()]}))
        assert billing.sincronizar_assinatura(usuario, db)["ativa"]


def test_plano_real_sdk_e_preco_arquivado(banco, monkeypatch):
    billing._plano_cache.cache_clear()
    preco = {
        "id": "price_plano",
        "active": True,
        "unit_amount": 4990,
        "currency": "brl",
        "product": {"id": "prod_1", "name": "Busca Pro"},
        "recurring": {"interval": "month", "interval_count": 1},
    }
    monkeypatch.setattr(
        stripe.Price,
        "retrieve",
        lambda *args, **kw: stripe.StripeObject.construct_from(preco, "sk_test"),
    )
    assert billing._plano_cache("price_plano", "sk_test", 1)["nome"] == "Busca Pro"
    preco["active"] = False
    with pytest.raises(HTTPException) as erro:
        billing._plano_cache("price_plano", "sk_test", 2)
    assert erro.value.status_code == 503
    billing._plano_cache.cache_clear()


def test_checkout_consulta_sessao_conhecida_antes_de_criar_nova(banco, monkeypatch):
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": []})
    monkeypatch.setattr(stripe.checkout.Session, "list", lambda **kw: {"data": []})
    monkeypatch.setattr(
        stripe.checkout.Session,
        "retrieve",
        lambda *args: stripe.StripeObject.construct_from(
            {"id": "cs_existente", "status": "open", "url": "https://checkout.stripe.com/reuse"},
            "sk_test",
        ),
    )
    criar = Mock()
    monkeypatch.setattr(stripe.checkout.Session, "create", criar)
    with banco() as db:
        db.add(BillingControleDB(usuario_id="u1", checkout_session_id="cs_existente"))
        db.commit()
        assert billing.criar_checkout(db.get(Usuario, "u1"), db)["url"].endswith("/reuse")
        criar.assert_not_called()


def test_checkout_aguarda_pagamento_em_confirmacao(banco, monkeypatch):
    monkeypatch.setattr(
        stripe.Subscription,
        "list",
        lambda **kw: {"data": [assinatura("canceled", id="sub_antiga")]},
    )
    monkeypatch.setattr(stripe.checkout.Session, "list", lambda **kw: {"data": []})
    monkeypatch.setattr(
        stripe.checkout.Session,
        "retrieve",
        lambda *args: {"id": "cs_completa", "status": "complete", "subscription": "sub_nova"},
    )
    criar = Mock()
    monkeypatch.setattr(stripe.checkout.Session, "create", criar)
    with banco() as db:
        db.add(BillingControleDB(usuario_id="u1", checkout_session_id="cs_completa"))
        db.commit()
        with pytest.raises(HTTPException) as erro:
            billing.criar_checkout(db.get(Usuario, "u1"), db)
        assert erro.value.status_code == 409
        criar.assert_not_called()


def test_cliente_novo_usa_chave_idempotente(banco, monkeypatch):
    criar = Mock(return_value=stripe.Customer.construct_from({"id": "cus_novo"}, "sk_test"))
    monkeypatch.setattr(stripe.Customer, "create", criar)
    with banco() as db:
        db.add(Usuario(id="u2", email="novo@example.test", senha_hash="irrelevante"))
        db.commit()
        usuario = db.get(Usuario, "u2")
        registro = billing._obter_ou_criar_assinatura(db, usuario)
        assert registro.stripe_customer_id == "cus_novo"
        assert criar.call_args.kwargs["idempotency_key"] == "customer-u2"
        db.commit()
        billing._obter_ou_criar_assinatura(db, usuario)
        assert criar.call_count == 1


def test_cliente_excluido_revoga_e_eventos_desconhecidos_nao_mudam_cobranca(banco):
    with banco() as db:
        billing._processar_evento(db, evento(tipo="customer.deleted", snapshot={"id": "cus_1"}))
        assert not billing.assinatura_ativa(db.get(AssinaturaDB, "u1"))
        assert billing._processar_evento(db, evento("evt_outro", tipo="charge.succeeded"))[
            "recebido"
        ]
        assert db.get(EventoStripeDB, "evt_outro") is None
        billing._processar_evento(
            db, evento("evt_outro_cliente", snapshot={"customer": "cus_outro"})
        )
        assert db.get(AssinaturaDB, "u1").status == "canceled"


def test_stripe_assinaturas_paginadas(banco, monkeypatch):
    listar = Mock(
        side_effect=[
            {"data": [assinatura(id="sub_1")], "has_more": True},
            {"data": [assinatura(id="sub_2")], "has_more": False},
        ]
    )
    monkeypatch.setattr(stripe.Subscription, "list", listar)
    assert len(billing._listar_assinaturas("cus_1")) == 2
    assert listar.call_args.kwargs["starting_after"] == "sub_1"


@pytest.mark.parametrize(
    "url", ["http://public.example.test", "javascript:alert(1)", "https://user:senha@site.test"]
)
def test_retorno_cobranca_rejeita_url_insegura(monkeypatch, url):
    monkeypatch.setenv("STRIPE_SUCCESS_URL", url)
    with pytest.raises(HTTPException) as erro:
        billing._url_retorno("STRIPE_SUCCESS_URL")
    assert erro.value.status_code == 503


def test_portal_e_erros_controlados(banco, monkeypatch):
    portal = Mock(return_value={"url": "https://billing.stripe.com/portal"})
    monkeypatch.setattr(stripe.billing_portal.Session, "create", portal)
    with banco() as db:
        assert billing.criar_portal(db.get(Usuario, "u1"), db)["url"].endswith("/portal")
        portal.side_effect = stripe.APIConnectionError("falha")
        with pytest.raises(HTTPException) as erro:
            billing.criar_portal(db.get(Usuario, "u1"), db)
        assert erro.value.status_code == 503


def test_limite_chamadas_checkout_antes_do_stripe(banco, monkeypatch):
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": [assinatura()]})
    with banco() as db:
        usuario = db.get(Usuario, "u1")
        for _ in range(10):
            with pytest.raises(HTTPException) as erro:
                billing.criar_checkout(usuario, db)
            assert erro.value.status_code == 409
        with pytest.raises(HTTPException) as erro:
            billing.criar_checkout(usuario, db)
        assert erro.value.status_code == 429
