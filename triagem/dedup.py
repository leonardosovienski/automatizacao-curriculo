"""Cascata de deduplicação em três camadas, na altura da persistência.

A chave por URL sozinha é cega para o caso mais comum: a mesma vaga publicada em
portais diferentes. Medido em 2026-07-27 — a vaga `DevSecOps Júnior` da People
Partners Consult entrou duas vezes no histórico, uma pelo LinkedIn e outra pela
Adzuna, com URLs distintas e duas análises pagas.

**Falso merge é pior que falso split.** Se duas vagas distintas da mesma empresa
viram uma, a outra some sem aviso e nunca recebe candidatura; uma duplicata que
escapa custa uma chamada de API e uma linha repetida no relatório. Toda a
calibragem abaixo segue essa assimetria — na dúvida, não funde.

Camadas, da mais determinística para a mais tolerante:

- **A — identidade exata**: URL canônica. Pega republicação no mesmo portal.
- **B — identidade estrutural**: `(empresa canônica, núcleo do cargo)` vindo de
  JSON-LD ou de campo estruturado da API. `datePosted` e `addressLocality` NÃO
  entram na chave: medido na Adzuna, o `datePosted` do JSON-LD e o `publicada_em`
  da API divergem em até 2 dias **na mesma página**, e a localidade de vaga remota
  é a sede da empresa, que muda de portal para portal. Os dois servem como
  corroboração, elevando a confiança da fusão, nunca como igualdade.
- **C — identidade semântica tolerante**: Jaccard sobre o núcleo do cargo, para os
  pares em que ao menos um lado não tem JSON-LD. Foi o caso da People Partners: o
  LinkedIn não publica schema.org, então só esta camada podia fundir aquele par.
"""

import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable, Optional

# Sufixos societários e ruído que variam de portal para portal para a mesma empresa.
SUFIXOS_SOCIETARIOS = frozenset(
    {
        "ltda", "sa", "me", "epp", "eireli", "eirelli", "inc", "llc",
        "corp", "corporation", "co", "cia", "group", "grupo", "holding",
        "tecnologia", "tecnologias", "solucoes", "solutions", "servicos",
        "consultoria", "brasil", "brazil",
    }
)

# Sufixos de mais de uma palavra precisam sair ANTES da tokenização: o filtro
# acima compara token a token, então `"s a"` e `"do brasil"` — que estavam no
# conjunto — nunca podiam casar com nada. Eram código morto declarando uma
# intenção que não acontecia: `ACME S/A` virava `acme s a` (o `/` vira espaço em
# `_normalizar`) e não colapsava para `acme`.
#
# `"do brasil"` foi deliberadamente DEIXADO de fora, apesar de estar no conjunto
# antigo. Ativá-lo faria `Volvo do Brasil` casar com `Volvo` (correto), mas
# também `Banco do Brasil` virar `banco` — e aí um empregador chamado só `Banco`
# seria fundido com ele. Não há regra estrutural que separe "nome + qualificador
# de país" de "nome próprio que contém o país"; a diferença é conhecimento de
# mundo. Como falso merge é pior que falso split (ver docstring do módulo), fica
# de fora. O comentário em test_dedup.py já registrava esse risco.
SUFIXOS_COMPOSTOS = ("s a",)

# Ruído de título: senioridade, regime e marketing. O que sobra é o núcleo do cargo.
# Sem isso, "Work From Home Junior DevOps / Rd" e "Junior DevOps Engineer" — a mesma
# vaga da BairesDev em portais diferentes — nunca colidem.
RUIDO_DE_CARGO = frozenset(
    {
        "junior", "jr", "senior", "sr", "pleno", "estagio", "estagiario", "trainee",
        "entry", "level", "i", "ii", "iii", "n1", "n2",
        "remoto", "remota", "remote", "home", "office", "work", "from", "wfh",
        "hibrido", "presencial", "teletrabalho", "anywhere",
        "vaga", "vagas", "job", "jobs", "oportunidade", "efetivo", "clt", "pj",
        "rd", "r d", "urgente", "novo", "nova", "contrata", "se", "de", "do", "da",
        "e", "em", "para", "com", "the", "and", "for", "at", "in",
        "brasil", "brazil", "latam",
    }
)

# 0.5 e não 0.6: `{devops}` contra `{devops, engineer}` dá exatamente 0.5, e são a
# mesma vaga com um sufixo de cargo a mais. O que protege do falso merge não é o
# limiar sozinho — é o portão de empresa: funções realmente distintas na mesma
# empresa ficam abaixo dele de qualquer forma (`Data Engineer` × `Data Analyst` = 0.33).
LIMIAR_JACCARD = 0.5
TOLERANCIA_DIAS_CORROBORACAO = 3


