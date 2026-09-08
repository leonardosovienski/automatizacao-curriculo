"""Stripe com reconciliação autoritativa, checkout único e cotas transacionais."""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlsplit

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .auth import usuario_atual
from .billing_models import BillingControleDB, EventoStripeDB, UsoMensalDB
from .database import AssinaturaDB, BuscaDB, Usuario, sessao
from .security import limitar_tentativas

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)
_STATUS_ATIVOS = {"active", "trialing"}
_STATUS_TERMINAIS = {"canceled", "incomplete_expired"}
_EVENTOS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.deleted",
}


def _dados(objeto) -> dict:
    # Stripe 15 removeu a herança de dict. Converte também objetos aninhados.
    return objeto if isinstance(objeto, dict) else objeto.to_dict()


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _utc(data: datetime) -> datetime:
    return (
        data.replace(tzinfo=timezone.utc) if data.tzinfo is None else data.astimezone(timezone.utc)
    )


def _stripe_configurado() -> bool:
    return all(
        os.environ.get(nome)
        for nome in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET")
    )


def _exigir_stripe() -> None:
    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(503, "Cobrança não está configurada neste ambiente.")
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    stripe.max_network_retries = 1
    if stripe.default_http_client is None:
        stripe.default_http_client = stripe.RequestsClient(timeout=10)


def _falha_stripe(erro: Exception) -> HTTPException:
    logger.warning("Falha na integração Stripe: %s", type(erro).__name__)
    return HTTPException(503, "Cobrança temporariamente indisponível. Tente novamente.")


def _bloquear_usuario(db: Session, usuario_id: str) -> None:
    # UPDATE sem alteração toma lock também no SQLite, que ignora FOR UPDATE.
    # Checkout, webhook, cotas e exclusão compartilham o lock até commit/rollback.
    resultado = db.execute(
        update(Usuario).where(Usuario.id == usuario_id).values(id=Usuario.id),
        execution_options={"synchronize_session": False},
    )
    if resultado.rowcount != 1:
        raise HTTPException(401, "Conta não encontrada.")


def assinatura_ativa(assinatura: AssinaturaDB | None) -> bool:
    preco = os.environ.get("STRIPE_PRICE_ID")
    return bool(
        assinatura
        and preco
        and assinatura.preco_id == preco
        and assinatura.stripe_subscription_id
        and assinatura.status in _STATUS_ATIVOS
        and assinatura.periodo_atual_fim
        and _utc(assinatura.periodo_atual_fim) > _agora()
    )


def exigir_assinatura(db: Session, usuario: Usuario) -> AssinaturaDB:
    assinatura = db.get(AssinaturaDB, usuario.id, populate_existing=True)
    if not assinatura_ativa(assinatura):
        raise HTTPException(402, "Uma assinatura ativa é necessária para buscar vagas.")
    return assinatura


def _limites() -> tuple[int, int]:
    try:
        limites = (
            int(os.environ.get("TRIAGEM_MONTHLY_SEARCH_LIMIT", "30")),
            int(os.environ.get("TRIAGEM_MONTHLY_ANALYSIS_LIMIT", "300")),
        )
        if min(limites) < 1:
            raise ValueError
        return limites
    except ValueError as erro:
        raise HTTPException(
            503, "Os limites do plano precisam ser configurados pelo operador."
        ) from erro


def _periodo_uso() -> tuple[str, datetime]:
    agora = _agora()
    proximo = datetime(
        agora.year + (agora.month == 12),
        1 if agora.month == 12 else agora.month + 1,
        1,
        tzinfo=timezone.utc,
    )
    return agora.strftime("%Y-%m"), proximo


def reservar_busca(db: Session, usuario: Usuario, limite: int) -> None:
    """Reserva custo junto com a busca; o chamador confirma a transação.

    Buscas aceitas consomem a cota mesmo sem resultados ou em falhas de provedor.
    Rollback antes da aceitação desfaz a reserva. O worker limita análises a limite.
    """
    if limite < 1 or limite > 20:
        raise HTTPException(422, "O limite deve estar entre 1 e 20 vagas.")
    _reservar_uso(db, usuario, limite, buscas=1)


def reservar_material(db: Session, usuario: Usuario) -> None:
    """Reserva duas chamadas de IA e nenhuma busca, na transação do job."""
    _reservar_uso(db, usuario, 2, buscas=0)


