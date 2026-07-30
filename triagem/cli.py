"""CLI com subcomandos: analisar (padrão), historico, status, cv.

Retrocompatível: `python triar.py vagas.json` equivale a `python triar.py analisar vagas.json`.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from filelock import FileLock, Timeout

from . import alvos_ats, ats, cache, dedup, historico
from .analisador import MODELO_PADRAO, MODELOS, analisar_vaga, criar_cliente
from .buscador import (
    _limpar_url,
    _normalizar,
    _redigir_segredos,
    _remoto_afirmado,
    _url_canonica,
    buscar_vagas,
    testar_fontes,
)
from .curriculo import carregar_cv_base, gerar_material
from .entrada import carregar_arquivo, carregar_vagas
from .exportar import exportar
from .relatorio import render_relatorio
from .schema import AnaliseVaga, VagaPontuada
from .scoring import parse_pesos, pontuar

SUBCOMANDOS = {"buscar", "analisar", "historico", "status", "cv", "limpar-cache", "sync-ats"}


def _inteiro_positivo(valor: str) -> int:
    try:
        numero = int(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError("deve ser um número inteiro") from e
    if numero < 1:
        raise argparse.ArgumentTypeError("deve ser maior que zero")
    return numero


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triar",
        description="Triagem de vagas personalizada (hard filters + scoring D1-D5 via Gemini).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    bu = sub.add_parser("buscar", help="Busca vagas atuais na web e executa a triagem")
    bu.add_argument(
        "pedido",
        nargs="?",
        default="Vagas de estágio ou Júnior em DevOps, DevSecOps ou C#/.NET, remotas no Brasil ou em Curitiba/Araucária",
        help="O tipo de vaga desejado",
    )
    bu.add_argument("--limite", type=_inteiro_positivo, default=10, metavar="N")
    bu.add_argument("--modelo", choices=MODELOS, default=MODELO_PADRAO)
    bu.add_argument("--paralelo", type=_inteiro_positivo, default=4, metavar="N")
    bu.add_argument("--saida", metavar="ARQ", help="Exporta o relatório (.md ou .csv)")
    bu.add_argument("--reanalisar", action="store_true")
    bu.add_argument("--sem-cache", action="store_true", dest="sem_cache",
                    help="Ignora o cache de busca e consulta as fontes do zero")
    bu.add_argument("--testar-fontes", action="store_true", dest="testar_fontes",
                    help="Só checa a saúde das fontes e fecha o circuito se elas responderem")
    bu.add_argument("--pesos", metavar="LISTA",
                    help="Pesos do score, ex.: d1=0.15,d2=0.30,d3=0.25,d4=0.15,d5=0.15")

    an = sub.add_parser("analisar", help="Analisa um lote de vagas (JSON ou texto)")
    an.add_argument("arquivo", nargs="?", help="Arquivo .json ou .txt com as vagas")
    an.add_argument("--stdin", action="store_true", help="Lê as vagas da entrada padrão")
    an.add_argument("--modelo", choices=MODELOS, default=MODELO_PADRAO,
                    help=f"Modelo Gemini (padrão: {MODELO_PADRAO})")
    an.add_argument("--paralelo", type=_inteiro_positivo, default=4, metavar="N",
                    help="Análises simultâneas (padrão: 4)")
    an.add_argument("--saida", metavar="ARQ", help="Exporta o relatório (.md ou .csv)")
    an.add_argument("--reanalisar", action="store_true",
                    help="Re-analisa vagas já presentes no histórico")
    an.add_argument("--pesos", metavar="LISTA",
                    help="Pesos do score, ex.: d1=0.15,d2=0.30,d3=0.25,d4=0.15,d5=0.15")

    hi = sub.add_parser("historico", help="Lista as vagas já analisadas")
    hi.add_argument("--status", choices=historico.STATUS_VALIDOS, help="Filtra por status")

    st = sub.add_parser("status", help="Atualiza o status de uma vaga (acompanhamento)")
    st.add_argument("id", help="ID da vaga (ou prefixo único)")
    st.add_argument("novo_status", choices=[s for s in historico.STATUS_VALIDOS if s != "descartada"])

    lc = sub.add_parser("limpar-cache", help="Remove entradas antigas do cache de busca")
    lc.add_argument("--tudo", action="store_true",
                    help="Apaga todas as entradas e reseta os circuitos das fontes")

    sa = sub.add_parser("sync-ats", help="Sincroniza ATS descobertos e fecha vagas removidas")
    sa.add_argument("--paralelo", type=_inteiro_positivo, default=4, metavar="N")

    cv = sub.add_parser("cv", help="Gera bullets de CV + mensagem de candidatura para uma vaga")
    cv.add_argument("id", help="ID da vaga no histórico (ou prefixo único)")
    cv.add_argument("--modelo", choices=MODELOS, default=MODELO_PADRAO)
    cv.add_argument("--saida", metavar="ARQ", help="Salva o material em arquivo .md")

    return parser


def main() -> int:
    if (
        sys.stdout.encoding
        and sys.stdout.encoding.lower() != "utf-8"
        and hasattr(sys.stdout, "reconfigure")
    ):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = _montar_parser()
    argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 1
    # Retrocompatibilidade: sem subcomando explícito, assume "analisar"
    if argv[0] not in SUBCOMANDOS and argv[0] not in ("-h", "--help"):
        argv = ["analisar"] + argv
    args = parser.parse_args(argv)

    load_dotenv()
    historico.aplicar_config_do_ambiente()
    cache.aplicar_config_do_ambiente()
    alvos_ats.aplicar_config_do_ambiente()

    # Serializa comandos do CLI. Sem lock, duas execuções fazem read-modify-write
    # sobre JSONs diferentes e a última apaga o estado gravado pela primeira.
    historico.ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(historico.ARQUIVO.with_suffix(".lock")), timeout=2)
    try:
        with lock:
            if args.comando == "buscar":
                return _cmd_buscar(args)
            if args.comando == "analisar":
                return _cmd_analisar(args)
            if args.comando == "historico":
                return _cmd_historico(args)
            if args.comando == "status":
                return _cmd_status(args)
            if args.comando == "cv":
                return _cmd_cv(args)
            if args.comando == "limpar-cache":
                return _cmd_limpar_cache(args)
            if args.comando == "sync-ats":
                return _cmd_sync_ats(args)
    except Timeout:
        print("Outra execução do triar está atualizando o estado. Tente novamente em instantes.")
        return 1
    return 1


def _cmd_limpar_cache(args) -> int:
    estado = cache.carregar()  # já poda entradas acima de DIAS_RETENCAO
    if args.tudo:
        removidas = cache.esvaziar(estado)
        detalhe = "todas as entradas e os circuitos das fontes"
    else:
        removidas = cache.podar(estado)
        detalhe = f"entradas com mais de {cache.DIAS_RETENCAO} dias"
    try:
        cache.salvar(estado)
    except OSError as e:
        print(f"Erro ao gravar o cache: {e}")
        return 1
    print(f"Cache limpo ({detalhe}): {removidas} entrada(s) removida(s).")
    print(f"Restam {len(estado['entradas'])} entrada(s) em {cache.ARQUIVO}.")
    return 0


def _cmd_sync_ats(args) -> int:
    """Delta Sync: API oficial decide o que ainda está aberto."""
    try:
        estado = alvos_ats.carregar()
        hist = historico.carregar()
    except ValueError as e:
        print(f"Erro: {e}")
        return 1
    alvos = alvos_ats.ativos(estado)
    if not alvos:
        print("Nenhum ATS ativo foi descoberto ainda. Rode 'triar buscar' para alimentar o radar.")
        return 0

    resultados = ats.sincronizar_alvos(alvos, args.paralelo)
    fechadas = 0
    inativos = 0
    falhas = 0
    for resultado in resultados:
        alvos_ats.aplicar_resultado(estado, resultado)
        if resultado.estado == "ativo":
            fechadas += len(
                historico.marcar_fechadas_por_ats(
                    hist, resultado.provedor, resultado.token, set(resultado.job_ids)
                )
            )
        elif resultado.estado == "inativo":
            inativos += 1
        else:
            falhas += 1
            print(f"  {resultado.provedor}:{resultado.token} — falha: {resultado.erro}")
    try:
        alvos_ats.salvar(estado)
        historico.salvar(hist)
    except OSError as e:
        print(f"Erro ao gravar estado do Delta Sync: {e}")
        return 1
    print(
        f"ATS sincronizados: {len(resultados)} | inativados: {inativos} | "
        f"vagas fechadas: {fechadas} | falhas transitórias: {falhas}"
    )
    return 0


def _exigir_api_key() -> bool:
    if not os.environ.get("GEMINI_API_KEY"):
        print("Erro: defina GEMINI_API_KEY no ambiente ou no arquivo .env (veja .env.example).")
        return False
    return True


# ---------------------------------------------------------------- analisar

def _cmd_buscar(args) -> int:
    if not _exigir_api_key():
        return 1
    try:
        client = criar_cliente()
    except Exception as e:
        print(f"Erro ao iniciar cliente da API: {_redigir_segredos(str(e))}")
        return 1

    if args.testar_fontes:
        print("Checando a saúde das fontes de busca...\n")
        estado = testar_fontes(client, log=print)
        vivas = sum(1 for ok in estado.values() if ok)
        print(f"\n{vivas} de {len(estado)} fonte(s) responderam.")
        return 0 if vivas else 1

    try:
        cv_base = carregar_cv_base()
    except (FileNotFoundError, ValueError) as e:
        # Erro de CV não é erro de busca: mensagem própria para não confundir.
        print(f"Erro: {e}")
        return 1

    try:
        print(f"Buscando até {args.limite} vaga(s) atuais na web para: {args.pedido}\n")
        vagas = buscar_vagas(
            client, cv_base, args.pedido, args.limite, args.modelo,
            log=print, usar_cache=not args.sem_cache,
        )
    except Exception as e:
        print(f"Erro na busca de vagas ({type(e).__name__}): {_redigir_segredos(str(e))}")
        return 1
    if not vagas:
        print("\nNenhuma vaga atual com link verificável foi encontrada.")
        return 0

    print(f"\nEncontradas {len(vagas)} vaga(s) com link. Iniciando triagem...\n")
    textos = [json.dumps(vaga.model_dump(), ensure_ascii=False, indent=2) for vaga in vagas]
    # O texto vem de um modelo e muda de execução para execução. A URL final (após
    # seguir os redirects do agregador) é a chave estável — e é a mesma quando a
    # vaga chega pela Jooble e pela Adzuna.
    chaves = [_url_canonica(vaga.chave_dedup()) for vaga in vagas]
    # A URL canônica cobre só a Camada A. As camadas B e C precisam de empresa,
    # título e confiança para reconhecer a mesma vaga vinda de outro portal.
    registros = [
        dedup.Registro(
            ident="",
            url=chaves[indice],
            empresa=vaga.empresa,
            titulo=vaga.titulo,
            confianca=vaga.confianca_empresa,
            idade_dias=dedup.idade_da_publicacao(vaga.publicada_em),
            localidade=vaga.localizacao,
            ats_provedor=vaga.ats_provedor,
            ats_token=vaga.ats_token,
            ats_job_id=vaga.ats_job_id,
        )
        for indice, vaga in enumerate(vagas)
    ]
    return _cmd_analisar(args, textos, chaves, registros)


def _impor_campos_autoritativos(analise: AnaliseVaga, texto_origem: str) -> AnaliseVaga:
    """Devolve os campos estruturados ao dono deles, depois que o modelo respondeu.

    O prompt manda copiar `empresa`, `regime` e `localizacao` da entrada quando eles
    chegam resolvidos — mas prompt é pedido, não garantia. Medido em 2026-07-27: a
    vaga da RedFox tinha `localizacao: "Curitiba (Remoto)"` extraída do JSON-LD do
    empregador e o modelo devolveu `"Brasil"` na análise, que é o valor que o
    relatório mostra. A extração ganhava e a exibição perdia.

    Só sobrescreve o que tem respaldo: campo vazio na origem deixa a análise como
    está, porque aqui não é lugar de inventar tampouco.
    """
    try:
        origem = json.loads(texto_origem or "{}")
    except ValueError:
        return analise
    if not isinstance(origem, dict):
        return analise

    mudancas: dict = {}
    # VagaEncontrada.model_dump() sempre contém estas chaves. A presença da
    # chave, mesmo vazia, identifica o caminho estruturado de `buscar`.
    if "empresa" in origem:
        empresa = str(origem.get("empresa") or "").strip()
        mudancas["empresa"] = (
            empresa
            if empresa and origem.get("confianca_empresa") != "baixa"
            else "Desconhecida"
        )

    if "localizacao" in origem:
        local = str(origem.get("localizacao") or "").strip()
        mudancas["localizacao"] = local
        # A fonte estruturada é dona da modalidade. Cidade sem modalidade não
        # autoriza o modelo a completar a lacuna como "remoto": o contrato do
        # schema prevê "indefinido", que scoring.py penaliza deterministicamente.
        if _remoto_afirmado(_normalizar(local)):
            mudancas["regime"] = "remoto"
        elif "hibrido" in _normalizar(local).split():
            mudancas["regime"] = "hibrido"
        elif "presencial" in _normalizar(local).split():
            mudancas["regime"] = "presencial"
        else:
            mudancas["regime"] = "indefinido"

    link = str(origem.get("link_final") or origem.get("link") or "").strip()
    if link:
        mudancas["link"] = _limpar_url(link)
    if origem.get("origem"):
        mudancas["origem"] = str(origem["origem"])
    if "publicada_em" in origem:
        mudancas["publicada_em"] = str(origem.get("publicada_em") or "")

    return analise.model_copy(update=mudancas) if mudancas else analise


def _cmd_analisar(args, textos=None, chaves=None, registros=None) -> int:
    try:
        pesos = parse_pesos(args.pesos) if getattr(args, "pesos", None) else None
    except ValueError as e:
        print(f"Erro em --pesos: {e}")
        return 1
    if pesos:
        print("Pesos personalizados: " + ", ".join(f"{k}={v:g}" for k, v in pesos.items()) + "\n")

    if textos is None:
        if args.stdin and args.arquivo:
            print("Use apenas uma fonte de entrada: informe um arquivo ou --stdin.")
            return 1
        try:
            if args.stdin:
                textos = carregar_vagas(sys.stdin.read(), log=print)
            elif args.arquivo:
                textos = carregar_arquivo(args.arquivo, log=print)
            else:
                print("Informe um arquivo ou use --stdin.")
                return 1
        except (ValueError, OSError) as e:
            print(f"Erro no input: {e}")
            return 1

    try:
        hist = historico.carregar()
    except ValueError as e:
        print(f"Erro: {e}")
        return 1
    resultados: List[VagaPontuada] = []
    pendentes: List[Tuple[str, str]] = []  # (id, texto)
    vistos = set()
    historico_recalculado = False

    for indice, texto in enumerate(textos):
        vid = historico.gerar_id(chaves[indice] if chaves else texto)
        # Cascata de dedup: a mesma vaga em outro portal tem outra URL e, por
        # consequência, outro id. Sem isto ela é reanalisada e paga de novo.
        if registros:
            registros[indice].id = vid
            existente = dedup.resolver_id(hist, registros[indice])
            if existente and existente != vid:
                print(f"  [{existente}] mesma vaga já vista em outra fonte "
                      f"({registros[indice].url}) — reaproveitando.")
                historico.registrar_alias(hist, existente, registros[indice].url)
                vid = existente
        if vid in vistos:
            # Antes sumia em silêncio: o relatório dizia "TOTAL ANALISADAS: 1"
            # para um arquivo de 3 itens, sem explicar o que houve com os outros.
            print(f"  [{vid}] duplicada no input — pulando.")
            continue
        vistos.add(vid)
        entrada = hist.get(vid)
        if entrada and entrada.get("analise") and not args.reanalisar:
            analise = AnaliseVaga.model_validate(entrada["analise"])
            analise = _impor_campos_autoritativos(analise, entrada.get("texto", ""))
            # Regras determinísticas evoluem. Nunca reutilize score materializado
            # por uma versão anterior do pipeline.
            reaproveitado = pontuar(analise, vid, pesos)
            resultados.append(reaproveitado)
            if (
                entrada.get("pipeline_version") != historico.PIPELINE_VERSION
                or entrada.get("score_final") != reaproveitado.score_final
                or entrada.get("analise") != reaproveitado.analise.model_dump()
            ):
                historico.registrar(hist, reaproveitado, entrada.get("texto", ""))
                historico_recalculado = True
            print(f"  [{vid}] {analise.empresa or '?'} - {analise.titulo_normalizado}: "
                  f"já no histórico (status: {entrada['status']}) — pulando. Use --reanalisar para refazer.")
        else:
            pendentes.append((vid, texto))

    falhas: List[Tuple[str, str, Exception]] = []
    if pendentes:
        if not _exigir_api_key():
            return 1
        try:
            client = criar_cliente()
        except Exception as e:
            print(f"Erro ao iniciar cliente da API: {_redigir_segredos(str(e))}")
            return 1
        print(f"Analisando {len(pendentes)} vaga(s) nova(s) com {MODELOS[args.modelo]}"
              f" ({args.paralelo} em paralelo)...\n")

        analises, falhas = _analisar_paralelo(
            client, pendentes, args.modelo, args.paralelo, pesos
        )

        # Persiste o que já terminou antes de esperar a segunda passagem. Se o
        # processo for interrompido, chamadas pagas não são perdidas.
        for vid, texto in pendentes:
            if vid not in analises:
                continue
            vaga = pontuar(_impor_campos_autoritativos(analises[vid], texto), vid, pesos)
            historico.registrar(hist, vaga, texto)
            resultados.append(vaga)
        if analises:
            try:
                historico.salvar(hist)
            except OSError as e:
                print(f"Erro ao salvar checkpoint do histórico: {e}")
                return 1

        # Segunda chance sequencial para falhas individuais.
        if falhas:
            print(f"\n  Re-tentando {len(falhas)} vaga(s) que falharam...")
            restantes = []
            for vid, texto, _ in falhas:
                try:
                    analise = analisar_vaga(client, texto, args.modelo, tentativas=1)
                    vaga = pontuar(_impor_campos_autoritativos(analise, texto), vid, pesos)
                    historico.registrar(hist, vaga, texto)
                    resultados.append(vaga)
                    print(f"  [{vid}] OK na segunda tentativa")
                except Exception as e:  # noqa: BLE001 — registramos e seguimos
                    restantes.append((vid, texto, e))
            falhas = restantes

        try:
            historico.salvar(hist)
        except OSError as e:
            print(f"Erro ao salvar histórico: {e}")
            return 1

    elif historico_recalculado:
        try:
            historico.salvar(hist)
        except OSError as e:
            print(f"Erro ao atualizar histórico para a versão atual: {e}")
            return 1

    if not resultados:
        print("Nenhuma vaga analisada.")
        return 1

    print()
    print(render_relatorio(resultados))

    if args.saida:
        try:
            exportar(resultados, args.saida)
            print(f"\nRelatório exportado para {args.saida}")
        except (ValueError, OSError) as e:
            print(f"\nErro no export: {e}")
            return 1

    if falhas:
        print(f"\nAtenção: {len(falhas)} vaga(s) não analisadas mesmo após retry:")
        for vid, _, e in falhas:
            print(f"  [{vid}] {type(e).__name__}: {_redigir_segredos(str(e))}")
        print("Rode novamente para reprocessar (as já analisadas serão puladas pelo histórico).")
    return 2 if falhas else 0


def _analisar_paralelo(client, pendentes, modelo, paralelo, pesos=None):
    analises: Dict[str, AnaliseVaga] = {}
    falhas: List[Tuple[str, str, Exception]] = []
    total = len(pendentes)
    feitas = 0
    with ThreadPoolExecutor(max_workers=max(1, paralelo)) as executor:
        futuros = {
            executor.submit(analisar_vaga, client, texto, modelo): (vid, texto)
            for vid, texto in pendentes
        }
        for futuro in as_completed(futuros):
            vid, texto = futuros[futuro]
            feitas += 1
            try:
                analise = futuro.result()
                analises[vid] = analise
                vaga = pontuar(analise, vid, pesos)
                rotulo = "DESCARTADA" if vaga.score_final is None else f"{vaga.score_final:.0f}/100"
                print(f"  [{feitas}/{total}] [{vid}] {analise.empresa or '?'} - "
                      f"{analise.titulo_normalizado}: {rotulo}")
            except Exception as e:  # noqa: BLE001 — coletamos para retry
                falhas.append((vid, texto, e))
                print(f"  [{feitas}/{total}] [{vid}] falha ({type(e).__name__})")
    return analises, falhas


# ---------------------------------------------------------------- historico

def _cmd_historico(args) -> int:
    try:
        hist = historico.carregar()
    except ValueError as e:
        print(f"Erro: {e}")
        return 1
    entradas = list(hist.items())
    if args.status:
        entradas = [(k, v) for k, v in entradas if v.get("status") == args.status]
    if not entradas:
        print("Histórico vazio." if not args.status else f"Nenhuma vaga com status '{args.status}'.")
        return 0

    # Ordena por score desc; descartadas (score None) por último
    entradas.sort(key=lambda kv: (kv[1].get("score_final") is None, -(kv[1].get("score_final") or 0)))

    print(f"{'ID':<12}{'SCORE':<8}{'STATUS':<12}{'ANALISADA EM':<20}EMPRESA — TÍTULO")
    print("-" * 100)
    for vid, e in entradas:
        analise = e.get("analise", {})
        score = f"{e['score_final']:.0f}" if e.get("score_final") is not None else "—"
        empresa = analise.get("empresa") or "?"
        titulo = analise.get("titulo_normalizado") or "?"
        rotulo = f"{empresa} — {titulo}"
        if len(rotulo) > 55:
            rotulo = rotulo[:52] + "..."
        print(f"{vid:<12}{score:<8}{e.get('status', '?'):<12}{e.get('analisado_em', '?'):<20}{rotulo}")
    print(f"\nTotal: {len(entradas)} | Atualize com: python triar.py status <id> <novo_status>")
    return 0


def _cmd_status(args) -> int:
    try:
        hist = historico.carregar()
    except ValueError as e:
        print(f"Erro: {e}")
        return 1
    try:
        vid = historico.atualizar_status(hist, args.id, args.novo_status)
    except KeyError as e:
        print(f"Erro: {e.args[0]}")
        return 1
    try:
        historico.salvar(hist)
    except OSError as e:
        print(f"Erro ao salvar histórico: {e}")
        return 1
    analise = hist[vid].get("analise", {})
    print(f"[{vid}] {analise.get('empresa') or '?'} - {analise.get('titulo_normalizado') or '?'} "
          f"-> status: {args.novo_status}")
    return 0


# ---------------------------------------------------------------- cv

def _cmd_cv(args) -> int:
    try:
        hist = historico.carregar()
    except ValueError as e:
        print(f"Erro: {e}")
        return 1
    try:
        vid = historico.buscar(hist, args.id)
    except KeyError as e:
        print(f"Erro: {e.args[0]}")
        print("Dica: rode 'python triar.py historico' para ver os IDs disponíveis.")
        return 1

    entrada = hist[vid]
    if entrada.get("status") == "descartada":
        print(f"Aviso: a vaga [{vid}] foi descartada no hard filter "
              f"({entrada['analise'].get('motivo_descarte') or 'sem motivo'}). Gerando mesmo assim.\n")

    try:
        cv_base = carregar_cv_base()
    except (FileNotFoundError, ValueError) as e:
        # ValueError = marcador PRIVADO malformado; abortar é intencional.
        print(f"Erro: {e}")
        return 1
    if "[preencha" in cv_base.lower():
        print("Aviso: perfil/cv_base.md ainda tem marcadores de preenchimento pendentes. "
              "O material sai melhor com o CV base completo.\n")

    if not _exigir_api_key():
        return 1
    try:
        client = criar_cliente()
    except Exception as e:
        print(f"Erro ao iniciar cliente da API: {_redigir_segredos(str(e))}")
        return 1
    analise = entrada.get("analise", {})
    print(f"Gerando material para [{vid}] {analise.get('empresa') or '?'} - "
          f"{analise.get('titulo_normalizado') or '?'} com {MODELOS[args.modelo]}...\n")

    try:
        material = gerar_material(client, cv_base, entrada.get("texto", ""), analise, args.modelo)
    except Exception as e:
        print(f"Erro ao gerar material pela API ({type(e).__name__}): {_redigir_segredos(str(e))}")
        return 1
    print(material)

    if args.saida:
        from pathlib import Path
        destino = Path(args.saida)
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(material, encoding="utf-8")
        except OSError as e:
            print(f"\nErro ao salvar material: {e}")
            return 1
        print(f"\nMaterial salvo em {args.saida}")
    return 0
