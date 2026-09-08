"""Busca isolada por processo com checkpoints e proteção dos dados pessoais."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from triagem import alvos_ats, cache, credenciais, perfil_usuario, replay
from triagem.analisador import MODELO_PADRAO, analisar_vaga, criar_cliente, system_prompt
from triagem.buscador import _url_canonica, buscar_vagas
from triagem.cli import _impor_campos_autoritativos
from triagem.curriculo import gerar_material, preparar_cv_para_ia
from triagem.historico import gerar_id
from triagem.scoring import pontuar

from .database import BuscaDB, PerfilDB, SessionLocal, Usuario, VagaDB
from .queue import atualizar, reservar_proxima

logger = logging.getLogger(__name__)


def _atualizar(busca_id: str, token: str, **campos) -> None:
    if not atualizar(busca_id, token, **campos):
        raise RuntimeError("Busca cancelada ou removida.")


def executar_busca(busca_id: str, token: str | None = None) -> None:
    if token is None:
        reserva = reservar_proxima(busca_id)
        if not reserva:
            return
        _, token = reserva
    perfil_anterior = perfil_usuario.atual()
    cache_anterior, replay_anterior = cache.ARQUIVO, replay.ATIVO
    ats_anterior = alvos_ats.ARQUIVO
    temporario = tempfile.TemporaryDirectory(prefix="triagem-busca-")
    cache.ARQUIVO = Path(temporario.name) / "cache.json"
    alvos_ats.ARQUIVO = Path(temporario.name) / "alvos.json"
    replay.ATIVO = False
    cliente = None
    try:
        with SessionLocal() as db:
            busca = db.get(BuscaDB, busca_id)
            if not busca or busca.worker_token != token or busca.estado != "processando":
                return
            usuario = db.get(Usuario, busca.usuario_id)
            if not usuario or not usuario.ativo:
                raise ValueError("Conta indisponível.")
            registro_perfil = db.get(PerfilDB, busca.usuario_id)
            if not registro_perfil or not registro_perfil.cv_base.strip():
                raise ValueError("Complete o perfil e o currículo antes de buscar vagas.")
            perfil = perfil_usuario.PerfilUsuario.model_validate(registro_perfil.dados)
            if not perfil.consentimento_ia or not perfil.onboarding_concluido:
                raise ValueError("Consentimento de IA ou perfil incompleto.")
            usuario_id, pedido, limite = busca.usuario_id, busca.pedido, busca.limite
            tipo, vaga_alvo_id = busca.tipo, busca.vaga_alvo_id
            cv_base = preparar_cv_para_ia(registro_perfil.cv_base)
            if not cv_base.strip():
                raise ValueError("Currículo sem conteúdo público para análise.")

        perfil_usuario._ATIVO = perfil
        system_prompt.cache_clear()
        credenciais.carregar_no_ambiente()
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("O operador ainda não configurou a integração Gemini.")

        _atualizar(busca_id, token, progresso=5, mensagem="Consultando fontes de vagas.")
        cliente = criar_cliente()
        if tipo == "material":
            with SessionLocal() as db:
                vaga_db = db.scalar(select(VagaDB).where(
                    VagaDB.usuario_id == usuario_id, VagaDB.vaga_id == vaga_alvo_id,
                ))
                if not vaga_db:
                    raise ValueError("Vaga indisponível.")
                texto_vaga, dados_analise = vaga_db.texto, vaga_db.analise
            _atualizar(busca_id, token, progresso=20, mensagem="Preparando material com evidências do seu currículo.")
            material = gerar_material(cliente, cv_base, texto_vaga, dados_analise)
            _atualizar(busca_id, token, estado="concluida", progresso=100, resultado=material,
                       mensagem="Material pronto. Revise antes de enviar sua candidatura.",
                       concluida_em=datetime.now(timezone.utc))
            return
        vagas = buscar_vagas(
            cliente, cv_base, pedido, limite, MODELO_PADRAO,
            usar_cache=True,
        )[:limite]
        if not vagas:
            _atualizar(
                busca_id, token, estado="concluida", progresso=100, encontradas=0,
                mensagem="Nenhuma vaga compatível foi encontrada.",
                concluida_em=datetime.now(timezone.utc),
            )
            return

        total = len(vagas)
        persistidas = 0
        for indice, vaga in enumerate(vagas, start=1):
            progresso = 15 + int((indice - 1) / total * 80)
            _atualizar(
                busca_id, token, progresso=progresso,
                mensagem=f"Analisando vaga {indice} de {total}.",
            )
            texto = json.dumps(vaga.model_dump(), ensure_ascii=False, indent=2)
            analise = analisar_vaga(cliente, texto, MODELO_PADRAO, cv_base=cv_base)
            analise = _impor_campos_autoritativos(analise, texto)
            chave = _url_canonica(vaga.chave_dedup()) or texto
            vaga_id = gerar_id(chave)
            pontuada = pontuar(analise, vaga_id, perfil.pesos)
            with SessionLocal() as db:
                # Lock impede exclusão/revogação concorrente entre a checagem e a gravação.
                db.execute(update(Usuario).where(Usuario.id == usuario_id).values(id=Usuario.id))
                usuario = db.scalar(select(Usuario).where(Usuario.id == usuario_id).with_for_update())
                busca = db.get(BuscaDB, busca_id)
                if (not usuario or not usuario.ativo or not busca
                        or busca.estado != "processando" or busca.worker_token != token
                        or not busca.expira_em
                        or busca.expira_em.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)):
                    return
                existente = db.scalar(select(VagaDB).where(
                    VagaDB.usuario_id == usuario_id, VagaDB.vaga_id == vaga_id
                ))
                if existente:
                    existente.score_final = pontuada.score_final
                    existente.analise = pontuada.analise.model_dump()
                    existente.texto = texto
                    existente.analisado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
                else:
                    db.add(VagaDB(
                        usuario_id=usuario_id, vaga_id=vaga_id,
                        status="descartada" if pontuada.score_final is None else "novo",
                        score_final=pontuada.score_final,
                        analisado_em=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        texto=texto, analise=pontuada.analise.model_dump(), aliases=[],
                    ))
                db.commit()
            persistidas += 1
            _atualizar(busca_id, token, encontradas=persistidas)

        _atualizar(
            busca_id, token, estado="concluida", progresso=100, encontradas=persistidas,
            mensagem=f"Busca concluída: {persistidas} vaga(s) processada(s).",
            concluida_em=datetime.now(timezone.utc),
        )
    except Exception as e:  # noqa: BLE001 - fronteira do worker registra falha controlada
        # Mensagens de provedores podem conter prompts/CV: registrar apenas classe e ID.
        logger.error("Busca %s falhou: %s", busca_id, type(e).__name__)
        atualizar(
            busca_id, token, estado="falhou", progresso=100,
            erro="Não foi possível concluir a busca. Confira seu perfil e tente novamente mais tarde.",
            mensagem="A busca não pôde ser concluída.",
            concluida_em=datetime.now(timezone.utc),
        )
    finally:
        perfil_usuario._ATIVO = perfil_anterior
        system_prompt.cache_clear()
        cache.ARQUIVO, replay.ATIVO = cache_anterior, replay_anterior
        alvos_ats.ARQUIVO = ats_anterior
        if cliente and hasattr(cliente, "close"):
            cliente.close()
        temporario.cleanup()


def agendar(busca_id: str) -> None:
    """A busca já está no banco; o sinal apenas reduz a latência do polling local."""
    from .worker import ACORDAR
    ACORDAR.set()
