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

from . import historico
from .analisador import MODELO_PADRAO, MODELOS, analisar_vaga, criar_cliente
from .buscador import buscar_vagas
from .curriculo import carregar_cv_base, gerar_material
from .entrada import carregar_arquivo, carregar_vagas
from .exportar import exportar
from .relatorio import render_relatorio
from .schema import AnaliseVaga, VagaPontuada
from .scoring import pontuar

SUBCOMANDOS = {"buscar", "analisar", "historico", "status", "cv"}


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

    hi = sub.add_parser("historico", help="Lista as vagas já analisadas")
    hi.add_argument("--status", choices=historico.STATUS_VALIDOS, help="Filtra por status")

    st = sub.add_parser("status", help="Atualiza o status de uma vaga (acompanhamento)")
    st.add_argument("id", help="ID da vaga (ou prefixo único)")
    st.add_argument("novo_status", choices=[s for s in historico.STATUS_VALIDOS if s != "descartada"])

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
    return 1


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
        cv_base = carregar_cv_base()
        client = criar_cliente()
        print(f"Buscando até {args.limite} vaga(s) atuais na web para: {args.pedido}\n")
        vagas = buscar_vagas(client, cv_base, args.pedido, args.limite, args.modelo)
    except Exception as e:
        print(f"Erro na busca de vagas ({type(e).__name__}): {e}")
        return 1
    if not vagas:
        print("Nenhuma vaga atual com link verificável foi encontrada.")
        return 0

    print(f"Encontradas {len(vagas)} vaga(s) com link. Iniciando triagem...\n")
    textos = [json.dumps(vaga.model_dump(), ensure_ascii=False, indent=2) for vaga in vagas]
    return _cmd_analisar(args, textos)


def _cmd_analisar(args, textos=None) -> int:
    if textos is None:
        if args.stdin and args.arquivo:
            print("Use apenas uma fonte de entrada: informe um arquivo ou --stdin.")
            return 1
        try:
            if args.stdin:
                textos = carregar_vagas(sys.stdin.read())
            elif args.arquivo:
                textos = carregar_arquivo(args.arquivo)
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

    for texto in textos:
        vid = historico.gerar_id(texto)
        if vid in vistos:
            continue  # vaga duplicada dentro do próprio input
        vistos.add(vid)
        entrada = hist.get(vid)
        if entrada and entrada.get("analise") and not args.reanalisar:
            analise = AnaliseVaga.model_validate(entrada["analise"])
            resultados.append(VagaPontuada(id=vid, analise=analise, score_final=entrada.get("score_final")))
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
            print(f"Erro ao iniciar cliente da API: {e}")
            return 1
        print(f"Analisando {len(pendentes)} vaga(s) nova(s) com {MODELOS[args.modelo]}"
              f" ({args.paralelo} em paralelo)...\n")

        analises: Dict[str, AnaliseVaga] = {}
        analises, falhas = _analisar_paralelo(client, pendentes, args.modelo, args.paralelo)

        # Segunda chance sequencial para falhas individuais.
        if falhas:
            print(f"\n  Re-tentando {len(falhas)} vaga(s) que falharam...")
            restantes = []
            for vid, texto, _ in falhas:
                try:
                    analises[vid] = analisar_vaga(client, texto, args.modelo)
                    print(f"  [{vid}] OK na segunda tentativa")
                except Exception as e:  # noqa: BLE001 — registramos e seguimos
                    restantes.append((vid, texto, e))
            falhas = restantes

        for vid, texto in pendentes:
            if vid not in analises:
                continue
            vaga = pontuar(analises[vid], vid)
            historico.registrar(hist, vaga, texto)
            resultados.append(vaga)

        try:
            historico.salvar(hist)
        except OSError as e:
            print(f"Erro ao salvar histórico: {e}")
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
            print(f"  [{vid}] {type(e).__name__}: {e}")
        print("Rode novamente para reprocessar (as já analisadas serão puladas pelo histórico).")
    return 0


def _analisar_paralelo(client, pendentes, modelo, paralelo):
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
                vaga = pontuar(analise, vid)
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
    except FileNotFoundError as e:
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
        print(f"Erro ao iniciar cliente da API: {e}")
        return 1
    analise = entrada.get("analise", {})
    print(f"Gerando material para [{vid}] {analise.get('empresa') or '?'} - "
          f"{analise.get('titulo_normalizado') or '?'} com {MODELOS[args.modelo]}...\n")

    try:
        material = gerar_material(client, cv_base, entrada.get("texto", ""), analise, args.modelo)
    except Exception as e:
        print(f"Erro ao gerar material pela API ({type(e).__name__}): {e}")
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
