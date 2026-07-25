"""Busca vagas atuais combinando APIs de emprego, Google Search Grounding e metabusca.

Princípio da camada: **fonte estruturada não passa por modelo**. Jooble e Adzuna já
devolvem título/empresa/localização/link/data em JSON — converter isso com um LLM só
introduz perda e alucinação (uma execução real perdeu 100% dos 29 anúncios da Jooble
porque o modelo resumiu a entrada). O modelo é usado apenas onde a entrada é texto
livre: resultados do Google Search Grounding e da metabusca DDGS.
"""

import html
import json
import os
import random
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from ddgs import DDGS
from google import genai
from google.genai import types
from pydantic import ValidationError

from . import cache
from .analisador import (
    MODELOS,
    TIMEOUT_BUSCA_MS,
    gerar_com_retentativa,
    texto_da_resposta,
)
from .schema import ResultadoBusca, VagaEncontrada

MODELO_BUSCA = "gemini-3.5-flash-lite"

SCHEMA_BUSCA = ResultadoBusca.model_json_schema()

# Anúncios mais antigos que isto são descartados: o objetivo é vaga aberta hoje.
DIAS_MAXIMOS_ANUNCIO = 60

TERMOS_ALVO = (
    "devops",
    "devsecops",
    "platform engineer",
    "platform engineering",
    "site reliability",
    "sre",
    "cloud",
    "c#",
    ".net",
    "dotnet",
)
TERMOS_ENTRADA = ("junior", "jr", "estagio", "estagiario", "trainee", "entry level")
TERMOS_SENIOR = (
    "senior",
    "sr",
    "especialista",
    "architect",
    "arquiteto",
    "tech lead",
    "technical lead",
    "staff",
    "principal",
    "gerente",
    "manager",
    "coordenador",
    "pleno",
    "mid level",  # "mid-level" cai aqui: o hífen vira espaço na normalização
    "midlevel",
    "team lead",
    "lider tecnico",
)
TERMOS_LOCAL = ("remoto", "remote", "brasil", "brazil", "curitiba", "araucaria")
TERMOS_INTERNACIONAL_ACEITO = (
    "brasil",
    "brazil",
    "latin america",
    "latam",
    "worldwide",
    "world wide",
    "global remote",
    "anywhere",
    "international contractor",
)
# Frases que provam que a vaga exige residência/autorização fora do Brasil.
TERMOS_RESTRICAO_EXTERIOR = (
    "us only",
    "usa only",
    "u.s. only",
    "united states only",
    "us based only",
    "us residents only",
    "must be located in the united states",
    "must reside in the united states",
    "must be based in the united states",
    "authorized to work in the united states",
    "eligible to work in the us",
    "canada only",
    "uk only",
    "eu only",
    "emea only",
    "must be based in europe",
    # Sem patrocínio de visto, "remote" nesses anúncios significa remoto no país deles.
    "no visa sponsorship",
    "visa sponsorship is not available",
    "we do not sponsor",
    "unable to sponsor",
    "not able to sponsor",
)

# Países cuja exigência de residência/autorização exclui alguém no Brasil.
# Brasil/LATAM ficam fora de propósito: "authorized to work in Brazil" não reprova.
_PAISES_EXTERIOR = (
    r"(?:the\s+)?(?:us|usa|u\.s\.?|united\s+states|america|uk|united\s+kingdom|eu|"
    r"european\s+union|europe|emea|canada|australia|germany|france|spain|ireland|"
    r"netherlands|portugal|india|singapore|japan)"
)
# A lista literal acima só pega a frase exata. Estes padrões cobrem as variações
# equivalentes ("right to work in the UK", "cannot sponsor work visas"), que antes
# passavam inteiras quando o host não era um portal conhecido.
PADROES_RESTRICAO_EXTERIOR = tuple(re.compile(p) for p in (
    rf"(?:authorized|authorised|eligible)\s+to\s+work\s+in\s+{_PAISES_EXTERIOR}",
    rf"right\s+to\s+work\s+in\s+{_PAISES_EXTERIOR}",
    rf"{_PAISES_EXTERIOR}\s+work\s+(?:authorization|authorisation|permit)",
    rf"(?:must|should)\s+(?:be\s+)?(?:currently\s+)?"
    rf"(?:located|based|residing|reside|live|living)\s+(?:in|within)\s+"
    rf"(?:the\s+)?(?:continental\s+)?{_PAISES_EXTERIOR}",
    rf"candidates?\s+(?:residing|located|based|living)\s+in\s+{_PAISES_EXTERIOR}",
    rf"must\s+hold\s+{_PAISES_EXTERIOR}\s+citizenship",
    rf"{_PAISES_EXTERIOR}\s+citizens?\s+only",
    r"(?:cannot|can\s+not|do\s+not|does\s+not|will\s+not|unable\s+to|not\s+able\s+to)"
    r"\s+sponsor",
    r"sponsorship\s+(?:is\s+)?not\s+(?:provided|available|offered|possible)",
    r"no\s+(?:visa\s+|work\s+)?sponsorship",
))
MARCADORES_REMOTO = (
    "remoto",
    "remota",
    # Plurais: fontes escrevem "Vagas remotas" no campo de localização, e sem
    # eles a vaga era tratada como se a praça declarada fosse presencial.
    "remotos",
    "remotas",
    "remotamente",
    "remote",
    "home office",
    "homeoffice",
    "teletrabalho",
    "anywhere",
    "100% remoto",
)
# "Não oferecemos trabalho remoto" contém a palavra "remoto" e, sem olhar para a
# negação, provava que a vaga era remota — exatamente ao contrário do que o
# anúncio diz. Deliberadamente fora da lista: "no", que em português é preposição
# ("no modelo remoto") e reprovaria vaga remota de verdade.
TERMOS_NEGACAO = ("nao", "sem", "nunca", "nenhum", "nenhuma", "not", "without", "exceto")
# Quantas palavras antes do marcador ainda contam como negação dele.
JANELA_NEGACAO = 4
# Anúncios que não são uma vaga: cadastro de currículo, pool de talentos.
TERMOS_NAO_VAGA = (
    "banco de talentos",
    "banco de curriculos",
    "cadastro de curriculo",
    "candidatura espontanea",
    "talent pool",
    "talent community",
    "vaga afirmativa cadastro",
)

