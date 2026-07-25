"""Exportação do relatório para Markdown ou CSV (escolhido pela extensão)."""

import csv
from datetime import datetime
from pathlib import Path
from typing import List

from .relatorio import ROTULOS_NIVEL, ROTULOS_REGIME, separar
from .schema import VagaPontuada


def _seguro_para_planilha(valor):
    """Neutraliza fórmulas: Excel executa células iniciadas por = + - @ (CSV injection).

    Descrições de vaga chegam de terceiros; um `=HYPERLINK(...)` no título viraria
    fórmula ativa na planilha do usuário.
    """
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return valor


def exportar(resultados: List[VagaPontuada], caminho: str) -> None:
    destino = Path(caminho)
    ext = destino.suffix.lower()
    destino.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".md":
        destino.write_text(_markdown(resultados), encoding="utf-8")
    elif ext == ".csv":
        _csv(resultados, caminho)
    else:
        raise ValueError(f"Extensão '{ext}' não suportada em --saida (use .md ou .csv).")


def _markdown(resultados: List[VagaPontuada]) -> str:
    aprovadas, descartadas = separar(resultados)
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = [
        f"# Triagem de vagas — {agora}",
        "",
        f"**Total:** {len(resultados)} | **Aprovadas:** {len(aprovadas)} | "
        f"**Descartadas (hard filter):** {len(descartadas)}",
        "",
    ]

    for i, vaga in enumerate(aprovadas, start=1):
        a = vaga.analise
        n = a.notas
        linhas += [
            f"## #{i} — {a.empresa or '?'} — {a.titulo_normalizado} ({vaga.score_final:.0f}/100)",
            "",
            f"- **ID:** `{vaga.id}` | **Regime:** {ROTULOS_REGIME[a.regime]} ({a.localizacao or '?'}) | "
            f"**Nível:** {ROTULOS_NIVEL[a.nivel_real]} | **Idioma:** {a.idioma_trabalho} | **Origem:** {a.origem}",
            f"- **Link:** {a.link or '(não informado)'}",
            f"- **Stack exigida:** {', '.join(a.stack_exigida) or '—'}",
            f"- **Stack desejável:** {', '.join(a.stack_desejavel) or '—'}",
            "",
            "| Dimensão | Nota | Justificativa |",
            "|---|---|---|",
            f"| Crescimento (30%) | {n.d1_crescimento.nota}/10 | {n.d1_crescimento.justificativa} |",
            f"| Regime/Loc (25%) | {n.d2_regime_localizacao.nota}/10 | {n.d2_regime_localizacao.justificativa} |",
            f"| Stack fit (20%) | {n.d3_stack_fit.nota}/10 | {n.d3_stack_fit.justificativa} |",
            f"| Inglês (15%) | {n.d4_ingles.nota}/10 | {n.d4_ingles.justificativa} |",
            f"| Nível real (10%) | {n.d5_nivel_real.nota}/10 | {n.d5_nivel_real.justificativa} |",
            "",
            f"**⚠ Alertas:** {'; '.join(a.alertas) if a.alertas else 'nenhum'}",
            "",
        ]

    if descartadas:
        linhas += ["## Descartadas (hard filter)", ""]
        for vaga in descartadas:
            a = vaga.analise
            linhas.append(
                f"- `{vaga.id}` {a.empresa or '?'} — {a.titulo_normalizado} — "
                f"{a.motivo_descarte or 'motivo não informado'}"
            )
        linhas.append("")

    return "\n".join(linhas)


def _csv(resultados: List[VagaPontuada], caminho: str) -> None:
    aprovadas, descartadas = separar(resultados)
    colunas = [
        "id", "score", "empresa", "titulo", "regime", "localizacao", "nivel_real",
        "idioma", "origem", "link", "stack_exigida", "stack_desejavel",
        "d1", "d2", "d3", "d4", "d5", "alertas", "motivo_descarte",
    ]
    # utf-8-sig para o Excel abrir acentos corretamente
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=colunas)
        writer.writeheader()
        for vaga in aprovadas + descartadas:
            a = vaga.analise
            n = a.notas
            writer.writerow({k: _seguro_para_planilha(v) for k, v in {
                "id": vaga.id,
                "score": vaga.score_final if vaga.score_final is not None else "",
                "empresa": a.empresa,
                "titulo": a.titulo_normalizado,
                "regime": a.regime,
                "localizacao": a.localizacao,
                "nivel_real": a.nivel_real,
                "idioma": a.idioma_trabalho,
                "origem": a.origem,
                "link": a.link,
                "stack_exigida": "; ".join(a.stack_exigida),
                "stack_desejavel": "; ".join(a.stack_desejavel),
                "d1": n.d1_crescimento.nota if n else "",
                "d2": n.d2_regime_localizacao.nota if n else "",
                "d3": n.d3_stack_fit.nota if n else "",
                "d4": n.d4_ingles.nota if n else "",
                "d5": n.d5_nivel_real.nota if n else "",
                "alertas": "; ".join(a.alertas),
                "motivo_descarte": a.motivo_descarte or "",
            }.items()})
