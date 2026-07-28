"""Conectores para ATS com API pública (padrão Adapter).

Por que existe: portais respondem 403 a cliente automatizado e escondem os dados
dentro de HTML que muda de layout. O ATS que hospeda a vaga expõe os mesmos dados
por REST, sem credencial e sem anti-bot — e são dados do empregador, não a
renderização de um agregador nem a interpretação de um LLM.

O caso que motivou o módulo: `Junior DevOps Engineer` da AvePoint entrou na triagem
com `localizacao: "Remoto"` inventada pelo modelo e virou a recomendação #1 com
78/100. O anúncio está no Greenhouse, e a API devolve, sem ambiguidade:

    location: {"name": "Da Nang, Da Nang, Vietnam"}

Cada adapter devolve o mesmo contrato que `_extrair_jobposting` produz a partir de
`schema.org/JobPosting`, para que o resto da esteira não precise saber a origem.
"""

import html as html_lib
import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import httpx

TIMEOUT_SEGUNDOS = 5

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (compatible; TriagemVagas/2.0)",
    "Accept": "application/json",
}

_TAGS_HTML = re.compile(r"<[^>]+>")


def _texto_limpo(bruto: str) -> str:
    """`content` do Greenhouse vem com HTML escapado dentro de string JSON."""
    if not bruto:
        return ""
    # Duplo unescape: a API entrega `&lt;p&gt;`, que vira `<p>` e só então é removível.
    desescapado = html_lib.unescape(bruto)
    sem_tags = _TAGS_HTML.sub(" ", desescapado)
    return " ".join(html_lib.unescape(sem_tags).split())


class GreenhouseAdapter:
    """Sem estado: só rotas e formatação. Erro de rede nunca escapa."""

    API_BASE = "https://boards-api.greenhouse.io/v1/boards"

    _URL_DIRETA = re.compile(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)")
    _EMBED_NO_HTML = re.compile(r"embed/job_board/js\?for=([a-zA-Z0-9_-]+)")

    @classmethod
    def identificar(
        cls, url: str, html_bruto: Optional[str] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """Devolve `(board_token, job_id)`, ou `(None, None)` se não for Greenhouse.

        Duas formas de hospedagem: o board direto, e o iframe embutido no site da
        empresa — que é o caso da AvePoint, onde a URL não menciona Greenhouse em
        lugar nenhum e o token só aparece no `<script>` do embed.
        """
        direto = cls._URL_DIRETA.search(url or "")
        if direto:
            return direto.group(1), direto.group(2)

        if "gh_jid=" not in (url or ""):
            return None, None

        valores = parse_qs(urlsplit(url).query).get("gh_jid") or []
        job_id = (valores[0] if valores else "").strip()
        if not job_id.isdigit():
            return None, None

        embutido = cls._EMBED_NO_HTML.search(html_bruto or "")
        if not embutido:
            # Sem o token não há como consultar a API; a esteira segue no fluxo legado.
            return None, None
        return embutido.group(1), job_id

    @classmethod
    def _nome_da_empresa(cls, board_token: str, vaga: dict) -> str:
        """`company_name` já vem na própria vaga; o board é o segundo recurso.

        Medido em 2026-07-27: o payload da vaga traz `company_name: "AvePoint"`, o
        que dispensa a segunda requisição na maioria dos casos.
        """
        direto = str(vaga.get("company_name") or "").strip()
        if direto:
            return direto
        try:
            resposta = httpx.get(
                f"{cls.API_BASE}/{board_token}",
                timeout=TIMEOUT_SEGUNDOS,
                headers=CABECALHOS,
            )
            if resposta.status_code == 200:
                nome = str((resposta.json() or {}).get("name") or "").strip()
                if nome:
                    return nome
        except Exception:  # noqa: BLE001 — falha de rede não derruba a extração
            pass
        return board_token.replace("-", " ").replace("_", " ").title()

    @classmethod
    def extrair(cls, board_token: str, job_id: str) -> Optional[dict]:
        """Consulta a API pública e devolve o contrato comum, ou None em falha."""
        if not board_token or not job_id:
            return None
        try:
            resposta = httpx.get(
                f"{cls.API_BASE}/{board_token}/jobs/{job_id}",
                timeout=TIMEOUT_SEGUNDOS,
                headers=CABECALHOS,
            )
            if resposta.status_code != 200:
                return None
            vaga = resposta.json()
        except Exception:  # noqa: BLE001 — 404, timeout, JSON inválido: tudo vira None
            return None
        if not isinstance(vaga, dict) or not vaga:
            return None

        local = vaga.get("location")
        localidade = ""
        if isinstance(local, dict):
            localidade = str(local.get("name") or "").strip()

        return {
            "hiringOrganization": {"name": cls._nome_da_empresa(board_token, vaga)},
            "jobLocationType": "TELECOMMUTE" if "remote" in localidade.lower() else None,
            "addressLocality": localidade,
            # `first_published` e não `updated_at`: medido na AvePoint, a vaga foi
            # publicada em 2025-02-09 e o `updated_at` dizia 2026-07-10. Usar o
            # segundo faz um anúncio de 17 meses parecer de 17 dias e atravessa o
            # DIAS_MAXIMOS_ANUNCIO, que existe para entregar vaga aberta hoje.
            "datePosted": (
                str(vaga.get("first_published") or vaga.get("updated_at") or "").strip()
            ),
            # O Greenhouse quase sempre deixa isto nulo, mas quando preenche é a
            # data real de fechamento — e alimenta o descarte determinístico.
            "validThrough": str(vaga.get("application_deadline") or "").strip() or None,
            "description": _texto_limpo(str(vaga.get("content") or "")),
            "title": str(vaga.get("title") or "").strip(),
        }


# Ordem de tentativa do dispatcher. Novos ATS entram aqui.
ADAPTADORES: tuple = (GreenhouseAdapter,)


def rotear(url: str, html_bruto: Optional[str] = None) -> Optional[dict]:
    """Tenta cada adapter; devolve o primeiro contrato preenchido, ou None.

    Nunca levanta: qualquer falha significa "não sei", e a esteira segue para o
    JSON-LD e, se preciso, para o texto livre.
    """
    for adaptador in ADAPTADORES:
        try:
            board_token, job_id = adaptador.identificar(url, html_bruto)
            if not board_token or not job_id:
                continue
            dados = adaptador.extrair(board_token, job_id)
            if dados:
                return dados
        except Exception:  # noqa: BLE001 — adapter quebrado não pode parar a busca
            continue
    return None