PORTAIS_INTERNACIONAIS = (
    "virtualvocations.com",
    "weworkremotely.com",
    "remoteok.com",
    "remoteok.io",
    "remotive.com",
    "wellfound.com",
    "angel.co",
    "dice.com",
    "ziprecruiter.com",
    "monster.com",
    "simplyhired.com",
    "jobgether.com",
    "himalayas.app",
    "workingnomads.com",
    "flexjobs.com",
    "arc.dev",
    "toptal.com",
    "turing.com",
    "builtin.com",
    "otta.com",
    "welcometothejungle.com",
)
# Nunca são a página canônica de um anúncio: são posts, encurtadores e feeds que
# apenas republicam vagas. Uma execução real aprovou com 74/100 um tweet de bot
# (x.com/.../status/...) — o host era desconhecido e respondeu 200, então nem o
# filtro de elegibilidade nem a validação de link o pegaram.
HOSTS_NAO_CANONICOS = (
    "x.com",
    "twitter.com",
    "t.co",
    "facebook.com",
    "instagram.com",
    "t.me",
    "telegram.me",
    "whatsapp.com",
    "reddit.com",
    "medium.com",
    "youtube.com",
    "bit.ly",
    "tinyurl.com",
    "lnkd.in",
    # Encurtadores: escondem o destino real do pré-filtro. A lista nunca fica
    # completa, por isso o host também é reavaliado depois de seguir o redirect
    # (ver _validar_links) — a lista só evita gastar uma requisição à toa.
    "ow.ly",
    "buff.ly",
    "is.gd",
    "rb.gy",
    "shorturl.at",
    "cutt.ly",
    "rebrand.ly",
    "goo.gl",
    "s.id",
    "tiny.cc",
    "shorte.st",
)

PORTAIS_BRASILEIROS = (
    "gupy.io",
    "vagas.com.br",
    "catho.com.br",
    "infojobs.com.br",
    "adzuna.com.br",
    "jooble.org",
    "trabalhabrasil.com.br",
    "empregos.com.br",
    "solides.jobs",
    "inhire.app",
    "abler.com.br",
    "kenoby.com",
    "99jobs.com",
    "programathor.com.br",
    "geekhunter.com.br",
    "revelo.com.br",
    "netvagas.com.br",
)

CIDADES_ACEITAS = ("curitiba", "araucaria")
# Termos de localização que não identificam uma praça específica.
LOCAL_GENERICO = frozenset(
    {
        "brasil",
        "brazil",
        "remoto",
        "remota",
        "remote",
        "home",
        "office",
        "homeoffice",
        "teletrabalho",
        "hibrido",
        "nacional",
        "anywhere",
        "todo",
        "pais",
        "br",
    }
)

# Parâmetros de URL que servem só para rastreio. O utm_source da Adzuna carrega o
# ADZUNA_APP_ID — sem esta limpeza a credencial vaza para relatório/CSV/histórico.
PARAMS_RASTREIO = frozenset(
    {
        "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "_ga",
        # LinkedIn: posição do resultado na busca. Muda a cada execução e, sem
        # remover, a mesma vaga vira uma chave de dedup diferente todo dia.
        "trk", "trackingid", "refid", "position", "pagenum", "originalsubdomain",
        "savedsearchid", "ebp",
    }
)  # sempre em minúsculas: _param_de_rastreio compara com a chave normalizada
PREFIXOS_RASTREIO = ("utm_",)

Log = Callable[[str], None]

# Mesmo conjunto que `_normalizar` preserva (o `[^a-z0-9+#.]` da regex). Como
# frozenset porque `_normalizar_com_indices` testa caractere a caractere.
CARACTERES_MANTIDOS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789+#.")


def _sem_log(_: str) -> None:
    return None


# ---------------------------------------------------------------- texto e URL

def _normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", texto or "")
    ascii_texto = "".join(c for c in sem_acentos if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9+#.]+", " ", ascii_texto.lower()).split())


def _contem_termo(texto: str, termos: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", texto) for termo in termos)


def _normalizar_com_indices(texto: str) -> tuple[str, list[int]]:
    """`_normalizar`, mas devolvendo o índice de origem de cada caractere.

    `_normalizar` remove acentos e pontuação e colapsa espaços, então o texto
    normalizado é mais curto que o original e os índices dos dois NÃO coincidem.
    Quem precisa localizar algo no texto normalizado e depois recortar o texto
    original tem que traduzir a posição — sem isso o recorte sai deslocado.
    """
    saida: list[str] = []
    indices: list[int] = []
    separador_pendente = False
    for posicao, caractere in enumerate(texto or ""):
        minusculo = caractere.lower()
        if minusculo in CARACTERES_MANTIDOS:
            # Caminho rápido: para ASCII a decomposição NFKD é a identidade, então
            # a chamada a unicodedata só queimaria tempo. É a esmagadora maioria
            # dos caracteres de uma página, e o loop roda sobre até 400 KB.
            base = minusculo
        else:
            decomposto = unicodedata.normalize("NFKD", caractere)
            base = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
        for convertido in base:
            if convertido in CARACTERES_MANTIDOS:
                if separador_pendente and saida:
                    saida.append(" ")
                    indices.append(posicao)
                separador_pendente = False
                saida.append(convertido)
                indices.append(posicao)
            else:
                separador_pendente = True
    return "".join(saida), indices


def _texto_limpo(bruto: str) -> str:
    """Snippets da Jooble vêm com `&nbsp;` e `<b>`; a Adzuna corta a descrição."""
    sem_tags = re.sub(r"<[^>]+>", " ", bruto or "")
    return " ".join(html.unescape(sem_tags).split())


def _redigir_segredos(texto: str) -> str:
    """Nunca deixe uma chave de API chegar ao terminal ou a um arquivo de saída."""
    for variavel in ("GEMINI_API_KEY", "JOOBLE_API_KEY", "ADZUNA_APP_ID", "ADZUNA_API_KEY"):
        valor = os.environ.get(variavel)
        if valor and len(valor) >= 8:
            texto = texto.replace(valor, f"<{variavel}>")
    return texto


def _param_de_rastreio(chave: str) -> bool:
    baixa = chave.lower()
    return baixa in PARAMS_RASTREIO or baixa.startswith(PREFIXOS_RASTREIO)


def _limpar_url(url: str) -> str:
    """Remove parâmetros de rastreio preservando os que identificam o anúncio."""
    partes = urlsplit((url or "").strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=False)
         if not _param_de_rastreio(k)]
    )
    return urlunsplit((partes.scheme, partes.netloc, partes.path, query, ""))


