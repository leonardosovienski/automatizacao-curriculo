"""Busca vagas atuais na web usando Google Search Grounding do Gemini."""

import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import httpx
from ddgs import DDGS
from google import genai
from google.genai import types

from .analisador import MODELOS, texto_da_resposta
from .schema import ResultadoBusca, VagaEncontrada

MODELO_BUSCA = "gemini-3.5-flash-lite"

TERMOS_ALVO = (
    "devops",
    "devsecops",
    "platform engineer",
    "platform engineering",
    "site reliability",
    "sre",
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
PORTAIS_INTERNACIONAIS = ("virtualvocations.com",)


def _normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", texto or "")
    ascii_texto = "".join(c for c in sem_acentos if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9+#.]+", " ", ascii_texto.lower()).split())


def _contem_termo(texto: str, termos: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", texto) for termo in termos)


def _pontuacao_preliminar(vaga: VagaEncontrada) -> int:
    titulo = _normalizar(vaga.titulo)
    conteudo = _normalizar(f"{vaga.titulo} {vaga.descricao}")
    pontos = 0
    if _contem_termo(titulo, TERMOS_SENIOR):
        return -100
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
    if re.search(r"\b(?:3|4|5|6|7|8|9|10)\+?\s*anos?\b", conteudo):
        pontos -= 8
    if not vaga.empresa.strip():
        pontos -= 2
    if len(vaga.descricao) < 100:
        pontos -= 2
    return pontos


def _url_canonica(url: str) -> str:
    partes = urlsplit(url.strip())
    return urlunsplit(
        (partes.scheme.lower(), partes.netloc.lower(), partes.path.rstrip("/"), "", "")
    )


def _chave_semantica(vaga: VagaEncontrada) -> tuple[str, str]:
    titulo = _normalizar(vaga.titulo)
    for termo in TERMOS_ENTRADA + TERMOS_SENIOR:
        titulo = re.sub(rf"(?<!\w){re.escape(termo)}(?!\w)", " ", titulo)
    return (" ".join(titulo.split()), _normalizar(vaga.empresa))


def _localizacao_compativel(vaga: VagaEncontrada) -> bool:
    host = urlsplit(vaga.link).netloc.lower()
    conteudo = _normalizar(f"{vaga.titulo} {vaga.descricao}")
    if any(portal in host for portal in PORTAIS_INTERNACIONAIS):
        return _contem_termo(conteudo, TERMOS_INTERNACIONAL_ACEITO)
    if "linkedin.com" in host and not host.startswith("br."):
        return _contem_termo(conteudo, TERMOS_INTERNACIONAL_ACEITO)
    return True


def _link_ativo(vaga: VagaEncontrada) -> bool:
    """Validação conservadora: remove expiração clara, preserva sites que bloqueiam bots."""
    try:
        response = httpx.get(
            vaga.link,
            follow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TriagemVagas/1.0)"},
        )
    except httpx.HTTPError:
        return True
    if response.status_code in (401, 403, 429):
        return True
    if response.status_code >= 400:
        return False

    original = urlsplit(vaga.link)
    final = urlsplit(str(response.url))
    if "linkedin.com" in original.netloc and "/jobs/view/" in original.path:
        if "/jobs/view/" not in final.path:
            return False
    pagina = _normalizar(response.text[:100_000])
    marcadores_expirados = (
        "no longer accepting applications",
        "nao aceita mais candidaturas",
        "job is no longer available",
        "vaga nao esta mais disponivel",
        "expired jd redirect",
    )
    return not any(marcador in pagina for marcador in marcadores_expirados)


def _validar_links(vagas: list[VagaEncontrada], limite: int) -> list[VagaEncontrada]:
    compativeis = [vaga for vaga in vagas if _localizacao_compativel(vaga)]
    if not compativeis:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(compativeis))) as executor:
        estados = list(executor.map(_link_ativo, compativeis))
    return [vaga for vaga, ativa in zip(compativeis, estados, strict=True) if ativa][:limite]


def _selecionar_candidatas(
    vagas: list[VagaEncontrada], limite: int
) -> list[VagaEncontrada]:
    """Remove lixo/senioridade, deduplica e ordena antes da triagem cara."""
    candidatas = [
        vaga
        for vaga in vagas
        if _pontuacao_preliminar(vaga) >= 8
        and _contem_termo(_normalizar(f"{vaga.titulo} {vaga.descricao}"), TERMOS_ALVO)
    ]
    candidatas.sort(
        key=lambda vaga: (
            _pontuacao_preliminar(vaga),
            bool(vaga.publicada_em),
            len(vaga.descricao),
        ),
        reverse=True,
    )
    unicas = []
    urls_vistas = set()
    vagas_vistas = set()
    for vaga in candidatas:
        url = _url_canonica(vaga.link)
        semantica = _chave_semantica(vaga)
        if url in urls_vistas or semantica in vagas_vistas:
            continue
        urls_vistas.add(url)
        vagas_vistas.add(semantica)
        unicas.append(vaga)
    return unicas[:limite]


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
    buscador = DDGS()
    for consulta in consultas:
        try:
            encontrados = buscador.text(
                consulta,
                region="br-pt",
                safesearch="moderate",
                timelimit="m",
                max_results=max_por_consulta,
            )
        except Exception:
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


