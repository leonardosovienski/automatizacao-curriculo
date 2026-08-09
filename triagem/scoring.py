"""Score composto e regras determinísticas que não dependem do modelo."""

from typing import Dict, Optional

from . import perfil_usuario
from .schema import AnaliseVaga, VagaPontuada

PESOS = {
    "d1_crescimento": 0.30,
    "d2_regime_localizacao": 0.25,
    "d3_stack_fit": 0.20,
    "d4_ingles": 0.15,
    "d5_nivel_real": 0.10,
}

# Regra fixa do spec — sobrescreve a nota do modelo para garantir consistência.
# `indefinido` vale menos que o pior caso conhecido: omissão de metadado pelo
# anunciante é pior que uma condição ruim declarada, porque não dá para decidir.
D2_POR_REGIME = {"remoto": 10, "hibrido": 7, "presencial": 6, "indefinido": 4}

ALERTA_REGIME_INDEFINIDO = (
    "Regime não declarado pela fonte — nota D2 reduzida por omissão, confirmar no anúncio."
)

# A regra acima olhava só o regime, e presencial no Vietnã valia os mesmos 6/10 que
# presencial em Curitiba. Medido em 2026-07-27: a vaga da AvePoint em Da Nang virou a
# recomendação #1 com 78/100. A causa raiz (localização inventada na extração) já foi
# fechada, mas a D2 continuava sem saber a diferença entre "do lado" e "outro
# continente" — bastava a próxima falha de extração para o mesmo resultado voltar.
#
# Nota máxima quando a vaga NÃO é remota e a praça está fora do raio aceitável.
# Não é descarte: quem descarta é o hard filter, com a descrição inteira na mão. Aqui
# é a nota refletindo que deslocar-se para lá é inviável.
D2_PRESENCIAL_FORA_DO_RAIO = 1
D2_HIBRIDO_FORA_DO_RAIO = 2

CIDADES_ACEITAS = ("curitiba", "araucaria", "sao jose dos pinhais", "colombo", "pinhais")
# Termos que não identificam praça: não servem para provar proximidade nem distância.
LOCAL_SEM_PRACA = ("brasil", "brazil", "remoto", "remota", "remote", "home office",
                   "hibrido", "nacional", "todo o pais", "anywhere", "")


def _fora_do_raio(localizacao: str) -> bool:
    """True só quando a praça é conhecida E claramente longe de Curitiba.

    Localização vazia ou genérica devolve False de propósito: ausência de dado não é
    prova de distância, e punir o desconhecido reintroduziria o palpite que passamos
    a sessão inteira removendo.
    """
    texto = " ".join((localizacao or "").lower().split())
    if not texto:
        return False
    sem_acento = (
        texto.replace("á", "a").replace("ã", "a").replace("â", "a")
        .replace("é", "e").replace("ê", "e").replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o").replace("ú", "u")
        .replace("ç", "c")
    )
    cidades = tuple(_sem_acento(c) for c in perfil_usuario.atual().cidades_aceitas)
    if any(cidade in sem_acento for cidade in cidades):
        return False
    if any(termo and termo in sem_acento for termo in LOCAL_SEM_PRACA):
        return False
    # Sobrou uma praça específica que não é nenhuma das aceitas.
    return True


def _sem_acento(texto: str) -> str:
    return (
        texto.lower().replace("á", "a").replace("ã", "a").replace("â", "a")
        .replace("é", "e").replace("ê", "e").replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o").replace("ú", "u")
        .replace("ç", "c")
    )


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
    pesos = pesos or perfil_usuario.atual().pesos
    # Evita que a regra determinística altere o objeto retornado pela API ou
    # reutilizado pelo chamador.
    analise = analise.model_copy(deep=True)
    if analise.descartada or analise.notas is None:
        return VagaPontuada(id=vid, analise=analise)

    notas = analise.notas
    nota_d2 = D2_POR_REGIME[analise.regime]
    motivo = "regra fixa do regime"
    # O alerta é determinístico e não depende do modelo lembrar de escrevê-lo: a nota
    # sozinha se dilui no score composto, e omissão de regime é o tipo de coisa que
    # precisa aparecer na leitura rápida do relatório.
    if analise.regime == "indefinido" and ALERTA_REGIME_INDEFINIDO not in analise.alertas:
        analise.alertas.append(ALERTA_REGIME_INDEFINIDO)
    # Remoto não é afetado por distância — é o ponto de ser remoto.
    if analise.regime != "remoto" and _fora_do_raio(analise.localizacao):
        nota_d2 = (
            D2_PRESENCIAL_FORA_DO_RAIO if analise.regime == "presencial"
            else D2_HIBRIDO_FORA_DO_RAIO
        )
        motivo = f"{analise.regime} em {analise.localizacao.strip()}, fora do raio de deslocamento"
    if notas.d2_regime_localizacao.nota != nota_d2:
        notas.d2_regime_localizacao.nota = nota_d2
        notas.d2_regime_localizacao.justificativa += f" (nota ajustada: {motivo})"

    score = (
        notas.d1_crescimento.nota * pesos["d1_crescimento"]
        + notas.d2_regime_localizacao.nota * pesos["d2_regime_localizacao"]
        + notas.d3_stack_fit.nota * pesos["d3_stack_fit"]
        + notas.d4_ingles.nota * pesos["d4_ingles"]
        + notas.d5_nivel_real.nota * pesos["d5_nivel_real"]
    ) * 10  # 0-10 ponderado -> 0-100

    return VagaPontuada(id=vid, analise=analise, score_final=round(score, 1))
