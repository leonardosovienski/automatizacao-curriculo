"""Busca vagas atuais combinando APIs de emprego, Google Search Grounding e metabusca.

Princípio da camada: **fonte estruturada não passa por modelo**. Jooble e Adzuna já
devolvem título/empresa/localização/link/data em JSON — converter isso com um LLM só
introduz perda e alucinação (uma execução real perdeu 100% dos 29 anúncios da Jooble
porque o modelo resumiu a entrada). O modelo é usado apenas onde a entrada é texto
livre: resultados do Google Search Grounding e da metabusca DDGS.
"""

import html
import ipaddress
import json
import os
import random
import re
import socket
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from ddgs import DDGS
from google import genai
from google.genai import types
from pydantic import ValidationError

from . import ats, cache, replay
from .analisador import (
    MODELOS,
    TIMEOUT_BUSCA_MS,
    gerar_com_retentativa,
    texto_da_resposta,
)
from .schema import ResultadoBusca, VagaEncontrada

# Grounding de Google Search não tem cota no tier gratuito nas famílias 2.0 e 3.x:
# toda chamada com a ferramenta volta 429 RESOURCE_EXHAUSTED, mesmo com chave nova e
# zero uso. Medido em 2026-07-27 contra 2.0-flash-lite, flash-lite-latest, 2.0-flash,
# 3-flash-preview, flash-latest, 3.1-flash-lite e 3.5-flash — só o 2.5-flash passou.
# É uma chamada por busca; a análise por vaga continua no modelo de MODELOS["lite"].
MODELO_BUSCA = "gemini-2.5-flash"

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
    # Medido na Fase 3: a BairesDev anuncia "Work From Home Junior DevOps" e a vaga
    # era reprovada pela praça declarada, com o regime escrito no próprio título.
    "work from home",
    "trabalho remoto",
    "totalmente remoto",
    "totalmente remota",
    "wfh",
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


def _resumo_erro(e: BaseException, limite: int = 240) -> str:
    """Uma linha com tipo + motivo, redigida e curta o bastante para caber no log.

    Sem a mensagem, um 429 de cota e um 404 de modelo inexistente aparecem os dois
    como `ClientError` — e é justamente a diferença entre esperar o circuito fechar
    e ter de corrigir o código. O payload de erro da API vem como um JSON de várias
    linhas, então achatamos e truncamos.
    """
    mensagem = " ".join(str(e).split())
    if len(mensagem) > limite:
        mensagem = mensagem[:limite].rstrip() + "…"
    texto = f"{type(e).__name__}: {mensagem}" if mensagem else type(e).__name__
    return _redigir_segredos(texto)


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


# ------------------------------------------------------- alfândega de URL
#
# Barreira determinística e sem rede, aplicada antes de qualquer requisição ou
# chamada de LLM. Medido na validação de 2026-07-27: `https://solides.com.br`
# (domínio nu) e `https://encontreumnerd.com.br/cadastro-prestador` (formulário de
# cadastro de freelancer) chegaram à análise paga e receberam 59/100 e 58/100 — o
# modelo espremeu uma home page tentando achar vaga que não existe.

CAMINHOS_NAO_VAGA = (
    "/login",
    "/signin",
    "/sign-in",
    "/cadastro",
    "/signup",
    "/sign-up",
    "/register",
    "/auth",
    "/prestador",
    "/conta",
    "/account",
    "/politica",
    "/privacidade",
    "/termos",
    "/quem-somos",
    "/sobre-nos",
    "/contato",
)

# Marcadores de caminho que identificam página de anúncio em português e inglês.
PADRAO_CAMINHO_ANUNCIO = re.compile(
    r"(?:^|/)(?:jobs?|vagas?|vaga_emprego|empregos?|carreiras?|careers?"
    r"|oportunidades?|positions?|opening|jdp|details?|anuncio)(?:/|$|[-_])"
)


def _e_encurtador(host: str) -> bool:
    limpo = (host or "").lower().removeprefix("www.")
    return any(limpo == alvo or limpo.endswith(f".{alvo}") for alvo in HOSTS_NAO_CANONICOS)