def _reservar_uso(db: Session, usuario: Usuario, analises: int, buscas: int) -> None:
    _bloquear_usuario(db, usuario.id)
    exigir_assinatura(db, usuario)
    ativa = db.scalar(
        select(BuscaDB.id)
        .where(BuscaDB.usuario_id == usuario.id, BuscaDB.estado.in_(["pendente", "processando"]))
        .limit(1)
    )
    if ativa:
        raise HTTPException(409, "Já existe uma busca em andamento para esta conta.")
    buscas_limite, analises_limite = _limites()
    mes, _ = _periodo_uso()
    uso = db.get(UsoMensalDB, (usuario.id, mes), populate_existing=True)
    if not uso:
        uso = UsoMensalDB(usuario_id=usuario.id, mes=mes, buscas=0, analises=0)
        db.add(uso)
        db.flush()
    resultado = db.execute(
        update(UsoMensalDB)
        .where(
            UsoMensalDB.usuario_id == usuario.id,
            UsoMensalDB.mes == mes,
            UsoMensalDB.buscas + buscas <= buscas_limite,
            UsoMensalDB.analises + analises <= analises_limite,
        )
        .values(buscas=UsoMensalDB.buscas + buscas, analises=UsoMensalDB.analises + analises)
    )
    if resultado.rowcount != 1:
        raise HTTPException(
            429, "A cota mensal do plano foi atingida. Ela renova no início do próximo mês UTC."
        )


def _uso(db: Session, usuario: Usuario) -> dict:
    buscas_limite, analises_limite = _limites()
    mes, reinicia = _periodo_uso()
    uso = db.get(UsoMensalDB, (usuario.id, mes), populate_existing=True)
    return {
        "mes": mes,
        "buscas_utilizadas": uso.buscas if uso else 0,
        "buscas_limite": buscas_limite,
        "analises_reservadas": uso.analises if uso else 0,
        "analises_limite": analises_limite,
        "reinicia_em": reinicia,
    }


@lru_cache(maxsize=16)
def _plano_cache(preco_id: str, chave: str, janela: int) -> dict:
    preco = _dados(stripe.Price.retrieve(preco_id, api_key=chave, expand=["product"]))
    recorrencia = preco.get("recurring") or {}
    if not preco.get("active") or not recorrencia or preco.get("unit_amount") is None:
        raise HTTPException(
            503, "O operador precisa configurar um preço recorrente ativo de valor fixo."
        )
    produto = preco.get("product")
    return {
        "nome": produto.get("name", "Plano mensal") if hasattr(produto, "get") else "Plano mensal",
        "valor_centavos": preco["unit_amount"],
        "moeda": preco["currency"],
        "intervalo": recorrencia["interval"],
        "intervalo_contagem": recorrencia["interval_count"],
    }