def _normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", texto or "")
    ascii_texto = "".join(c for c in sem_acentos if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9+#.]+", " ", ascii_texto.lower()).split())


def empresa_canonica(nome: str) -> str:
    """Nome comparável entre portais: sem acento, pontuação nem sufixo societário.

    O ponto é removido aqui e **só aqui**. `_normalizar` o preserva de propósito,
    porque `nucleo_do_cargo` precisa de `.net` e `node.js` inteiros — mas em nome
    de empresa ele é só pontuação, e mantê-lo fazia `"ltda."` não casar com
    SUFIXOS_SOCIETARIOS, que guarda `"ltda"`. O efeito atingia quase toda forma
    abreviada (`S.A.`, `Inc.`, `Corp.`, `Cia.`), não só uma: apenas as grafias sem
    pontuação funcionavam.

    Medido em 2026-08-08, na primeira busca real: a SKA entrou duas vezes no
    histórico, uma como `SKA AUTOMACAO DE ENGENHARIAS LTDA` e outra com ponto
    final. Duas análises pagas para o mesmo anúncio.

    Remover o ponto de todos os tokens é seguro porque o valor devolvido só é
    usado para comparar igualdade (camadas B e C) — nunca é exibido. `Booking.com`
    vira `bookingcom` dos dois lados e continua casando consigo mesmo.
    """
    texto = " ".join(bruto.replace(".", "") for bruto in _normalizar(nome).split())
    for composto in SUFIXOS_COMPOSTOS:
        texto = re.sub(rf"(?:^|\s){re.escape(composto)}(?=\s|$)", " ", texto)
    return " ".join(t for t in texto.split() if t not in SUFIXOS_SOCIETARIOS)


def nucleo_do_cargo(titulo: str) -> frozenset[str]:
    """Conjunto de tokens que identifica a função, sem senioridade nem marketing.

    Conjunto, e não string: a ordem das palavras muda entre portais para o mesmo
    anúncio, e comparar string exigiria que os dois títulos fossem idênticos.
    """
    tokens = [t for t in _normalizar(titulo).split() if t and t not in RUIDO_DE_CARGO]
    return frozenset(tokens)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- camada A

def chave_exata(url_canonica: str) -> str:
    return url_canonica or ""


# ---------------------------------------------------------------- camada B

def chave_estrutural(empresa: str, titulo: str) -> Optional[tuple[str, frozenset[str]]]:
    """None quando falta empresa ou núcleo — sem os dois não há identidade estrutural."""
    canonica = empresa_canonica(empresa)
    nucleo = nucleo_do_cargo(titulo)
    if not canonica or not nucleo:
        return None
    return (canonica, nucleo)


def corroborado(
    data_a: Optional[int], data_b: Optional[int], local_a: str, local_b: str
) -> bool:
    """Sinal auxiliar: datas próximas ou mesma localidade reforçam a fusão."""
    if data_a is not None and data_b is not None:
        if abs(data_a - data_b) <= TOLERANCIA_DIAS_CORROBORACAO:
            return True
    if local_a and local_b and _normalizar(local_a) == _normalizar(local_b):
        return True
    return False


# ---------------------------------------------------------------- camada C

def pode_fundir_semanticamente(
    empresa_a: str, confianca_a: str, empresa_b: str, confianca_b: str
) -> bool:
    """Portão da camada C.

    Funde quando (a) um dos lados tem empresa de origem estruturada, ou (b) as duas
    fontes independentes nomeiam exatamente o mesmo empregador. O critério (b) é o
    que salva o caso real: a entrada do LinkedIn tinha `confianca_empresa: media`
    porque o LinkedIn não publica JSON-LD, e sob a regra "só com alta" a duplicata
    que motivou esta cascata continuaria duplicada. Um nome alucinado não reaparece
    idêntico num segundo material independente — corroboração cruzada é evidência.
    """
    canonica_a = empresa_canonica(empresa_a)
    canonica_b = empresa_canonica(empresa_b)
    if not canonica_a or not canonica_b:
        return False
    if canonica_a in ("desconhecida", "desconhecido"):
        return False
    if canonica_b in ("desconhecida", "desconhecido"):
        return False
    if "alta" in (confianca_a, confianca_b):
        return True
    return canonica_a == canonica_b


# ---------------------------------------------------------------- orquestração

class Registro:
    """O mínimo que a cascata precisa saber sobre uma vaga, venha de onde vier."""

    __slots__ = (
        "id", "url", "empresa", "titulo", "confianca", "idade_dias", "localidade",
        "ats_provedor", "ats_token", "ats_job_id",
    )

    def __init__(
        self,
        ident: str,
        url: str,
        empresa: str,
        titulo: str,
        confianca: str = "media",
        idade_dias: Optional[int] = None,
        localidade: str = "",
        ats_provedor: str = "",
        ats_token: str = "",
        ats_job_id: str = "",
    ):
        self.id = ident
        self.url = url
        self.empresa = empresa
        self.titulo = titulo
        self.confianca = confianca
        self.idade_dias = idade_dias
        self.localidade = localidade
        self.ats_provedor = ats_provedor
        self.ats_token = ats_token
        self.ats_job_id = ats_job_id


def idade_da_publicacao(publicada_em: str) -> Optional[int]:
    bruto = str(publicada_em or "").strip().replace("Z", "+00:00")
    if not bruto:
        return None
    try:
        momento = datetime.fromisoformat(bruto)
    except ValueError:
        try:
            momento = datetime.fromisoformat(bruto[:10])
        except ValueError:
            return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - momento).days)