def _url_canonica(url: str) -> str:
    """Chave de deduplicação. Mantém a query: em Indeed/LinkedIn o id da vaga está nela."""
    partes = urlsplit(_limpar_url(url))
    query = urlencode(sorted(parse_qsl(partes.query, keep_blank_values=False)))
    return urlunsplit(
        (partes.scheme.lower(), partes.netloc.lower(), partes.path.rstrip("/"), query, "")
    )


def _dias_desde(publicada_em: str) -> Optional[int]:
    """Idade do anúncio em dias, ou None quando a data não é interpretável."""
    bruto = (publicada_em or "").strip()
    if not bruto:
        return None
    bruto = bruto.replace("Z", "+00:00")
    # Jooble devolve 7 casas decimais; datetime.fromisoformat aceita no máximo 6.
    bruto = re.sub(r"(\.\d{6})\d+", r"\1", bruto)
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


# ---------------------------------------------------------------- filtros

def _pontuacao_preliminar(vaga: VagaEncontrada) -> int:
    titulo = _normalizar(vaga.titulo)
    conteudo = _normalizar(f"{vaga.titulo} {vaga.descricao}")
    if _contem_termo(titulo, TERMOS_SENIOR):
        return -100
    if _contem_termo(conteudo, TERMOS_NAO_VAGA):
        return -100

    pontos = 0
    if _contem_termo(conteudo, TERMOS_ALVO):
        pontos += 8
    if _contem_termo(titulo, TERMOS_ALVO):
        pontos += 5
    if _contem_termo(conteudo, TERMOS_ENTRADA):
        pontos += 6
    if _contem_termo(titulo, TERMOS_ENTRADA):
        pontos += 5
    if _contem_termo(conteudo, TERMOS_LOCAL):
        pontos += 3
    # 3+ anos de experiência, em português e em inglês.
    if re.search(r"(?<!\w)(?:[3-9]|1\d)\s*\+?\s*(?:anos?|years?)(?!\w)", conteudo):
        pontos -= 8
    # Senioridade citada só na descrição vale penalidade, não descarte automático.
    if _contem_termo(conteudo, TERMOS_SENIOR) and not _contem_termo(conteudo, TERMOS_ENTRADA):
        pontos -= 8
    idade = _dias_desde(vaga.publicada_em)
    if idade is not None and idade > 30:
        pontos -= 3
    if not vaga.empresa.strip():
        pontos -= 2
    if len(vaga.descricao) < 100:
        pontos -= 2
    return pontos


def _host_canonico(url: str) -> bool:
    """False para post de rede social, encurtador e agregador de link.

    O candidato precisa cair na página de candidatura, não num tweet que fala
    sobre a vaga. Recebe a URL solta porque a checagem roda duas vezes: no
    pré-filtro (sobre o link anunciado) e depois de seguir o redirect.
    """
    partes = urlsplit(url or "")
    host = partes.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if any(host == ruim or host.endswith(f".{ruim}") for ruim in HOSTS_NAO_CANONICOS):
        return False
    # No LinkedIn o anúncio vive em /jobs/; /feed/update/... é um post sobre a vaga.
    if "linkedin.com" in host and "/jobs/" not in partes.path:
        return False
    return True


def _host_de_anuncio(vaga: VagaEncontrada) -> bool:
    return _host_canonico(vaga.link)


def _area_alvo(vaga: VagaEncontrada) -> bool:
    """O termo-alvo precisa estar no TÍTULO, não em qualquer lugar do texto.

    "cloud", "devops" e ".net" aparecem na descrição de quase toda vaga de TI: com a
    checagem no texto inteiro entraram na triagem paga "Talent Sourcer", "Data
    Scientist" e "Data Engineer" só porque a descrição citava cloud.
    """
    return _contem_termo(_normalizar(vaga.titulo), TERMOS_ALVO)


def _chave_semantica(vaga: VagaEncontrada) -> tuple[str, str]:
    titulo = _normalizar(vaga.titulo)
    for termo in TERMOS_ENTRADA + TERMOS_SENIOR:
        titulo = re.sub(rf"(?<!\w){re.escape(termo)}(?!\w)", " ", titulo)
    return (" ".join(titulo.split()), _normalizar(vaga.empresa))


def _anuncio_recente(vaga: VagaEncontrada) -> bool:
    idade = _dias_desde(vaga.publicada_em)
    return idade is None or idade <= DIAS_MAXIMOS_ANUNCIO


def _remoto_afirmado(conteudo: str) -> bool:
    """True só quando alguma menção a trabalho remoto NÃO está negada.

    `conteudo` já vem normalizado. Uma única menção afirmativa basta; o que não
    pode acontecer é a frase "não oferecemos trabalho remoto" contar como prova
    de que a vaga é remota.
    """
    for marcador in MARCADORES_REMOTO:
        for achado in re.finditer(rf"(?<!\w){re.escape(marcador)}(?!\w)", conteudo):
            anteriores = conteudo[: achado.start()].split()[-JANELA_NEGACAO:]
            if any(palavra in TERMOS_NEGACAO for palavra in anteriores):
                continue
            # "no remote work": em inglês "no" nega; em português é preposição,
            # então só vale quando é a palavra imediatamente anterior.
            if anteriores and anteriores[-1] == "no" and marcador in ("remote", "anywhere"):
                continue
            return True
    return False


def _local_declarado_incompativel(vaga: VagaEncontrada) -> bool:
    """A fonte declara uma praça específica fora de Curitiba/Araucária.

    **A praça declarada vence o texto do anúncio.** Só o próprio campo de
    localização pode dizer que a vaga é remota — a descrição, não. O modelo já
    classificou como `remoto` uma vaga cujo campo estruturado da Adzuna dizia
    "Recife, Pernambuco", e a regra fixa do D2 então premiava o erro com 10/10.
    Deixar a descrição desempatar reabria essa porta: bastava a palavra "remoto"
    aparecer em qualquer canto do anúncio.

    O preço é assumido: vaga genuinamente remota que a fonte carimba com a
    cidade-sede da empresa é reprovada. Falso negativo é mais barato que enviar o
    candidato para uma vaga presencial em outro estado.
    """
    local = _normalizar(vaga.localizacao)
    if not local or _contem_termo(local, CIDADES_ACEITAS):
        return False
    especifico = [palavra for palavra in local.split() if palavra not in LOCAL_GENERICO]
    if not especifico:
        return False  # "Brasil", "Remoto" — não há praça para reprovar
    # "São Paulo, SP (Remoto)" é a fonte declarando remoto, e vale. Já
    # "São Paulo (não remoto)" continua reprovando, via _remoto_afirmado.
    return not _remoto_afirmado(local)