def _plano() -> dict | None:
    if not _stripe_configurado():
        return None
    _exigir_stripe()
    return _plano_cache(
        os.environ["STRIPE_PRICE_ID"], os.environ["STRIPE_SECRET_KEY"], int(time.time() // 300)
    )


def _status(db: Session, usuario: Usuario) -> dict:
    assinatura = db.get(AssinaturaDB, usuario.id, populate_existing=True)
    try:
        plano = _plano()
    except (stripe.StripeError, HTTPException):
        plano = None
    return {
        "status": assinatura.status if assinatura else "inativa",
        "ativa": assinatura_ativa(assinatura),
        "periodo_atual_fim": assinatura.periodo_atual_fim if assinatura else None,
        "configurado": _stripe_configurado() and plano is not None,
        "plano": plano,
        "uso": _uso(db, usuario),
    }


@router.get("/status")
def status_assinatura(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
) -> dict:
    return _status(db, usuario)


def _listar_assinaturas(customer_id: str) -> list:
    resultado = []
    parametros = {"customer": customer_id, "status": "all", "limit": 100}
    while True:
        pagina = _dados(stripe.Subscription.list(**parametros))
        dados = pagina.get("data", [])
        resultado.extend(dados)
        if not pagina.get("has_more") or not dados:
            return resultado
        parametros["starting_after"] = dados[-1]["id"]


def _itens(objeto: dict) -> list:
    return (objeto.get("items") or {}).get("data") or []


def _preco_e_periodo(objeto: dict) -> tuple[str | None, datetime | None]:
    itens = _itens(objeto)
    item = next(
        (i for i in itens if (i.get("price") or {}).get("id") == os.environ.get("STRIPE_PRICE_ID")),
        None,
    )
    item = item or (itens[0] if itens else {})
    # Basil moveu o período para os itens; fallback para versões antigas.
    fim = item.get("current_period_end") or objeto.get("current_period_end")
    return (item.get("price") or {}).get("id"), (
        datetime.fromtimestamp(fim, tz=timezone.utc) if fim else None
    )


def _prioridade_assinatura(objeto: dict) -> tuple:
    preco, fim = _preco_e_periodo(objeto)
    preco_esperado = os.environ.get("STRIPE_PRICE_ID")
    concede_acesso = bool(
        objeto.get("id")
        and objeto.get("status") in _STATUS_ATIVOS
        and preco_esperado
        and preco == preco_esperado
        and fim
        and fim > _agora()
    )
    return (
        concede_acesso,
        objeto.get("status") not in _STATUS_TERMINAIS,
        bool(preco_esperado and preco == preco_esperado),
        objeto.get("created", 0),
        objeto.get("id", ""),
    )


def _aplicar_evento_assinatura(db: Session, objeto: dict) -> None:
    """Aplica somente objetos consultados na API Stripe, nunca snapshots atrasados."""
    assinatura = db.scalar(
        select(AssinaturaDB)
        .where(AssinaturaDB.stripe_customer_id == objeto.get("customer"))
        .execution_options(populate_existing=True)
    )
    if not assinatura:
        return
    assinatura.stripe_subscription_id = objeto.get("id")
    assinatura.status = objeto.get("status", "inativa")
    assinatura.preco_id, assinatura.periodo_atual_fim = _preco_e_periodo(objeto)


def _reconciliar(db: Session, assinatura: AssinaturaDB) -> list:
    assinaturas = _listar_assinaturas(assinatura.stripe_customer_id)
    if not assinaturas:
        assinatura.status = "inativa"
        assinatura.stripe_subscription_id = None
        assinatura.periodo_atual_fim = None
        assinatura.preco_id = None
        return []
    # Preserva acesso já pago diante de uma assinatura incompleta mais recente.
    # Data só desempata depois de status, preço e período válidos para acesso.
    # Sem nenhuma assinatura elegível, o estado continua sem conceder acesso.
    escolhida = max(assinaturas, key=_prioridade_assinatura)
    _aplicar_evento_assinatura(db, escolhida)
    db.flush()
    return assinaturas


@router.post("/sincronizar")
def sincronizar_assinatura(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
) -> dict:
    _exigir_stripe()
    limitar_tentativas(db, f"billing:sync:{usuario.id}", limite=20, janela=300)
    _bloquear_usuario(db, usuario.id)
    assinatura = db.get(AssinaturaDB, usuario.id, populate_existing=True)
    try:
        if assinatura:
            _reconciliar(db, assinatura)
        db.commit()
    except stripe.StripeError as erro:
        db.rollback()
        raise _falha_stripe(erro) from erro
    return _status(db, usuario)


def _url_retorno(variavel: str, sufixo: str = "") -> str:
    base = os.environ.get("TRIAGEM_PUBLIC_URL", "http://localhost:5173").rstrip("/")
    url = os.environ.get(variavel) or f"{base}/{sufixo}"
    try:
        partes = urlsplit(url)
        _ = partes.port
    except ValueError as erro:
        raise HTTPException(503, "A URL de retorno da cobrança está inválida.") from erro
    if (
        partes.scheme not in {"https", "http"}
        or not partes.hostname
        or partes.username
        or partes.password
    ):
        raise HTTPException(503, "A URL de retorno da cobrança está inválida.")
    if partes.scheme != "https" and partes.hostname not in {"localhost", "127.0.0.1"}:
        raise HTTPException(503, "A URL pública de cobrança precisa usar HTTPS.")
    return url


def _obter_ou_criar_assinatura(db: Session, usuario: Usuario) -> AssinaturaDB:
    assinatura = db.get(AssinaturaDB, usuario.id, populate_existing=True)
    if assinatura:
        return assinatura
    cliente = stripe.Customer.create(
        email=usuario.email,
        metadata={"usuario_id": usuario.id},
        idempotency_key=f"customer-{usuario.id}",
    )
    assinatura = AssinaturaDB(
        usuario_id=usuario.id, stripe_customer_id=cliente["id"], status="inativa"
    )
    db.add(assinatura)
    db.flush()
    return assinatura


def _listar_checkouts_abertos(customer_id: str) -> list:
    resultado = []
    parametros = {"customer": customer_id, "status": "open", "limit": 100}
    while True:
        pagina = _dados(stripe.checkout.Session.list(**parametros))
        dados = pagina.get("data", [])
        resultado.extend(dados)
        if not pagina.get("has_more") or not dados:
            return resultado
        parametros["starting_after"] = dados[-1]["id"]


def _checkout_compativel(checkout: dict) -> bool:
    # Consulta itens autoritativos: metadata ou o preço configurado hoje não
    # provam qual preço uma sessão já aberta efetivamente cobrará.
    itens = _dados(stripe.checkout.Session.list_line_items(checkout["id"], limit=100))
    linhas = itens.get("data", [])
    return bool(
        checkout.get("url")
        and not itens.get("has_more")
        and len(linhas) == 1
        and (linhas[0].get("price") or {}).get("id") == os.environ.get("STRIPE_PRICE_ID")
        and linhas[0].get("quantity") == 1
    )


@router.post("/checkout")
def criar_checkout(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
) -> dict:
    _exigir_stripe()
    limitar_tentativas(db, f"billing:checkout:{usuario.id}", limite=10, janela=300)
    if not _stripe_configurado():
        raise HTTPException(503, "O operador precisa configurar o preço e o webhook da cobrança.")
    _bloquear_usuario(db, usuario.id)
    try:
        _plano()
        assinatura = _obter_ou_criar_assinatura(db, usuario)
        assinaturas = _reconciliar(db, assinatura)
        if any(item.get("status") not in _STATUS_TERMINAIS for item in assinaturas):
            db.commit()
            raise HTTPException(
                409,
                "Já existe uma assinatura. Use Gerenciar assinatura para atualizar ou cancelar.",
            )
        controle = db.get(BillingControleDB, usuario.id)
        if not controle:
            controle = BillingControleDB(usuario_id=usuario.id)
            db.add(controle)
        # Recupera sessões mesmo se a resposta/commit anterior falhou.
        abertas = _listar_checkouts_abertos(assinatura.stripe_customer_id)
        if controle.checkout_session_id and not any(
            aberta["id"] == controle.checkout_session_id for aberta in abertas
        ):
            anterior = _dados(stripe.checkout.Session.retrieve(controle.checkout_session_id))
            if anterior.get("customer") != assinatura.stripe_customer_id:
                raise HTTPException(409, "A sessão de cobrança não corresponde a esta conta.")
            if anterior.get("status") == "open":
                abertas.append(anterior)
            assinatura_anterior = anterior.get("subscription")
            if anterior.get("status") == "complete" and not any(
                item.get("id") == assinatura_anterior for item in assinaturas
            ):
                db.commit()
                raise HTTPException(
                    409, "O pagamento está sendo confirmado. Aguarde e atualize a assinatura."
                )
        reutilizavel = None
        for aberta in abertas:
            if aberta.get("mode") != "subscription":
                continue
            if _checkout_compativel(aberta):
                reutilizavel = reutilizavel or aberta
                continue
            # Bloqueia o link antigo antes de abrir qualquer nova cobrança.
            # Se um pagamento vencer essa corrida, Stripe recusará a expiração;
            # propagamos a falha sem criar uma segunda assinatura.
            encerrada = _dados(
                stripe.checkout.Session.expire(
                    aberta["id"],
                    idempotency_key=f"expire-checkout-{aberta['id']}",
                )
            )
            if encerrada.get("status") != "expired":
                raise HTTPException(
                    409, "A cobrança anterior está sendo confirmada. Atualize a assinatura."
                )
            controle.checkout_session_id = encerrada["id"]
        if reutilizavel:
            controle.checkout_session_id = reutilizavel["id"]
            db.commit()
            return {"url": reutilizavel["url"]}
        material = f"{usuario.id}:{os.environ['STRIPE_PRICE_ID']}:{controle.checkout_session_id or 'inicial'}"
        checkout = _dados(
            stripe.checkout.Session.create(
                customer=assinatura.stripe_customer_id,
                mode="subscription",
                line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}],
                success_url=_url_retorno("STRIPE_SUCCESS_URL", "?sucesso=1"),
                cancel_url=_url_retorno("STRIPE_CANCEL_URL", "?cancelado=1"),
                client_reference_id=usuario.id,
                subscription_data={"metadata": {"usuario_id": usuario.id}},
                idempotency_key="checkout-" + hashlib.sha256(material.encode()).hexdigest(),
            )
        )
        controle.checkout_session_id = checkout["id"]
        db.commit()
        if checkout.get("status") != "open" or not checkout.get("url"):
            raise HTTPException(
                409, "A sessão anterior encerrou. Atualize a assinatura e tente novamente."
            )
        return {"url": checkout["url"]}
    except stripe.StripeError as erro:
        db.rollback()
        raise _falha_stripe(erro) from erro