def _mesma_vaga(a: Registro, b: Registro) -> Optional[str]:
    """Nome da camada que funde o par, ou None."""
    if a.url and b.url and chave_exata(a.url) == chave_exata(b.url):
        return "A"

    identidade_a = (a.ats_provedor, a.ats_token, a.ats_job_id)
    identidade_b = (b.ats_provedor, b.ats_token, b.ats_job_id)
    if all(identidade_a) and all(identidade_b):
        return "ATS" if identidade_a == identidade_b else None

    chave_a = chave_estrutural(a.empresa, a.titulo)
    chave_b = chave_estrutural(b.empresa, b.titulo)
    if chave_a and chave_b and chave_a == chave_b:
        # Empresa+título não identificam uma requisição. Mesmo com confiança alta,
        # exija data/local compatível para não apagar duas aberturas distintas.
        if corroborado(a.idade_dias, b.idade_dias, a.localidade, b.localidade):
            return "B"

    if not pode_fundir_semanticamente(a.empresa, a.confianca, b.empresa, b.confianca):
        return None
    if empresa_canonica(a.empresa) != empresa_canonica(b.empresa):
        return None
    if (
        jaccard(nucleo_do_cargo(a.titulo), nucleo_do_cargo(b.titulo)) >= LIMIAR_JACCARD
        and corroborado(a.idade_dias, b.idade_dias, a.localidade, b.localidade)
    ):
        return "C"
    return None


def registro_de_historico(ident: str, entrada: dict) -> Optional[Registro]:
    """Converte uma entrada gravada em Registro, lendo o material de origem.

    `analise` é o que o LLM concluiu e `texto` é o que a fonte entregou. A empresa
    vem do material de origem, não da análise: é ele que carrega `confianca_empresa`.
    """
    import json as _json

    try:
        origem = _json.loads(entrada.get("texto") or "{}")
    except ValueError:
        return None
    if not isinstance(origem, dict):
        return None
    return Registro(
        ident=ident,
        url=origem.get("link_final") or origem.get("link") or "",
        empresa=origem.get("empresa") or "",
        titulo=origem.get("titulo") or "",
        confianca=origem.get("confianca_empresa") or "media",
        idade_dias=idade_da_publicacao(origem.get("publicada_em") or ""),
        localidade=origem.get("localizacao") or "",
        ats_provedor=origem.get("ats_provedor") or "",
        ats_token=origem.get("ats_token") or "",
        ats_job_id=origem.get("ats_job_id") or "",
    )


def resolver_id(historico: dict, novo: Registro) -> Optional[str]:
    """Id já existente para esta mesma vaga, ou None.

    Percorre o histórico inteiro em vez de consultar uma chave: é exatamente o que
    a chave por URL não consegue fazer, e o histórico tem dezenas de entradas, não
    milhões. Quando escalar, isto vira índice por `(empresa canônica, núcleo)`.
    """
    for ident, entrada in historico.items():
        antigo = registro_de_historico(ident, entrada)
        if antigo is None:
            continue
        if _mesma_vaga(antigo, novo):
            return ident
    return None


def resolver_registro(registros: Iterable[Registro], novo: Registro) -> Optional[str]:
    """ID de uma vaga equivalente já aceita no lote atual, ou None."""
    for antigo in registros:
        if _mesma_vaga(antigo, novo):
            return antigo.id
    return None


def agrupar(registros: Iterable[Registro]) -> list[list[Registro]]:
    """Agrupa duplicatas preservando a ordem de chegada.

    O primeiro de cada grupo é a vencedora; os demais viram alias. Nada é
    descartado: `solides.com.br` e o link do LinkedIn não são intercambiáveis para
    quem vai se candidatar, então as duas URLs continuam disponíveis.
    """
    grupos: list[list[Registro]] = []
    for registro in registros:
        for grupo in grupos:
            if any(_mesma_vaga(grupo[0], outro) for outro in (registro,)):
                grupo.append(registro)
                break
        else:
            grupos.append([registro])
    return grupos