def _restricao_de_residencia(conteudo: str) -> bool:
    """A vaga exige residência/autorização de trabalho fora do Brasil."""
    if _contem_termo(conteudo, TERMOS_RESTRICAO_EXTERIOR):
        return True
    return any(padrao.search(conteudo) for padrao in PADROES_RESTRICAO_EXTERIOR)


def _localizacao_compativel(vaga: VagaEncontrada) -> bool:
    """Barra vaga que exige residência fora do Brasil sem abertura explícita."""
    host = urlsplit(vaga.link).netloc.lower()
    conteudo = _normalizar(f"{vaga.titulo} {vaga.descricao} {vaga.localizacao}")
    if _restricao_de_residencia(conteudo):
        return _contem_termo(conteudo, TERMOS_INTERNACIONAL_ACEITO)
    if host.endswith(".br") or any(portal in host for portal in PORTAIS_BRASILEIROS):
        return True
    if "linkedin.com" in host and not host.startswith("br."):
        return _contem_termo(conteudo, TERMOS_INTERNACIONAL_ACEITO)
    if any(portal in host for portal in PORTAIS_INTERNACIONAIS):
        return _contem_termo(conteudo, TERMOS_INTERNACIONAL_ACEITO)
    return True  # host desconhecido: sem evidência contrária, não reprova


MARCADORES_EXPIRADOS = (
    "no longer accepting applications",
    "nao aceita mais candidaturas",
    "job is no longer available",
    "vaga nao esta mais disponivel",
    "vaga encerrada",
    "esta vaga foi encerrada",
    "expired jd redirect",
)

# Limite do texto extraído da página do anúncio. O suficiente para os requisitos
# completos, sem inflar o prompt de análise com rodapé e menu do portal.
MAX_DESCRICAO_ENRIQUECIDA = 6000


@dataclass
class Inspecao:
    """Resultado de uma única visita à página do anúncio.

    Uma requisição responde três perguntas de uma vez: o anúncio está no ar, para
    onde o agregador redireciona (chave de dedup entre fontes) e qual é o texto
    completo da descrição (Adzuna/Jooble truncam). Fazer três buscas separadas
    triplicaria o tráfego contra os mesmos portais.
    """

    ativo: bool
    url_final: str = ""
    texto_pagina: str = ""


def _obter(link: str):
    return httpx.get(
        link,
        follow_redirects=True,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0 (compatible; TriagemVagas/1.0)"},
    )


def _inspecionar_link(vaga: VagaEncontrada) -> Inspecao:
    """Remove expiração clara e host morto, preservando sites que bloqueiam bots.

    Erro de rede ganha uma segunda tentativa: falhar na primeira pode ser um soluço
    de DNS/TLS, mas falhar duas vezes seguidas é sinal de anúncio fora do ar. Erros
    que não são de rede (URL inválida, IDNA) não são culpa do anúncio — mantemos.
    """
    try:
        response = _obter(vaga.link)
    except httpx.RequestError:
        try:
            response = _obter(vaga.link)
        except httpx.RequestError:
            return Inspecao(ativo=False)
    except Exception:
        # httpx.InvalidURL e erros de IDNA não herdam de HTTPError e antes
        # derrubavam a busca inteira.
        return Inspecao(ativo=True)
    if response.status_code in (401, 403, 429):
        return Inspecao(ativo=True)
    if response.status_code >= 400:
        return Inspecao(ativo=False)

    original = urlsplit(vaga.link)
    url_final = _limpar_url(str(response.url))
    final = urlsplit(url_final)
    if "linkedin.com" in original.netloc and "/jobs/view/" in original.path:
        if "/jobs/view/" not in final.path:
            return Inspecao(ativo=False)
    try:
        bruto = response.text[:400_000]
    except Exception:
        return Inspecao(ativo=True, url_final=url_final)
    if any(marcador in _normalizar(bruto[:100_000]) for marcador in MARCADORES_EXPIRADOS):
        return Inspecao(ativo=False, url_final=url_final)
    return Inspecao(ativo=True, url_final=url_final, texto_pagina=bruto)


def _texto_visivel(html_bruto: str) -> str:
    """Texto legível da página, sem script/style/tags."""
    sem_ruido = re.sub(
        r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html_bruto or ""
    )
    return _texto_limpo(sem_ruido)


def _enriquecer_descricao(vaga: VagaEncontrada, texto_pagina: str) -> VagaEncontrada:
    """Substitui a descrição truncada pelo texto da própria página do anúncio.

    Conservador de propósito: só troca quando a página é claramente mais rica e
    ainda contém o começo da descrição original — caso contrário estaríamos
    colando o menu do portal no lugar dos requisitos da vaga.
    """
    if not texto_pagina:
        return vaga
    visivel = _texto_visivel(texto_pagina)
    if len(visivel) <= len(vaga.descricao) * 1.5:
        return vaga
    # Âncora: as primeiras palavras da descrição da API precisam existir na página.
    inicio = " ".join(_normalizar(vaga.descricao).split()[:6])
    normalizado, indices = _normalizar_com_indices(visivel)
    if inicio and inicio not in normalizado:
        return vaga
    encontrado = normalizado.find(inicio)
    # A posição vem do texto normalizado; o recorte é no texto visível. Traduzir
    # é obrigatório: sem isso o corte começa antes da âncora e cola o menu do
    # portal no lugar dos requisitos.
    posicao = indices[encontrado] if 0 <= encontrado < len(indices) else 0
    trecho = visivel[posicao : posicao + MAX_DESCRICAO_ENRIQUECIDA]
    if len(trecho) <= len(vaga.descricao):
        return vaga
    return vaga.model_copy(update={"descricao": trecho, "descricao_completa": True})