@router.post("/portal")
def criar_portal(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)) -> dict:
    _exigir_stripe()
    limitar_tentativas(db, f"billing:portal:{usuario.id}", limite=10, janela=300)
    assinatura = db.get(AssinaturaDB, usuario.id)
    if not assinatura:
        raise HTTPException(404, "Nenhuma assinatura encontrada para este usuário.")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=assinatura.stripe_customer_id,
            return_url=_url_retorno("STRIPE_PORTAL_RETURN_URL"),
        )
        return {"url": portal["url"]}
    except stripe.StripeError as erro:
        raise _falha_stripe(erro) from erro


def encerrar_cobranca_para_exclusao(db: Session, usuario: Usuario) -> None:
    """Exclusão confirmada cancela assinaturas imediatamente; falha preserva a conta."""
    _bloquear_usuario(db, usuario.id)
    assinatura = db.get(AssinaturaDB, usuario.id, populate_existing=True)
    if not assinatura:
        return
    _exigir_stripe()
    try:
        # Customer.delete cancela todas as assinaturas; registros fiscais ficam no Stripe.
        stripe.Customer.delete(assinatura.stripe_customer_id)
        assinatura.status = "canceled"
        assinatura.periodo_atual_fim = _agora()
    except stripe.InvalidRequestError as erro:
        if erro.code != "resource_missing":
            raise _falha_stripe(erro) from erro
    except stripe.StripeError as erro:
        raise _falha_stripe(erro) from erro