def _buscar_jooble(pedido: str, limite: int) -> tuple[str, list[str]]:
    """Consulta a API estruturada da Jooble quando uma chave está configurada."""
    api_key = os.environ.get("JOOBLE_API_KEY")
    if not api_key:
        return "", []

    consultas = [
        pedido,
        "DevOps Junior",
        "DevSecOps Junior",
        "Desenvolvedor .NET C# Junior",
    ]
    resultados = []
    vistos = set()
    for consulta in consultas:
        try:
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
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        for vaga in payload.get("jobs", []):
            link = vaga.get("link")
            if not link or link in vistos:
                continue
            vistos.add(link)
            resultados.append(
                f"Título: {vaga.get('title', '')}\n"
                f"Empresa: {vaga.get('company', '')}\n"
                f"Localização: {vaga.get('location', '')}\n"
                f"Tipo: {vaga.get('type', '')}\n"
                f"Atualizada em: {vaga.get('updated', '')}\n"
                f"Link: {link}\n"
                f"Descrição: {vaga.get('snippet', '')}"
            )
    return "\n\n---\n\n".join(resultados), [f"- Jooble: {link}" for link in vistos]


def _buscar_adzuna(pedido: str, limite: int) -> tuple[str, list[str]]:
    """Consulta vagas brasileiras na Adzuna quando as duas credenciais existem."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_API_KEY")
    if not app_id or not app_key:
        return "", []

    consultas = (pedido, "DevOps Junior", "DevSecOps Junior", ".NET C# Junior")
    resultados = []
    vistos = set()
    por_consulta = min(20, max(5, limite // len(consultas)))
    for consulta in consultas:
        try:
            response = httpx.get(
                "https://api.adzuna.com/v1/api/jobs/br/search/1",
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": consulta,
                    "results_per_page": por_consulta,
                    "sort_by": "date",
                    "content-type": "application/json",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        for vaga in payload.get("results", []):
            link = vaga.get("redirect_url")
            if not link or link in vistos:
                continue
            vistos.add(link)
            empresa = vaga.get("company") or {}
            localizacao = vaga.get("location") or {}
            resultados.append(
                f"Título: {vaga.get('title', '')}\n"
                f"Empresa: {empresa.get('display_name', '')}\n"
                f"Localização: {localizacao.get('display_name', '')}\n"
                f"Publicada em: {vaga.get('created', '')}\n"
                f"Link: {link}\n"
                f"Descrição: {vaga.get('description', '')}"
            )
    return "\n\n---\n\n".join(resultados), [f"- Adzuna: {link}" for link in vistos]


def buscar_vagas(
    client: genai.Client,
    cv_base: str,
    pedido: str,
    limite: int = 10,
    modelo_analise: str = "lite",
) -> list[VagaEncontrada]:
    """Pesquisa a web e devolve vagas com descrição e URL para triagem."""
    hoje = date.today().isoformat()
    # As fontes são ruidosas. Coletamos um conjunto maior para que vagas ruins
    # não ocupem as posições que o usuário pediu.
    limite_coleta = min(50, max(20, limite * 5))
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
Elimine cargos Sênior, Staff, Lead, Principal, Arquiteto, Especialista e Gerente antes de
responder. Priorize títulos com Estágio, Estagiário, Trainee, Júnior ou Jr e descrições que
aceitem até 2 anos de experiência. Não invente dados ausentes. Apresente os achados com links.
"""
    textos = []
    fontes = []

    texto_jooble, fontes_jooble = _buscar_jooble(pedido, limite_coleta)
    if texto_jooble:
        textos.append(texto_jooble)
        fontes.extend(fontes_jooble)

    texto_adzuna, fontes_adzuna = _buscar_adzuna(pedido, limite_coleta)
    if texto_adzuna:
        textos.append(texto_adzuna)
        fontes.extend(fontes_adzuna)

    try:
        descoberta = client.models.generate_content(
            model=MODELO_BUSCA,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(google_search=types.GoogleSearch()),
                    types.Tool(url_context=types.UrlContext()),
                ],
            ),
        )
        texto_google = texto_da_resposta(descoberta)
        if texto_google:
            textos.append(texto_google)
        fontes.extend(_fontes_grounding(descoberta))
    except Exception:
        texto_meta, fontes_meta = _busca_metasearch(pedido, limite_coleta)
        if texto_meta:
            textos.append(texto_meta)
        fontes.extend(fontes_meta)
    if not textos and not fontes:
        return []

    resultados_texto = "\n\n---\n\n".join(textos)
    normalizacao = client.models.generate_content(
        model=MODELOS[modelo_analise],
        contents=(
            f"Converta os resultados abaixo em até {limite_coleta} vagas candidatas. "
            "DESCARTE antes de responder: Sênior/Sr, Staff, Lead, Principal, Arquiteto, "
            "Especialista, Gerente, vagas que exijam 3+ anos e áreas sem relação com "
            "DevOps/DevSecOps/Platform/SRE/C#/.NET. Priorize Estágio, Trainee, Júnior/Jr, "
            "remoto Brasil e Curitiba/Araucária. Inclua somente anúncios específicos com "
            "URL HTTP(S), preserve requisitos e não invente informações.\n\n"
            f"RESULTADOS:\n{resultados_texto}\n\nFONTES:\n" + "\n".join(fontes)
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=ResultadoBusca.model_json_schema(),
        ),
    )
    resultado = ResultadoBusca.model_validate_json(texto_da_resposta(normalizacao))

    candidatas = _selecionar_candidatas(resultado.vagas, limite * 3)
    return _validar_links(candidatas, limite)