def _validar_links(
    vagas: list[VagaEncontrada], limite: int, log: Log = _sem_log
) -> list[VagaEncontrada]:
    """Valida, resolve o redirect e enriquece a descrição em uma passada só."""
    if not vagas:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(vagas))) as executor:
        inspecoes = list(executor.map(_inspecionar_link, vagas))

    ativas: list[VagaEncontrada] = []
    enriquecidas = 0
    vistos: set[str] = set()
    duplicadas = 0
    redirecionadas = 0
    for vaga, inspecao in zip(vagas, inspecoes, strict=True):
        if not inspecao.ativo:
            continue
        # O pré-filtro julgou o link anunciado; o redirect pode terminar num post
        # de rede social ou num encurtador que a lista não conhecia. Sem esta
        # segunda checagem, um encurtador fora da lista entrega um tweet.
        if inspecao.url_final and not _host_canonico(inspecao.url_final):
            redirecionadas += 1
            continue
        atualizada = vaga.model_copy(update={"link_final": inspecao.url_final})
        antes = len(atualizada.descricao)
        atualizada = _enriquecer_descricao(atualizada, inspecao.texto_pagina)
        if len(atualizada.descricao) > antes:
            enriquecidas += 1
        # Dedup entre fontes: Jooble e Adzuna redirecionam para o mesmo anúncio.
        chave = _url_canonica(atualizada.chave_dedup())
        if chave in vistos:
            duplicadas += 1
            continue
        vistos.add(chave)
        ativas.append(atualizada)

    inativas = len(vagas) - len(ativas) - duplicadas - redirecionadas
    if inativas:
        log(f"  link inativo/expirado: {inativas} descartada(s)")
    if redirecionadas:
        log(f"  redirect terminou fora de página de anúncio: {redirecionadas} descartada(s)")
    if duplicadas:
        log(f"  mesma vaga em fontes diferentes: {duplicadas} mesclada(s)")
    if enriquecidas:
        log(f"  descrição completa obtida da página: {enriquecidas} vaga(s)")
    return ativas[:limite]


def _selecionar_candidatas(
    vagas: list[VagaEncontrada], limite: int, log: Log = _sem_log
) -> list[VagaEncontrada]:
    """Remove lixo/senioridade/vaga vencida, deduplica e ordena antes da triagem cara."""
    cortes = {"area": 0, "senioridade": 0, "antiga": 0, "local": 0, "nao_anuncio": 0, "duplicada": 0}
    # Guarda a pontuação junto da vaga: ela já foi calculada para decidir o corte e
    # recalculá-la na ordenação repetia todo o trabalho de normalização e regex.
    pontuadas: list[tuple[int, VagaEncontrada]] = []
    for vaga in vagas:
        if not _host_de_anuncio(vaga):
            cortes["nao_anuncio"] += 1
            continue
        if not _area_alvo(vaga):
            cortes["area"] += 1
            continue
        pontos = _pontuacao_preliminar(vaga)
        if pontos < 8:
            cortes["senioridade"] += 1
            continue
        if not _anuncio_recente(vaga):
            cortes["antiga"] += 1
            continue
        if _local_declarado_incompativel(vaga) or not _localizacao_compativel(vaga):
            cortes["local"] += 1
            continue
        pontuadas.append((pontos, vaga))

    def _ordem(par: tuple[int, VagaEncontrada]):
        pontos, vaga = par
        # `_dias_desde` era chamada duas vezes por vaga só para testar contra None.
        idade = _dias_desde(vaga.publicada_em)
        return (pontos, -(idade if idade is not None else 999), len(vaga.descricao))

    pontuadas.sort(key=_ordem, reverse=True)
    candidatas = [vaga for _, vaga in pontuadas]
    unicas = []
    urls_vistas = set()
    vagas_vistas = set()
    for vaga in candidatas:
        url = _url_canonica(vaga.link)
        semantica = _chave_semantica(vaga)
        if url in urls_vistas or semantica in vagas_vistas:
            cortes["duplicada"] += 1
            continue
        urls_vistas.add(url)
        vagas_vistas.add(semantica)
        unicas.append(vaga)

    descartes = ", ".join(f"{motivo}: {n}" for motivo, n in cortes.items() if n)
    log(f"  pré-filtro: {len(unicas)} candidata(s) de {len(vagas)}" + (f" ({descartes})" if descartes else ""))
    return unicas[:limite]


# ---------------------------------------------------------------- fontes estruturadas

def _vaga_ou_none(**campos) -> Optional[VagaEncontrada]:
    """Constrói a vaga descartando o item quando ele não satisfaz o schema."""
    try:
        return VagaEncontrada(**campos)
    except ValidationError:
        return None


def _com_tentativas(
    chamada,
    tentativas: int = 3,
    espera_inicial: float = 1.0,
    espera_base: float = 2.0,
):
    """Repete uma chamada HTTP com backoff e jitter; None se todas falharem.

    Engole a exceção de propósito: a URL da Jooble contém a chave de API e a
    Adzuna leva app_id/app_key na query string.
    """
    for tentativa in range(tentativas):
        try:
            return chamada()
        except Exception:
            if tentativa == tentativas - 1:
                return None
            time.sleep(espera_inicial * espera_base**tentativa * random.uniform(1.0, 2.0))
    return None