def _processar_evento(db: Session, evento: dict) -> dict:
    tipo = evento.get("type")
    if tipo not in _EVENTOS:
        return {"recebido": True}
    evento_id = evento.get("id")
    if not evento_id or not isinstance(evento_id, str):
        raise HTTPException(400, "Evento Stripe sem identificador.")
    if db.get(EventoStripeDB, evento_id):
        return {"recebido": True, "duplicado": True}
    objeto = evento["data"]["object"]
    customer_id = objeto.get("id") if tipo == "customer.deleted" else objeto.get("customer")
    assinatura = db.scalar(
        select(AssinaturaDB).where(AssinaturaDB.stripe_customer_id == customer_id)
    )
    try:
        if assinatura:
            _bloquear_usuario(db, assinatura.usuario_id)
        # Identificador e efeito na MESMA transação; falha permite retry do Stripe.
        db.add(EventoStripeDB(evento_id=evento_id, tipo=tipo))
        db.flush()
        if assinatura:
            if tipo == "customer.deleted":
                assinatura.status = "canceled"
                assinatura.periodo_atual_fim = _agora()
            else:
                _reconciliar(db, assinatura)
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(EventoStripeDB, evento_id):
            return {"recebido": True, "duplicado": True}
        raise
    except stripe.StripeError as erro:
        db.rollback()
        raise _falha_stripe(erro) from erro
    return {"recebido": True}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(sessao)) -> dict:
    _exigir_stripe()
    segredo = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not segredo:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET não configurado.")
    payload = await request.body()
    if len(payload) > 1_000_000:
        raise HTTPException(413, "Webhook excede o tamanho permitido.")
    try:
        evento = _dados(
            stripe.Webhook.construct_event(
                payload, request.headers.get("stripe-signature", ""), segredo
            )
        )
    except (ValueError, stripe.SignatureVerificationError) as erro:
        raise HTTPException(400, "Assinatura de webhook inválida.") from erro
    modo_esperado = "_live_" in os.environ["STRIPE_SECRET_KEY"]
    if isinstance(evento.get("livemode"), bool) and evento["livemode"] != modo_esperado:
        raise HTTPException(400, "Modo do evento incompatível com a configuração de cobrança.")
    return await run_in_threadpool(_processar_evento, db, evento)