# Último segmento que é só a palavra-marcador, sem identificador nenhum: é a página
# de listagem do portal, não um anúncio. Medido em 2026-07-27 — o GeekHunter
# redireciona a página da vaga para `/pt/vagas`, e sem esta regra esse endereço
# vira `link_final` de todas as vagas do portal, fundindo-as pela Camada A.
SEGMENTOS_DE_LISTAGEM = frozenset(
    {
        "vaga", "vagas", "emprego", "empregos", "job", "jobs", "career", "careers",
        "carreira", "carreiras", "oportunidade", "oportunidades", "positions",
        "busca", "buscar", "search", "resultados", "results", "pt", "br", "en",
    }
)


def _path_parece_anuncio(caminho: str) -> bool:
    """Id numérico, marcador conhecido, slug com várias palavras ou caminho aninhado.

    Deliberadamente permissivo no geral: aqui o erro caro é o falso negativo, que
    apaga uma vaga boa antes de qualquer um olhar para ela. O trabalho pesado é
    feito pelas duas regras anteriores (domínio nu e blacklist). `gupy.io/v/1` é
    forma legítima de portal e precisa passar.

    A exceção é a página de listagem, que precisa ser reprovada mesmo tendo vários
    segmentos — ela não identifica anúncio nenhum e contamina a chave de dedup.
    """
    segmentos = [parte for parte in caminho.split("/") if parte]
    if not segmentos:
        return False
    if segmentos[-1] in SEGMENTOS_DE_LISTAGEM:
        return False
    if re.search(r"\d{4,}", caminho):
        return True
    if PADRAO_CAMINHO_ANUNCIO.search(caminho):
        return True
    if len(segmentos) >= 2:
        return True
    return segmentos[-1].count("-") >= 2


