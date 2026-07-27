"""Migração de execução única: limpa o histórico legado da validação de 2026-07-27.

Por que existe: as entradas gravadas antes da refatoração carregam dados que as
correções não reescrevem retroativamente —

- `Nerdin Vagas de TI` como empregador (é o portal; o JSON-LD da página diz que o
  anunciante é anônimo, e a vaga expirou em 2025-12-09);
- AvePoint com `localizacao: "Remoto"` inventada pelo LLM (o anúncio é presencial
  em Da Nang, no Vietnã);
- quatro links da Adzuna com `link_final` vazio, resquício do 403 que o cabeçalho
  de navegador resolveu;
- a mesma vaga da People Partners em duas entradas, uma por fonte.

Rodar isto deixa a base limpa para que os testes de regressão futuros meçam o
pipeline novo, e não o resíduo do antigo.

    python migrar_historico.py --simular   # mostra o que faria, sem gravar
    python migrar_historico.py             # aplica, com backup

Status manual (`aplicado`, `entrevista`, `recusado`) é preservado por padrão: é
informação que só existe na sua cabeça e não pode ser recuperada por reprocesso.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from triagem import historico  # noqa: E402
from triagem.dedup import Registro, agrupar  # noqa: E402

STATUS_MANUAIS = ("aplicado", "entrevista", "recusado")


def _registro(ident: str, entrada: dict) -> Registro:
    try:
        origem = json.loads(entrada.get("texto") or "{}")
    except ValueError:
        origem = {}
    return Registro(
        ident=ident,
        url=origem.get("link_final") or origem.get("link") or "",
        empresa=origem.get("empresa") or "",
        titulo=origem.get("titulo") or "",
        confianca=origem.get("confianca_empresa") or "media",
        localidade=origem.get("localizacao") or "",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simular", action="store_true", help="Não grava nada; só relata")
    parser.add_argument(
        "--descartar-status",
        action="store_true",
        help="Também apaga entradas com status manual (aplicado/entrevista/recusado)",
    )
    args = parser.parse_args()

    historico.aplicar_config_do_ambiente()
    caminho = historico.ARQUIVO
    if not caminho.exists():
        print(f"Nada a migrar: '{caminho}' não existe.")
        return 0

    hist = historico.carregar()
    print(f"Histórico atual: {len(hist)} entrada(s) em {caminho}\n")

    preservadas: dict[str, dict] = {}
    removidas: list[tuple[str, str]] = []
    for ident, entrada in hist.items():
        status = entrada.get("status", "novo")
        if status in STATUS_MANUAIS and not args.descartar_status:
            preservadas[ident] = entrada
            print(f"  MANTIDA   [{ident}] status manual '{status}'")
        else:
            removidas.append((ident, status))

    print()
    for ident, status in removidas:
        entrada = hist[ident]
        try:
            origem = json.loads(entrada.get("texto") or "{}")
        except ValueError:
            origem = {}
        print(f"  REMOVIDA  [{ident}] {origem.get('empresa', '?')} — "
              f"{str(origem.get('titulo', '?'))[:44]} (status: {status})")

    # Diagnóstico: quantas das removidas eram, na verdade, a mesma vaga?
    grupos = agrupar([_registro(i, hist[i]) for i, _ in removidas])
    duplicatas = sum(len(g) - 1 for g in grupos)
    print(f"\n  {len(removidas)} removida(s), {len(preservadas)} preservada(s)")
    print(f"  destas, {duplicatas} eram duplicata(s) que a cascata nova teria fundido")

    if args.simular:
        print("\n--simular: nada foi gravado.")
        return 0

    if caminho.exists():
        backup = caminho.with_suffix(f"{caminho.suffix}.pre-migracao")
        shutil.copy2(caminho, backup)
        print(f"\nBackup: {backup}")

    historico.salvar(preservadas)
    marca = caminho.parent / ".migracao_historico.json"
    marca.write_text(
        json.dumps(
            {
                "migrado_em": datetime.now(timezone.utc).isoformat(),
                "removidas": [i for i, _ in removidas],
                "preservadas": sorted(preservadas),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Histórico regravado com {len(preservadas)} entrada(s). Marca em {marca}")
    print("\nPróximo passo: rode `python triar.py buscar` para repovoar com o pipeline novo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
