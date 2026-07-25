"""Score composto e regras determinísticas que não dependem do modelo."""

from typing import Dict, Optional

from .schema import AnaliseVaga, VagaPontuada

PESOS = {
    "d1_crescimento": 0.30,
    "d2_regime_localizacao": 0.25,
    "d3_stack_fit": 0.20,
    "d4_ingles": 0.15,
    "d5_nivel_real": 0.10,
}

# Regra fixa do spec — sobrescreve a nota do modelo para garantir consistência
D2_POR_REGIME = {"remoto": 10, "hibrido": 8, "presencial": 6}


def parse_pesos(texto: str) -> Dict[str, float]:
    """Interpreta `d1=0.15,d2=0.30,...` do CLI e valida que a soma é 1.0."""
    apelidos = {
        "d1": "d1_crescimento",
        "d2": "d2_regime_localizacao",
        "d3": "d3_stack_fit",
        "d4": "d4_ingles",
        "d5": "d5_nivel_real",
    }
    pesos = dict(PESOS)
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        chave, _, valor = parte.partition("=")
        nome = apelidos.get(chave.strip().lower())
        if not nome:
            raise ValueError(f"Dimensão desconhecida em --pesos: '{chave.strip()}'. Use d1..d5.")
        try:
            peso = float(valor)
        except ValueError as e:
            raise ValueError(f"Peso inválido para {chave.strip()}: '{valor}'.") from e
        # Só a soma era validada: `d1=1.55,d2=-1.00,...` somava 1.0 e passava,
        # com uma dimensão puxando o score para baixo quanto melhor fosse a nota.
        if not 0.0 <= peso <= 1.0:
            raise ValueError(
                f"Peso fora da faixa para {chave.strip()}: {peso:g}. Use um valor entre 0 e 1."
            )
        pesos[nome] = peso
    total = sum(pesos.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Os pesos devem somar 1.0 (informado: {total:.3f}).")
    return pesos


def pontuar(
    analise: AnaliseVaga, vid: str = "", pesos: Optional[Dict[str, float]] = None
) -> VagaPontuada:
    """Devolve a vaga pontuada; score_final fica None quando descartada."""
    pesos = pesos or PESOS
    # Evita que a regra determinística altere o objeto retornado pela API ou
    # reutilizado pelo chamador.
    analise = analise.model_copy(deep=True)
    if analise.descartada or analise.notas is None:
        return VagaPontuada(id=vid, analise=analise)

    notas = analise.notas
    nota_d2 = D2_POR_REGIME[analise.regime]
    if notas.d2_regime_localizacao.nota != nota_d2:
        notas.d2_regime_localizacao.nota = nota_d2
        notas.d2_regime_localizacao.justificativa += " (nota ajustada pela regra fixa do regime)"

    score = (
        notas.d1_crescimento.nota * pesos["d1_crescimento"]
        + notas.d2_regime_localizacao.nota * pesos["d2_regime_localizacao"]
        + notas.d3_stack_fit.nota * pesos["d3_stack_fit"]
        + notas.d4_ingles.nota * pesos["d4_ingles"]
        + notas.d5_nivel_real.nota * pesos["d5_nivel_real"]
    ) * 10  # 0-10 ponderado -> 0-100

    return VagaPontuada(id=vid, analise=analise, score_final=round(score, 1))