def _url_de_vaga_plausivel(url: str) -> bool:
    """False para o que não pode ser a página de um anúncio específico."""
    partes = urlsplit(url or "")
    if partes.scheme not in ("http", "https") or not partes.netloc:
        return False
    caminho = (partes.path or "").rstrip("/")
    baixo = caminho.lower()
    if any(marcador in baixo for marcador in CAMINHOS_NAO_VAGA):
        return False
    if len(caminho) <= 1:
        return False  # domínio nu: institucional, nunca anúncio
    # Encurtador tem id opaco no path e o destino real só é conhecido depois do
    # redirect — não pode ser reprovado por "não parecer anúncio".
    if _e_encurtador(partes.netloc) and re.fullmatch(r"/[A-Za-z0-9_-]{4,}", caminho):
        return True
    # Greenhouse e afins põem o id da vaga na query: /careers/job-detail?gh_jid=5594102
    if partes.query and re.search(r"\d{4,}", partes.query):
        return True
    return _path_parece_anuncio(baixo)


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

    **Exceção medida na Fase 3:** o título do próprio anúncio também vale como
    declaração. "Work From Home Junior DevOps" da BairesDev era reprovada porque a
    Adzuna carimbou "Rio de Janeiro" — com o regime escrito na primeira linha do
    título. O título é a manchete que o anunciante escolheu, não uma palavra solta
    perdida no corpo do texto; abrir para a descrição inteira é que reabriria a
    porta que este filtro fechou.
    """
    local = _normalizar(vaga.localizacao)
    if _remoto_afirmado(_normalizar(vaga.titulo)):
        return False
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
    # Medido na Fase 3: a nerdin.com.br devolve 200 numa vaga desativada há 250
    # dias e só avisa no corpo. Sem estes marcadores o anúncio morto passa como
    # ativo — e quando a fonte omite `publicada_em`, DIAS_MAXIMOS_ANUNCIO também
    # não tem o que comparar, então esta é a única barreira que sobra.
    "nao estamos aceitando novas candidaturas",
    "nao estamos mais aceitando candidaturas",
    "vaga desativada",
    "vaga expirada",
    "esta vaga expirou",
    "candidaturas encerradas",
    "processo seletivo encerrado",
    "this job has expired",
    "this position has been filled",
)

# Limite do texto extraído da página do anúncio. O suficiente para os requisitos
# completos, sem inflar o prompt de análise com rodapé e menu do portal.
MAX_DESCRICAO_ENRIQUECIDA = 6000


class PermissaoRobots(Exception):
    """O robots.txt do host proíbe buscar esta URL. Não é falha do anúncio."""


# Domínios que não hospedam anúncio: são roteadores que existem só para redirecionar.
# O Google Search Grounding devolve todos os links por baixo de vertexaisearch, e o
# robots.txt de lá proíbe tudo — o que deixaria toda vaga vinda do grounding presa a
# uma URL temporária, sem link real de candidatura e sem JSON-LD.
#
# A exceção é estreita de propósito: seguimos o `Location` sem baixar corpo nem
# indexar nada, e o host de destino passa pelo robots.txt dele normalmente. Completar
# o trajeto de rede não é raspar conteúdo; ler a página do LinkedIn seria, e por isso
# o LinkedIn continua bloqueado.
HOSTS_ROUTER_DE_REDIRECT = ("vertexaisearch.cloud.google.com",)
MAX_SALTOS_ROUTER = 4


def _e_router_de_redirect(url: str) -> bool:
    host = urlsplit(url or "").netloc.lower().removeprefix("www.")
    return any(host == alvo or host.endswith(f".{alvo}") for alvo in HOSTS_ROUTER_DE_REDIRECT)


def _resolver_router(url: str) -> str:
    """Segue os redirects de um roteador conhecido sem baixar o corpo das páginas."""
    atual = url
    for _ in range(MAX_SALTOS_ROUTER):
        if not _e_router_de_redirect(atual):
            return atual
        _esperar_vez(urlsplit(atual).netloc.lower())
        destino = ""
        for metodo in (httpx.head, httpx.get):
            try:
                resposta = metodo(
                    atual, follow_redirects=False, timeout=10, headers=CABECALHOS_NAVEGADOR
                )
            except Exception:  # noqa: BLE001 — roteador fora do ar devolve a URL original
                return atual
            destino = resposta.headers.get("location", "")
            if destino:
                break
            # Alguns roteadores só emitem o Location no GET; se nem assim vier, o
            # corpo é descartado sem leitura e a URL original é preservada.
        if not destino:
            return atual
        atual = urljoin(atual, destino)
    return atual


# ------------------------------------------------- schema.org/JobPosting
#
# A página do anúncio publica, em JSON-LD, exatamente os campos que o pipeline vinha
# pedindo a um LLM adivinhar. Medido em 2026-07-27: a Adzuna traz `hiringOrganization`,
# `datePosted`, `validThrough`, `jobLocationType` e uma `description` de 1.502–2.815
# chars, contra os 500 truncados da API. É dado do empregador, não renderização do
# portal — por isso vence qualquer heurística de texto.

# Rótulos que ocupam o lugar do empregador sem identificar ninguém. `code` é real:
# é o que a Nerdin devolve em `hiringOrganization` numa vaga de anunciante anônimo.
NOMES_EMPRESA_IMPLAUSIVEIS = frozenset(
    {
        "code",
        "empresa",
        "empresa confidencial",
        "confidencial",
        "anonima",
        "anonimo",
        "empregador",
        "cliente",
        "n a",
        "none",
        "null",
        "nao informado",
    }
)


@dataclass
class Anuncio:
    """Campos autoritativos extraídos do JSON-LD. Vazio significa ausente, não falso."""

    empresa: str = ""
    empresa_confiavel: bool = False
    remoto: Optional[bool] = None
    localidade: str = ""
    publicada_em: str = ""
    expira_em: str = ""
    descricao: str = ""

    def vazio(self) -> bool:
        return not any(
            (self.empresa, self.remoto is not None, self.publicada_em,
             self.expira_em, self.descricao)
        )


def _empresa_do_jsonld_confiavel(nome: str, host: str) -> bool:
    """`alta` é conquistada, não presumida pelo formato do dado.

    Dois testes: o nome não pode ser o do próprio portal que hospeda o anúncio
    (agregador se declarando empregador) nem um rótulo genérico de anunciante
    anônimo. Sem isso, trocaríamos a alucinação do LLM por lixo bem formatado.
    """
    normalizado = _normalizar(nome)
    if len(normalizado) < 3:
        return False
    if normalizado in NOMES_EMPRESA_IMPLAUSIVEIS:
        return False
    if _empresa_e_portal(normalizado):
        return False
    # A coincidência entre nome e domínio só é suspeita quando o host é um
    # agregador — aí significa que o portal se declarou empregador. No site de
    # carreiras da própria empresa ela é o oposto: `avepoint.com` publicando
    # "AvePoint" é a confirmação mais forte que existe, e a regra anterior
    # reprovava justamente os casos mais confiáveis.
    limpo = (host or "").lower().removeprefix("www.")
    e_agregador = any(
        limpo == portal or limpo.endswith(f".{portal}")
        for portal in PORTAIS_BRASILEIROS + PORTAIS_INTERNACIONAIS + HOSTS_NAO_CANONICOS
    )
    if not e_agregador:
        return True
    dominio = limpo.split(".")[0]
    return not (dominio and dominio in normalizado.split())


def _blocos_jsonld(html_bruto: str):
    for bloco in re.findall(
        r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html_bruto or "", re.S | re.I
    ):
        try:
            dado = json.loads(bloco)
        except ValueError:
            continue
        pilha = dado if isinstance(dado, list) else [dado]
        while pilha:
            item = pilha.pop()
            if not isinstance(item, dict):
                continue
            # Alguns portais aninham tudo num @graph.
            grafo = item.get("@graph")
            if isinstance(grafo, list):
                pilha.extend(grafo)
            yield item


def _anuncio_de_contrato(dados: dict, host: str = "") -> Anuncio:
    """Converte o contrato comum num `Anuncio`.

    Um só mapeamento para as duas origens — o `schema.org/JobPosting` lido da página
    e o payload de um adapter de ATS (`triagem/ats.py`), que devolve as mesmas
    chaves de propósito. Dois mapeamentos separados divergiriam com o tempo, e a
    divergência apareceria como campo faltando numa das origens.
    """
    organizacao = dados.get("hiringOrganization")
    nome = ""
    if isinstance(organizacao, dict):
        nome = str(organizacao.get("name") or "").strip()
    elif isinstance(organizacao, str):
        nome = organizacao.strip()

    tipo_local = str(dados.get("jobLocationType") or "").upper()

    return Anuncio(
        empresa=nome,
        empresa_confiavel=bool(nome) and _empresa_do_jsonld_confiavel(nome, host),
        remoto=True if "TELECOMMUTE" in tipo_local else None,
        localidade=str(dados.get("addressLocality") or "").strip(),
        publicada_em=str(dados.get("datePosted") or "").strip(),
        expira_em=str(dados.get("validThrough") or "").strip(),
        descricao=_texto_visivel(str(dados.get("description") or "")),
    )


def _extrair_jobposting(html_bruto: str, host: str = "") -> Anuncio:
    """Lê o schema.org/JobPosting da página. Campo ausente fica vazio, nunca inferido."""
    for item in _blocos_jsonld(html_bruto):
        if "JobPosting" not in str(item.get("@type", "")):
            continue

        # No schema.org a localidade fica aninhada; o contrato comum a quer plana.
        localidade = ""
        local = item.get("jobLocation")
        primeiro = local[0] if isinstance(local, list) and local else local
        if isinstance(primeiro, dict):
            endereco = primeiro.get("address")
            if isinstance(endereco, dict):
                localidade = str(endereco.get("addressLocality") or "").strip()

        return _anuncio_de_contrato({**item, "addressLocality": localidade}, host)
    return Anuncio()


def _expirado(expira_em: str) -> bool:
    """`validThrough` no passado é descarte determinístico, sem consultar o LLM.

    Medido na Nerdin: `validThrough: 2025-12-09`, sete meses no passado, numa página
    que responde 200 e cuja fonte não declarou `publicada_em` — então nem o
    DIAS_MAXIMOS_ANUNCIO tinha o que comparar. Este é o único sinal confiável.
    """
    if not (expira_em or "").strip():
        return False
    dias = _dias_desde(expira_em)
    return dias is not None and dias > 0


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
    anuncio: Anuncio = field(default_factory=Anuncio)


# O User-Agent auto-declarado como bot levava 403 da Adzuna — a fonte com o melhor
# material da busca. Medido em 2026-07-27: 403/511 chars com o UA antigo, 200/3397
# chars com cabeçalho de navegador, e é lá que está o JSON-LD com empresa, data e
# regime autoritativos. O token de identificação fica no fim, para que o operador do
# site consiga reconhecer e bloquear o tráfego se quiser.
TOKEN_AGENTE = "TriagemVagas"
CABECALHOS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 {TOKEN_AGENTE}/2.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# Intervalo mínimo entre duas requisições ao mesmo host. `_validar_links` dispara
# até 8 threads em paralelo: sem o freio, um portal recebe 8 requisições no mesmo
# instante — que é o padrão que faz um site classificar o tráfego como ataque.
INTERVALO_MINIMO_POR_HOST = 1.5
_ultimo_acesso: dict[str, float] = {}
_trava_acesso = threading.Lock()


def _esperar_vez(host: str) -> None:
    """Serializa o acesso por host, dormindo o que faltar do intervalo mínimo."""
    with _trava_acesso:
        agora = time.monotonic()
        proximo = _ultimo_acesso.get(host, 0.0) + INTERVALO_MINIMO_POR_HOST
        espera = max(0.0, proximo - agora)
        _ultimo_acesso[host] = agora + espera
    if espera:
        time.sleep(espera)


@lru_cache(maxsize=128)
def _robots(base: str) -> Optional[RobotFileParser]:
    """robots.txt do host, ou None quando não há (o que libera o acesso)."""
    try:
        resposta = httpx.get(
            f"{base}/robots.txt", timeout=5, headers=CABECALHOS_NAVEGADOR, follow_redirects=False
        )
    except Exception:  # noqa: BLE001 — robots indisponível não bloqueia a busca
        return None
    if resposta.status_code != 200:
        return None
    leitor = RobotFileParser()
    leitor.parse(resposta.text.splitlines())
    return leitor


def _permitido_por_robots(url: str) -> bool:
    partes = urlsplit(url or "")
    if not partes.netloc or not _host_e_seguro(partes.hostname or ""):
        return False
    leitor = _robots(f"{partes.scheme}://{partes.netloc}")
    if leitor is None:
        return True
    try:
        return leitor.can_fetch(TOKEN_AGENTE, url)
    except Exception:  # noqa: BLE001 — robots malformado não bloqueia a busca
        return True


@lru_cache(maxsize=512)
def _host_e_seguro(host: str) -> bool:
    """Aceita apenas hosts que resolvem exclusivamente para IPs globais.

    Links de agregadores são dados não confiáveis. Bloquear loopback, redes privadas
    e link-local evita que a validação de uma vaga alcance serviços locais ou
    metadados de cloud. Todos os A/AAAA precisam ser globais para não escolher
    arbitrariamente entre respostas DNS seguras e inseguras.
    """
    nome = (host or "").strip().strip("[]").lower()
    if not nome or nome == "localhost":
        return False
    try:
        return ipaddress.ip_address(nome).is_global
    except ValueError:
        pass
    try:
        respostas = socket.getaddrinfo(nome, None, type=socket.SOCK_STREAM)
        enderecos = {resposta[4][0] for resposta in respostas}
        return bool(enderecos) and all(ipaddress.ip_address(ip).is_global for ip in enderecos)
    except (OSError, ValueError):
        return False


def _url_de_rede_segura(url: str) -> bool:
    """Valida esquema, credenciais e destino antes de cada salto de rede."""
    partes = urlsplit(url or "")
    return (
        partes.scheme in ("http", "https")
        and not partes.username
        and not partes.password
        and _host_e_seguro(partes.hostname or "")
    )


def _obter(link: str):
    """GET seguro, com redirects manuais e revalidação de cada destino."""
    destino = link
    for _ in range(6):
        if not _url_de_rede_segura(destino):
            raise httpx.InvalidURL("destino de rede não permitido")
        if not _permitido_por_robots(destino):
            raise PermissaoRobots(destino)
        _esperar_vez(urlsplit(destino).netloc.lower())
        resposta = httpx.get(
            destino, follow_redirects=False, timeout=15, headers=CABECALHOS_NAVEGADOR
        )
        if resposta.status_code not in (301, 302, 303, 307, 308):
            return resposta
        location = resposta.headers.get("location")
        if not location:
            return resposta
        destino = urljoin(destino, location)
    raise httpx.TooManyRedirects("muitos redirects ao validar vaga")


def _inspecionar_link(vaga: VagaEncontrada) -> Inspecao:
    """Remove expiração clara e host morto, preservando sites que bloqueiam bots.

    Erro de rede ganha uma segunda tentativa: falhar na primeira pode ser um soluço
    de DNS/TLS, mas falhar duas vezes seguidas é sinal de anúncio fora do ar. Erros
    que não são de rede (URL inválida, IDNA) não são culpa do anúncio — mantemos.
    """
    # Roteador de redirect é resolvido antes de tudo: o que vale é o host de
    # destino, tanto para o robots.txt quanto para a chave de dedup.
    link = _limpar_url(_resolver_router(vaga.link)) if _e_router_de_redirect(vaga.link) else vaga.link
    roteado = link != vaga.link

    try:
        response = _obter(link)
    except PermissaoRobots:
        # O site pediu para não ser buscado. Não é sinal de anúncio fora do ar:
        # preservamos a vaga, apenas sem enriquecimento. O link resolvido ainda
        # vale — é o endereço real de candidatura, e serve de chave de dedup.
        return Inspecao(ativo=True, url_final=link if roteado else "")
    except httpx.RequestError:
        try:
            response = _obter(link)
        except PermissaoRobots:
            return Inspecao(ativo=True, url_final=link if roteado else "")
        except httpx.RequestError:
            return Inspecao(ativo=False)
    except Exception:
        # httpx.InvalidURL e erros de IDNA não herdam de HTTPError e antes
        # derrubavam a busca inteira.
        return Inspecao(ativo=True, url_final=link if roteado else "")
    if response.status_code in (401, 403, 429):
        return Inspecao(ativo=True, url_final=link if roteado else "")
    if response.status_code >= 400:
        return Inspecao(ativo=False)

    original = urlsplit(link)
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
    # Roteamento de ATS antes do JSON-LD: quando a vaga é hospedada num ATS com API
    # pública, o dado vem da fonte do empregador em vez de ser lido da página. Falha
    # aqui não interrompe nada — `rotear` devolve None e a esteira segue no fluxo
    # legado (JSON-LD e, por último, o texto livre).
    dados_ats = ats.rotear(link, bruto)
    if dados_ats:
        anuncio = _anuncio_de_contrato(dados_ats, urlsplit(url_final or link).netloc)
        if _expirado(anuncio.expira_em):
            replay.gravar("descarte_expirada", link, bruto, {"origem_dados": "API_GREENHOUSE"})
            return Inspecao(ativo=False, url_final=url_final)
        return Inspecao(ativo=True, url_final=url_final, texto_pagina=bruto, anuncio=anuncio)

    anuncio = _extrair_jobposting(bruto, urlsplit(url_final or link).netloc)
    # `validThrough` no passado encerra aqui: nem enriquecimento, nem LLM.
    if _expirado(anuncio.expira_em):
        replay.gravar("descarte_expirada", link, bruto, {"validThrough": anuncio.expira_em})
        return Inspecao(ativo=False, url_final=url_final)
    # HTML só nos caminhos suspeitos: página que responde 200 mas não publica
    # JobPosting é onde a extração erra, e é o que precisa ser reproduzível.
    if anuncio.vazio():
        replay.gravar("sem_jsonld", link, bruto, {"status": response.status_code})
    return Inspecao(ativo=True, url_final=url_final, texto_pagina=bruto, anuncio=anuncio)


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


def _aplicar_jobposting(vaga: VagaEncontrada, anuncio: Anuncio) -> VagaEncontrada:
    """Precedência: JSON-LD > campo estruturado da API > o que já estava lá.

    Nunca preenche a partir de nada: campo ausente no JSON-LD deixa o valor
    anterior intacto, e o LLM segue proibido de inventar qualquer um destes.
    """
    if anuncio.vazio():
        return vaga
    mudancas: dict = {}
    if anuncio.empresa and anuncio.empresa_confiavel:
        mudancas["empresa"] = anuncio.empresa
        mudancas["confianca_empresa"] = "alta"
    elif anuncio.empresa and not anuncio.empresa_confiavel:
        # Nome presente mas reprovado (portal ou rótulo genérico): não herda nada,
        # e o que veio do LLM perde a pouca confiança que tinha.
        mudancas["empresa"] = "Desconhecida"
        mudancas["confianca_empresa"] = "baixa"
    if anuncio.remoto:
        mudancas["localizacao"] = f"{anuncio.localidade} (Remoto)".strip() if anuncio.localidade else "Remoto"
    elif anuncio.localidade and not _normalizar(vaga.localizacao):
        mudancas["localizacao"] = anuncio.localidade
    if anuncio.publicada_em and not (vaga.publicada_em or "").strip():
        mudancas["publicada_em"] = anuncio.publicada_em
    if len(anuncio.descricao) > len(vaga.descricao):
        mudancas["descricao"] = anuncio.descricao[:MAX_DESCRICAO_ENRIQUECIDA]
        mudancas["descricao_completa"] = True
    return vaga.model_copy(update=mudancas) if mudancas else vaga


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
    estruturadas = 0
    redirect_generico = 0
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
        # A alfândega vale para o destino do redirect também. Medido em 2026-07-27:
        # o GeekHunter redireciona a página da vaga para `/pt/vagas`, a listagem
        # genérica. Como `chave_dedup()` devolve `link_final or link`, duas vagas
        # diferentes do mesmo portal virariam a mesma chave — e a Camada A as
        # fundiria em silêncio, que é o pior erro possível nesta cascata.
        url_final = inspecao.url_final if _url_de_vaga_plausivel(inspecao.url_final) else ""
        if inspecao.url_final and not url_final:
            redirect_generico += 1
        atualizada = vaga.model_copy(update={"link_final": url_final})
        antes = len(atualizada.descricao)
        atualizada = _aplicar_jobposting(atualizada, inspecao.anuncio)
        if not inspecao.anuncio.vazio():
            estruturadas += 1
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
    if estruturadas:
        log(f"  dados autoritativos via schema.org/JobPosting: {estruturadas} vaga(s)")
    if redirect_generico:
        log(f"  redirect caiu em página genérica, link anunciado mantido: {redirect_generico}")
    return ativas[:limite]


def _selecionar_candidatas(
    vagas: list[VagaEncontrada], limite: int, log: Log = _sem_log
) -> list[VagaEncontrada]:
    """Remove lixo/senioridade/vaga vencida, deduplica e ordena antes da triagem cara."""
    cortes = {
        "area": 0, "senioridade": 0, "antiga": 0, "local": 0,
        "nao_anuncio": 0, "url_invalida": 0, "duplicada": 0,
    }
    # Guarda a pontuação junto da vaga: ela já foi calculada para decidir o corte e
    # recalculá-la na ordenação repetia todo o trabalho de normalização e regex.
    pontuadas: list[tuple[int, VagaEncontrada]] = []
    for vaga in vagas:
        if not _host_de_anuncio(vaga):
            cortes["nao_anuncio"] += 1
            continue
        # Antes de gastar requisição ou token: a URL sequer pode ser um anúncio?
        if not _url_de_vaga_plausivel(vaga.link):
            cortes["url_invalida"] += 1
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
            "informações. Deixe `localizacao` VAZIO quando o anúncio não declarar a "
            "praça ou o regime — nunca deduza 'Remoto' da ausência de cidade. Em "
            "`empresa`, use o nome do empregador, nunca o do site que hospeda o "
            "anúncio; se o anunciante for anônimo, deixe vazio.\n\n"
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
    locais_apagados = 0
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
        antes_local = vaga.localizacao
        vaga = _ancorar_localizacao(vaga, origem_normalizada)
        if antes_local and not vaga.localizacao:
            locais_apagados += 1
        vagas.append(vaga)
    if invalidas:
        log(f"  metabusca: {invalidas} item(ns) fora do schema descartado(s)")
    if inferidas:
        log(f"  metabusca: {inferidas} empresa(s) sem respaldo no texto → 'Desconhecida'")
    if locais_apagados:
        log(f"  metabusca: {locais_apagados} localização(ões) sem respaldo no texto → vazio")
    return vagas


# O nome do portal aparece no material de origem tanto quanto o do empregador, então
# a ancoragem por si só o aprova. Medido na Fase 3: "Nerdin Vagas de TI" (o site) foi
# gravada como empregadora de uma vaga cujo anunciante real é anônimo; "Buscar Vagas |
# Emprego" e "Caderno Nacional" apareceram no mesmo lote.
MARCADORES_PORTAL_EMPRESA = (
    "vaga",
    "vagas",
    "emprego",
    "empregos",
    "empregos com",
    "classificados",
    "banco de talentos",
    "caderno nacional",
    "job board",
    "jobs board",
    "quadro de vagas",
)


def _empresa_e_portal(empresa_normalizada: str) -> bool:
    """O 'empregador' é na verdade o site que hospeda o anúncio."""
    return _contem_termo(empresa_normalizada, MARCADORES_PORTAL_EMPRESA)


# Vizinhança, em caracteres, considerada "o material daquele anúncio" dentro do blob
# de texto livre. O blob traz dezenas de vagas coladas: procurar "remoto" nele inteiro
# aprova qualquer coisa, porque alguma outra vaga da lista é remota.
JANELA_ANCORA = 600


def _trecho_de_origem(vaga: VagaEncontrada, origem_normalizada: str) -> str:
    """Recorta o pedaço do texto livre que fala desta vaga, e não das vizinhas."""
    for agulha in (_normalizar(vaga.link), _normalizar(vaga.titulo)):
        if not agulha:
            continue
        posicao = origem_normalizada.find(agulha)
        if posicao >= 0:
            inicio = max(0, posicao - JANELA_ANCORA)
            return origem_normalizada[inicio : posicao + len(agulha) + JANELA_ANCORA]
    return ""


def _ancorar_localizacao(vaga: VagaEncontrada, origem_normalizada: str) -> VagaEncontrada:
    """Localização sem respaldo no material de origem vira vazio, não palpite.

    Medido na Fase 3: a vaga `Junior DevOps Engineer` da AvePoint saiu do texto
    livre com `localizacao: "Remoto"` e virou a recomendação #1 com D2 10/10 — o
    anúncio real é presencial em Da Nang, no Vietnã, e nunca menciona remoto. A
    descrição gravada tinha 107 caracteres e era paráfrase do modelo, não o texto
    do anúncio: não havia de onde tirar a localização, e ele preencheu assim mesmo.

    Campo vazio é honesto e o D2 pune a ausência de informação. Campo inventado é
    premiado. Por isso o default aqui é apagar, não manter.
    """
    local = _normalizar(vaga.localizacao)
    if not local:
        return vaga
    trecho = _trecho_de_origem(vaga, origem_normalizada) or origem_normalizada
    if _remoto_afirmado(local):
        # "Remoto" é a afirmação mais cara do schema: o D2 tem regra fixa de 10/10.
        if not _remoto_afirmado(trecho):
            return vaga.model_copy(update={"localizacao": ""})
        return vaga
    significativas = [
        palavra for palavra in local.split() if len(palavra) > 3 and palavra not in LOCAL_GENERICO
    ]
    if significativas and not any(palavra in trecho for palavra in significativas):
        return vaga.model_copy(update={"localizacao": ""})
    return vaga


def _ancorar_empresa(vaga: VagaEncontrada, origem_normalizada: str) -> VagaEncontrada:
    """Se o nome da empresa não aparece no material de origem, o modelo o inventou.

    Regressão real: a mesma URL saiu como "Casado.dev", "Sylision" e "Desconhecida"
    em execuções diferentes. Checagem determinística — o modelo não é consultado
    sobre a própria confiança, porque ele erra isso também.
    """
    empresa = _normalizar(vaga.empresa)
    if not empresa:
        return vaga.model_copy(update={"empresa": "Desconhecida", "confianca_empresa": "baixa"})
    if _empresa_e_portal(empresa):
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
        registrar(f"  {nome}: indisponível ({_resumo_erro(e)})")
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
            registrar(f"  {nome}: FALHA ({_resumo_erro(e)})")
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
        registrar(f"  Google Search: FALHA ({_resumo_erro(e)}) — circuito mantido")

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
                    # `url_context` saiu daqui. Ele existia para o modelo abrir a
                    # página do anúncio e ler os detalhes — trabalho que agora é feito
                    # por `_extrair_jobposting`, com o schema.org publicado pelo
                    # empregador em vez de leitura interpretada. E ele passou a
                    # derrubar a fonte: medido em 2026-07-27,
                    # `400 INVALID_ARGUMENT — Number of urls to lookup exceeds the
                    # limit (21 > 20)`. O teto é do próprio tool e quem escolhe as
                    # URLs é o modelo; não há parâmetro nosso para limitar a lista.
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    http_options=types.HttpOptions(timeout=TIMEOUT_BUSCA_MS),
                ),
            )
            texto_livre = texto_da_resposta(descoberta)
            fontes = _fontes_grounding(descoberta)
            cache.registrar_sucesso(estado_cache, "Google Search")
            registrar(f"  Google Search: {len(fontes)} fonte(s) citada(s)")
            # O grounding não passa pelo cache (só o fallback DDGS passa) e devolve
            # resultado diferente a cada chamada: sem guardar o blob, uma falha de
            # extração não tem como ser reproduzida depois.
            replay.gravar(
                "grounding", pedido, texto_livre, {"fontes": fontes, "modelo": MODELO_BUSCA}
            )
        except Exception as e:  # noqa: BLE001
            abriu = cache.registrar_falha(estado_cache, "Google Search")
            sufixo = (
                f" — {cache.FALHAS_PARA_ABRIR} falhas seguidas, pulando a fonte por "
                f"{cache.HORAS_CIRCUITO_ABERTO} h"
                if abriu else ""
            )
            registrar(f"  Google Search: indisponível ({_resumo_erro(e)}){sufixo}")

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