def _buscar_jooble(pedido: str, limite: int) -> list[VagaEncontrada]:
    """Consulta a API estruturada da Jooble quando uma chave está configurada."""
    api_key = os.environ.get("JOOBLE_API_KEY")
    if not api_key:
        return []

    consultas = [
        pedido,
        "DevOps Junior",
        "DevSecOps Junior",
        "Desenvolvedor .NET C# Junior",
    ]
    vagas: list[VagaEncontrada] = []
    vistos = set()
    for consulta in consultas:
        def chamar(consulta=consulta):
            response = httpx.post(
                f"https://jooble.org/api/{api_key}",
                json={
                    "keywords": consulta,
                    "location": "Brazil",
                    "page": "1",
                    "ResultOnPage": str(min(15, max(5, limite // 3))),
                    "companysearch": "false",
                },
                timeout=20,
            )
            response.raise_for_status()
            return response.json()

        payload = _com_tentativas(chamar)
        if not isinstance(payload, dict):
            continue
        for item in payload.get("jobs", []):
            link = _limpar_url(item.get("link") or "")
            if not link or link in vistos:
                continue
            vistos.add(link)
            snippet = _texto_limpo(item.get("snippet", ""))
            contexto = " | ".join(
                parte for parte in (
                    _texto_limpo(item.get("location", "")),
                    _texto_limpo(item.get("type", "")),
                    _texto_limpo(item.get("salary", "")),
                    f"fonte original: {item.get('source', '')}" if item.get("source") else "",
                ) if parte
            )
            vaga = _vaga_ou_none(
                titulo=_texto_limpo(item.get("title", "")) or "?",
                empresa=_texto_limpo(item.get("company", "")),
                descricao=f"{snippet} ({contexto})" if contexto else snippet,
                link=link,
                origem="jooble",
                publicada_em=item.get("updated", "") or "",
                localizacao=_texto_limpo(item.get("location", "")),
            )
            if vaga:
                vagas.append(vaga)
    return vagas


def _buscar_adzuna(pedido: str, limite: int) -> list[VagaEncontrada]:
    """Consulta vagas brasileiras na Adzuna quando as duas credenciais existem."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_API_KEY")
    if not app_id or not app_key:
        return []

    consultas = (pedido, "DevOps Junior", "DevSecOps Junior", ".NET C# Junior")
    vagas: list[VagaEncontrada] = []
    vistos = set()
    por_consulta = min(20, max(5, limite // len(consultas)))
    for consulta in consultas:
        def chamar(consulta=consulta):
            response = httpx.get(
                "https://api.adzuna.com/v1/api/jobs/br/search/1",
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": consulta,
                    "results_per_page": por_consulta,
                    "sort_by": "date",
                    "max_days_old": DIAS_MAXIMOS_ANUNCIO,
                    "content-type": "application/json",
                },
                timeout=20,
            )
            response.raise_for_status()
            return response.json()

        payload = _com_tentativas(chamar)
        if not isinstance(payload, dict):
            continue
        for item in payload.get("results", []):
            # redirect_url traz `utm_source=<ADZUNA_APP_ID>`: limpar é obrigatório.
            link = _limpar_url(item.get("redirect_url") or "")
            if not link or link in vistos:
                continue
            vistos.add(link)
            empresa = item.get("company") or {}
            localizacao = item.get("location") or {}
            vaga = _vaga_ou_none(
                titulo=_texto_limpo(item.get("title", "")) or "?",
                empresa=_texto_limpo(empresa.get("display_name", "")),
                descricao=_texto_limpo(item.get("description", "")),
                link=link,
                origem="adzuna",
                publicada_em=item.get("created", "") or "",
                localizacao=_texto_limpo(localizacao.get("display_name", "")),
            )
            if vaga:
                vagas.append(vaga)
    return vagas


# ---------------------------------------------------------------- fontes textuais

def _fontes_grounding(response) -> list[str]:
    fontes = []
    for candidate in response.candidates or []:
        metadata = candidate.grounding_metadata
        if not metadata:
            continue
        for chunk in metadata.grounding_chunks or []:
            web = chunk.web
            if web and web.uri:
                titulo = web.title or "fonte"
                fontes.append(f"- {titulo}: {web.uri}")
    return list(dict.fromkeys(fontes))


def _busca_metasearch(pedido: str, limite: int) -> tuple[str, list[str]]:
    """Fallback sem chave própria quando a cota do Google Search está indisponível."""
    consultas = [
        f"{pedido} vaga",
        "vaga DevOps Junior OR estágio remoto Brasil Azure",
        "vaga desenvolvedor C# .NET Junior OR estágio remoto Brasil",
        "vaga DevOps Junior OR C# .NET Curitiba",
    ]
    resultados = []
    vistos = set()
    max_por_consulta = min(15, max(5, limite // 3))
    for indice, consulta in enumerate(consultas):
        if len(vistos) >= limite:
            break  # já temos material suficiente; consulta a mais só arrisca bloqueio
        if indice:
            # Pausa entre consultas, não só na retentativa. Medido: em rajada a 4ª
            # consulta volta estrangulada (1 resultado em vez de 8); com pausa o
            # mesmo conjunto rendeu 32 em vez de 25.
            time.sleep(random.uniform(1.5, 3.0))
        # Instância nova por consulta: o DDG bloqueia rajadas na mesma sessão.
        # Só 2 tentativas e espera longa: o bloqueio é por padrão de uso, não por
        # cota — insistir rápido piora. Backoff curto seria contraproducente.
        def chamar(consulta=consulta):
            return list(
                DDGS().text(
                    consulta,
                    region="br-pt",
                    safesearch="moderate",
                    timelimit="m",
                    max_results=max_por_consulta,
                )
            )

        encontrados = _com_tentativas(chamar, tentativas=2, espera_inicial=5.0, espera_base=1.0)
        if not encontrados:
            continue
        for item in encontrados:
            link = item.get("href") or item.get("url")
            if not link or link in vistos:
                continue
            vistos.add(link)
            resultados.append(
                f"Título: {item.get('title', '')}\n"
                f"Link: {link}\n"
                f"Resumo: {item.get('body', '')}"
            )
    texto = "\n\n---\n\n".join(resultados)
    fontes = [f"- resultado: {link}" for link in vistos]
    return texto, fontes


def _normalizar_texto_livre(
    client: genai.Client,
    modelo_analise: str,
    resultados_texto: str,
    fontes: list[str],
    limite_coleta: int,
    log: Log = _sem_log,
) -> list[VagaEncontrada]:
    """Só o texto livre passa pelo modelo — e item inválido não derruba o lote."""
    resposta = gerar_com_retentativa(
        client,
        model=MODELOS[modelo_analise],
        contents=(
            f"Converta os resultados abaixo em até {limite_coleta} vagas candidatas. "
            "DESCARTE antes de responder: Sênior/Sr, Pleno, Staff, Lead, Principal, "
            "Arquiteto, Especialista, Gerente, vagas que exijam 3+ anos e áreas sem "
            "relação com DevOps/DevSecOps/Cloud/Platform/SRE/C#/.NET. Priorize Estágio, "
            "Trainee, Júnior/Jr, remoto Brasil e Curitiba/Araucária. Inclua somente "
            "anúncios específicos com URL HTTP(S), copie a localização declarada no "
            "anúncio para o campo `localizacao`, preserve requisitos e não invente "
            "informações.\n\n"
            f"RESULTADOS:\n{resultados_texto}\n\nFONTES:\n" + "\n".join(fontes)
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=SCHEMA_BUSCA,
            http_options=types.HttpOptions(timeout=TIMEOUT_BUSCA_MS),
        ),
    )
    bruto = texto_da_resposta(resposta)
    if not bruto.strip():
        log("  metabusca: o modelo não devolveu JSON — resultados de texto livre ignorados")
        return []
    # Valida item a item: antes, uma única vaga com descrição curta ou link
    # relativo invalidava a lista inteira e a busca terminava em erro.
    try:
        dados = json.loads(bruto)
    except ValueError:
        log("  metabusca: JSON inválido devolvido pelo modelo — resultados ignorados")
        return []
    itens = dados.get("vagas", []) if isinstance(dados, dict) else []
    origem_normalizada = _normalizar(resultados_texto)
    vagas = []
    invalidas = 0
    inferidas = 0
    for item in itens:
        if not isinstance(item, dict):
            invalidas += 1
            continue
        item = dict(item)
        item["link"] = _limpar_url(str(item.get("link", "")))
        item["confianca_empresa"] = "media"
        vaga = _vaga_ou_none(**{k: v for k, v in item.items() if k in VagaEncontrada.model_fields})
        if not vaga:
            invalidas += 1
            continue
        vaga = _ancorar_empresa(vaga, origem_normalizada)
        if vaga.confianca_empresa == "baixa":
            inferidas += 1
        vagas.append(vaga)
    if invalidas:
        log(f"  metabusca: {invalidas} item(ns) fora do schema descartado(s)")
    if inferidas:
        log(f"  metabusca: {inferidas} empresa(s) sem respaldo no texto → 'Desconhecida'")
    return vagas


def _ancorar_empresa(vaga: VagaEncontrada, origem_normalizada: str) -> VagaEncontrada:
    """Se o nome da empresa não aparece no material de origem, o modelo o inventou.

    Regressão real: a mesma URL saiu como "Casado.dev", "Sylision" e "Desconhecida"
    em execuções diferentes. Checagem determinística — o modelo não é consultado
    sobre a própria confiança, porque ele erra isso também.
    """
    empresa = _normalizar(vaga.empresa)
    if not empresa:
        return vaga.model_copy(update={"empresa": "Desconhecida", "confianca_empresa": "baixa"})
    if empresa in origem_normalizada:
        return vaga.model_copy(update={"confianca_empresa": "media"})
    return vaga.model_copy(update={"empresa": "Desconhecida", "confianca_empresa": "baixa"})


# ---------------------------------------------------------------- orquestração

def _fonte_estruturada(
    nome: str,
    funcao,
    pedido: str,
    limite_coleta: int,
    estado_cache: dict,
    consulta_cache: str,
    usar_cache: bool,
    registrar: Log,
) -> list[VagaEncontrada]:
    """Consulta a fonte usando cache dentro do TTL e cache vencido como rede."""
    if usar_cache:
        guardadas, idade = cache.obter(estado_cache, nome, consulta_cache)
        if guardadas is not None:
            registrar(
                f"  {nome}: {len(guardadas)} anúncio(s) — cache de "
                f"{cache.descrever_idade(idade)} (use --sem-cache para forçar)"
            )
            return [VagaEncontrada.model_validate(d) for d in guardadas]

    try:
        achadas = funcao(pedido, limite_coleta)
    except Exception as e:  # noqa: BLE001 — uma fonte fora do ar não derruba a busca
        registrar(f"  {nome}: indisponível ({_redigir_segredos(f'{type(e).__name__}: {e}')})")
        achadas = []

    if achadas:
        registrar(f"  {nome}: {len(achadas)} anúncio(s)")
        cache.guardar(estado_cache, nome, consulta_cache, [v.model_dump() for v in achadas])
        return achadas

    # --sem-cache significa "consulte a fonte de verdade": nem entrada fresca nem
    # vencida podem ser servidas, senão a flag não faz o que promete.
    vencidas, idade = (
        cache.obter_vencido(estado_cache, nome, consulta_cache) if usar_cache else (None, None)
    )
    if vencidas:
        registrar(
            f"  {nome}: sem resposta agora — usando cache de "
            f"{cache.descrever_idade(idade)} ({len(vencidas)} anúncio(s))"
        )
        return [VagaEncontrada.model_validate(d) for d in vencidas]

    registrar(f"  {nome}: sem resultados (ou credencial não configurada)")
    return []


def _texto_livre_com_cache(
    nome: str,
    produzir,
    estado_cache: dict,
    consulta_cache: str,
    usar_cache: bool,
    registrar: Log,
) -> tuple[str, list[str]]:
    """Mesmo contrato de `_fonte_estruturada`, mas para as fontes de texto livre.

    Sem isto, a instabilidade do DDGS (o DuckDuckGo bloqueia rajadas e devolve
    zero) atingia toda execução: a busca perdia a única fonte de texto livre viva
    enquanto a cota do Google Search está esgotada.
    """
    if usar_cache:
        guardado, idade = cache.obter(estado_cache, nome, consulta_cache)
        if guardado:
            registrar(
                f"  {nome}: {len(guardado.get('fontes', []))} resultado(s) — cache de "
                f"{cache.descrever_idade(idade)}"
            )
            return guardado.get("texto", ""), guardado.get("fontes", [])

    try:
        texto, fontes = produzir()
    except Exception as e:  # noqa: BLE001
        texto, fontes = "", []
        registrar(f"  {nome}: indisponível ({type(e).__name__})")

    if texto.strip():
        cache.guardar(estado_cache, nome, consulta_cache, {"texto": texto, "fontes": fontes})
        registrar(f"  {nome}: {len(fontes)} resultado(s)")
        return texto, fontes

    if usar_cache:
        vencido, idade = cache.obter_vencido(estado_cache, nome, consulta_cache)
        if vencido and vencido.get("texto"):
            registrar(
                f"  {nome}: sem resposta agora — usando cache de "
                f"{cache.descrever_idade(idade)} ({len(vencido.get('fontes', []))} resultado(s))"
            )
            return vencido["texto"], vencido.get("fontes", [])

    registrar(f"  {nome}: 0 resultado(s)")
    return "", []


def testar_fontes(client: genai.Client, log: Optional[Log] = None) -> dict[str, bool]:
    """Health check por fonte. Um Google Search OK fecha o circuito na hora."""
    registrar = log or _sem_log
    estado = cache.carregar()
    resultado: dict[str, bool] = {}

    for nome, funcao in (("Jooble", _buscar_jooble), ("Adzuna", _buscar_adzuna)):
        try:
            achadas = funcao("DevOps Junior", 5)
        except Exception as e:  # noqa: BLE001
            achadas = []
            registrar(f"  {nome}: FALHA ({_redigir_segredos(f'{type(e).__name__}')})")
        resultado[nome] = bool(achadas)
        if achadas:
            registrar(f"  {nome}: OK ({len(achadas)} anúncio(s))")
        elif nome not in resultado or not resultado[nome]:
            registrar(f"  {nome}: sem resultados ou credencial ausente")

    try:
        client.models.generate_content(
            model=MODELO_BUSCA,
            contents="Responda apenas: ok",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                http_options=types.HttpOptions(timeout=TIMEOUT_BUSCA_MS),
            ),
        )
        resultado["Google Search"] = True
        cache.registrar_sucesso(estado, "Google Search")
        registrar("  Google Search: OK — circuito fechado")
    except Exception as e:  # noqa: BLE001
        resultado["Google Search"] = False
        registrar(f"  Google Search: FALHA ({type(e).__name__}) — circuito mantido")

    try:
        _, fontes = _busca_metasearch("vaga DevOps Junior remoto Brasil", 10)
    except Exception:  # noqa: BLE001
        fontes = []
    resultado["DDGS"] = bool(fontes)
    registrar(f"  Metabusca DDGS: {'OK' if fontes else 'sem resultados'} ({len(fontes)})")

    cache.salvar(estado)
    return resultado


def buscar_vagas(
    client: genai.Client,
    cv_base: str,
    pedido: str,
    limite: int = 10,
    modelo_analise: str = "lite",
    log: Optional[Log] = None,
    usar_cache: bool = True,
) -> list[VagaEncontrada]:
    """Pesquisa a web e devolve vagas com descrição e URL para triagem.

    `log` recebe uma linha por fonte. Sem ele a busca fica muda — e uma fonte
    fora do ar (cota do Google Search estourada, por exemplo) passa despercebida.
    """
    registrar = log or _sem_log
    hoje = date.today().isoformat()
    # As fontes são ruidosas. Coletamos um conjunto maior para que vagas ruins
    # não ocupem as posições que o usuário pediu.
    limite_coleta = min(50, max(20, limite * 5))

    # O estado real é sempre carregado, inclusive com --sem-cache: a flag manda
    # ignorar RESULTADOS gravados, não esquecer que a cota do Google Search está
    # esgotada. Com um dicionário vazio aqui, --sem-cache chamava a fonte no 429
    # a cada execução e nunca registrava a falha.
    estado_cache = cache.carregar()
    consulta_cache = f"{pedido}|{limite_coleta}"

    vagas: list[VagaEncontrada] = []
    for nome, funcao in (("Jooble", _buscar_jooble), ("Adzuna", _buscar_adzuna)):
        vagas.extend(
            _fonte_estruturada(
                nome, funcao, pedido, limite_coleta,
                estado_cache, consulta_cache, usar_cache, registrar,
            )
        )

    prompt = f"""
Hoje é {hoje}. Encontre até {limite_coleta} vagas de emprego ATUALMENTE ABERTAS que façam
sentido para o candidato abaixo e atendam ao pedido dele.

PEDIDO:
{pedido}

CV DO CANDIDATO:
{cv_base}

Pesquise anúncios publicados ou atualizados recentemente. Priorize a página oficial da
empresa, Gupy, LinkedIn, Indeed, InHire e portais confiáveis. Para cada vaga, obtenha:
título, empresa, descrição suficiente para avaliar requisitos, regime/localização,
senioridade, URL específica do anúncio, origem e data de publicação quando disponível.
Não inclua páginas genéricas de busca, listas de vagas, anúncios encerrados, vagas sem URL
verificável nem oportunidades que claramente não aceitam alguém localizado no Brasil.
Elimine cargos Pleno, Sênior, Staff, Lead, Principal, Arquiteto, Especialista e Gerente
antes de responder. Priorize títulos com Estágio, Estagiário, Trainee, Júnior ou Jr e
descrições que aceitem até 2 anos de experiência. Não invente dados ausentes. Apresente os
achados com links.
"""
    texto_livre = ""
    fontes: list[str] = []
    horas_restantes = cache.circuito_aberto(estado_cache, "Google Search")
    if horas_restantes is not None:
        registrar(
            f"  Google Search: CIRCUITO ABERTO — cota esgotada, nova tentativa em "
            f"{horas_restantes:.0f} h. Rode 'triar buscar --testar-fontes' para reabrir agora."
        )
    else:
        try:
            descoberta = client.models.generate_content(
                model=MODELO_BUSCA,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[
                        types.Tool(google_search=types.GoogleSearch()),
                        types.Tool(url_context=types.UrlContext()),
                    ],
                    http_options=types.HttpOptions(timeout=TIMEOUT_BUSCA_MS),
                ),
            )
            texto_livre = texto_da_resposta(descoberta)
            fontes = _fontes_grounding(descoberta)
            cache.registrar_sucesso(estado_cache, "Google Search")
            registrar(f"  Google Search: {len(fontes)} fonte(s) citada(s)")
        except Exception as e:  # noqa: BLE001
            abriu = cache.registrar_falha(estado_cache, "Google Search")
            sufixo = (
                f" — {cache.FALHAS_PARA_ABRIR} falhas seguidas, pulando a fonte por "
                f"{cache.HORAS_CIRCUITO_ABERTO} h"
                if abriu else ""
            )
            registrar(
                f"  Google Search: indisponível "
                f"({_redigir_segredos(f'{type(e).__name__}')}){sufixo}"
            )

    # Também cai no fallback quando o grounding responde vazio, não só quando falha.
    if not texto_livre.strip():
        texto_livre, fontes = _texto_livre_com_cache(
            "Metabusca DDGS",
            lambda: _busca_metasearch(pedido, limite_coleta),
            estado_cache, consulta_cache, usar_cache, registrar,
        )

    if texto_livre.strip():
        try:
            vagas.extend(
                _normalizar_texto_livre(
                    client, modelo_analise, texto_livre, fontes, limite_coleta, registrar
                )
            )
        except Exception as e:  # noqa: BLE001
            registrar(f"  Normalização do texto livre falhou ({type(e).__name__})")

    # Grava sempre: mesmo com --sem-cache os resultados frescos renovam o cache e
    # as falhas desta execução precisam contar para o circuito.
    try:
        cache.salvar(estado_cache)
    except OSError as e:
        registrar(f"  Aviso: não foi possível gravar o cache de busca ({type(e).__name__})")

    if not vagas:
        return []

    candidatas = _selecionar_candidatas(vagas, limite * 3, registrar)
    return _validar_links(candidatas, limite, registrar)
