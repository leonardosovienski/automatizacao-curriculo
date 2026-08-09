"""Worker serial do MVP: busca, analisa e persiste vagas por usuário."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from triagem import credenciais, perfil_usuario
from triagem.analisador import MODELO_PADRAO, analisar_vaga, criar_cliente, system_prompt
from triagem.buscador import _redigir_segredos, _url_canonica, buscar_vagas
from triagem.cli import _impor_campos_autoritativos
from triagem.historico import gerar_id
from triagem.scoring import pontuar

from .database import BuscaDB, PerfilDB, SessionLocal, VagaDB

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="busca-saas")


def _atualizar(busca_id: str, **campos) -> None:
    with SessionLocal() as db:
        busca = db.get(BuscaDB, busca_id)
        if not busca:
            return
        for nome, valor in campos.items():
            setattr(busca, nome, valor)
        db.commit()


def executar_busca(busca_id: str) -> None:
    perfil_anterior = perfil_usuario.atual()
    try:
        with SessionLocal() as db:
            busca = db.get(BuscaDB, busca_id)
            if not busca:
                return
            registro_perfil = db.get(PerfilDB, busca.usuario_id)
            if not registro_perfil or not registro_perfil.cv_base.strip():
                raise ValueError("Complete o perfil e o currículo antes de buscar vagas.")
            perfil = perfil_usuario.PerfilUsuario.model_validate(registro_perfil.dados)
            usuario_id, pedido, limite = busca.usuario_id, busca.pedido, busca.limite
            cv_base = registro_perfil.cv_base

        perfil_usuario._ATIVO = perfil
        system_prompt.cache_clear()
        credenciais.carregar_no_ambiente()
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("O operador ainda não configurou a integração Gemini.")

        _atualizar(busca_id, estado="processando", progresso=5, mensagem="Consultando fontes de vagas.")
        cliente = criar_cliente()
        vagas = buscar_vagas(
            cliente, cv_base, pedido, limite, MODELO_PADRAO,
            usar_cache=True,
        )
        if not vagas:
            _atualizar(
                busca_id, estado="concluida", progresso=100, encontradas=0,
                mensagem="Nenhuma vaga compatível foi encontrada.",
                concluida_em=datetime.now(timezone.utc),
            )
            return

        total = len(vagas)
        persistidas = 0
        for indice, vaga in enumerate(vagas, start=1):
            progresso = 15 + int((indice - 1) / total * 80)
            _atualizar(
                busca_id, progresso=progresso,
                mensagem=f"Analisando vaga {indice} de {total}.",
            )
            texto = json.dumps(vaga.model_dump(), ensure_ascii=False, indent=2)
            analise = analisar_vaga(cliente, texto, MODELO_PADRAO)
            analise = _impor_campos_autoritativos(analise, texto)
            chave = _url_canonica(vaga.chave_dedup()) or texto
            vaga_id = gerar_id(chave)
            pontuada = pontuar(analise, vaga_id, perfil.pesos)
            with SessionLocal() as db:
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

        _atualizar(
            busca_id, estado="concluida", progresso=100, encontradas=persistidas,
            mensagem=f"Busca concluída: {persistidas} vaga(s) processada(s).",
            concluida_em=datetime.now(timezone.utc),
        )
    except Exception as e:  # noqa: BLE001 - fronteira do worker registra falha controlada
        mensagem = _redigir_segredos(str(e))
        _atualizar(
            busca_id, estado="falhou", progresso=100, erro=mensagem,
            mensagem="A busca não pôde ser concluída.",
            concluida_em=datetime.now(timezone.utc),
        )
    finally:
        perfil_usuario._ATIVO = perfil_anterior
        system_prompt.cache_clear()


def agendar(busca_id: str) -> None:
    EXECUTOR.submit(executar_busca, busca_id)
