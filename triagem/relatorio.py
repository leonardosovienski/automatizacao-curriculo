"""Renderização do relatório no terminal, no formato do spec."""

from typing import List, Tuple

from .schema import VagaPontuada

ROTULOS_NIVEL = {
    "estagio": "ESTÁGIO",
    "jr": "JR",
    "pleno_disfarcado": "PLENO DISFARÇADO",
    "senior": "SÊNIOR",
}
ROTULOS_REGIME = {
    "remoto": "REMOTO",
    "hibrido": "HÍBRIDO",
    "presencial": "PRESENCIAL",
    "indefinido": "INDEFINIDO",
}


def separar(resultados: List[VagaPontuada]) -> Tuple[List[VagaPontuada], List[VagaPontuada]]:
    """(aprovadas ordenadas por score desc, descartadas)."""
    aprovadas = sorted(
        (r for r in resultados if r.score_final is not None),
        key=lambda r: r.score_final,
        reverse=True,
    )
    descartadas = [r for r in resultados if r.score_final is None]
    return aprovadas, descartadas


def render_relatorio(resultados: List[VagaPontuada]) -> str:
    aprovadas, descartadas = separar(resultados)
    linhas: List[str] = []

    for i, vaga in enumerate(aprovadas, start=1):
        a = vaga.analise
        n = a.notas
        linhas.append("---")
        linhas.append(
            f"[#{i}] SCORE: {vaga.score_final:.0f}/100 | ID: {vaga.id or '?'} | "
            f"REGIME: {ROTULOS_REGIME[a.regime]} | NÍVEL: {ROTULOS_NIVEL[a.nivel_real]}"
        )
        linhas.append(f"Empresa: {a.empresa or '?'} | Título: {a.titulo_normalizado}")
        linhas.append(f"Link: {a.link or '(não informado)'}")
        linhas.append("")
        linhas.append(f"▸ Crescimento:   {n.d1_crescimento.nota}/10 — {n.d1_crescimento.justificativa}")
        linhas.append(f"▸ Regime/Loc:    {n.d2_regime_localizacao.nota}/10 — {n.d2_regime_localizacao.justificativa}")
        linhas.append(f"▸ Stack fit:     {n.d3_stack_fit.nota}/10 — {n.d3_stack_fit.justificativa}")
        linhas.append(f"▸ Inglês:        {n.d4_ingles.nota}/10 — {n.d4_ingles.justificativa}")
        linhas.append(f"▸ Nível real:    {n.d5_nivel_real.nota}/10 — {n.d5_nivel_real.justificativa}")
        linhas.append("")
        alertas = "; ".join(a.alertas) if a.alertas else "nenhum"
        linhas.append(f"⚠ Alertas: {alertas}")
        linhas.append("---")
        linhas.append("")

    if descartadas:
        linhas.append("DESCARTADAS (hard filter):")
        for vaga in descartadas:
            a = vaga.analise
            motivo = a.motivo_descarte or "motivo não informado"
            linhas.append(f"  ✗ [{vaga.id or '?'}] {a.empresa or '?'} - {a.titulo_normalizado} — {motivo}")
        linhas.append("")

    total = len(resultados)
    top = f"{aprovadas[0].analise.empresa} - {aprovadas[0].analise.titulo_normalizado}" if aprovadas else "nenhuma"
    linhas.append(
        f"TOTAL ANALISADAS: {total} | DESCARTADAS (hard filter): {len(descartadas)} | "
        f"APROVADAS: {len(aprovadas)} | TOP RECOMENDADA: {top}"
    )
    return "\n".join(linhas)
