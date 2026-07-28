"""Conectores de ATS — casos reais capturados da API do Greenhouse em 2026-07-27.

Nenhum teste toca a rede: os payloads são os que a API devolveu de verdade para a
vaga `Junior DevOps Engineer` da AvePoint (`gh_jid=5594102`) — a mesma que entrou na
triagem como "Remoto" alucinado e virou a recomendação #1 com 78/100.
"""

import httpx
import pytest

from triagem.ats import GreenhouseAdapter, _texto_limpo, rotear

# Recorte fiel do payload real de boards-api.greenhouse.io.
VAGA_REAL = {
    "id": 5594102,
    "title": "JUNIOR DEVOPS ENGINEER",
    "company_name": "AvePoint",
    "location": {"name": "Da Nang, Da Nang, Vietnam"},
    "first_published": "2025-02-09T21:48:30-05:00",
    "updated_at": "2026-07-10T03:02:51-04:00",
    "application_deadline": None,
    "content": "&lt;p&gt;YOUR RESPONSIBILITIES WILL INCLUDE&lt;/p&gt;\n&lt;p&gt;• Management of Azure&lt;/p&gt;",
    "absolute_url": "https://www.avepoint.com/careers/job-detail?gh_jid=5594102",
}

HTML_COM_EMBED = (
    '<html><body><div id="grnhse_app"></div>'
    '<script src="https://boards.greenhouse.io/embed/job_board/js?for=avepoint"></script>'
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Qualquer requisição não interceptada explicitamente falha o teste."""

    def _proibido(*a, **k):
        raise AssertionError("teste de ATS não pode tocar a rede")

    monkeypatch.setattr(httpx, "get", _proibido)


def _responder(monkeypatch, mapa: dict):
    """Instala um httpx.get falso que devolve por URL."""

    class _Resposta:
        def __init__(self, status, corpo):
            self.status_code = status
            self._corpo = corpo

        def json(self):
            return self._corpo

    def _get(url, **kwargs):
        for fragmento, (status, corpo) in mapa.items():
            if fragmento in url:
                return _Resposta(status, corpo)
        return _Resposta(404, {})

    monkeypatch.setattr("triagem.ats.httpx.get", _get)


# ---------------------------------------------------------------- identificar

def test_identifica_url_direta_do_board():
    token, job = GreenhouseAdapter.identificar(
        "https://boards.greenhouse.io/acmecorp/jobs/4567890"
    )
    assert (token, job) == ("acmecorp", "4567890")


def test_identifica_iframe_ofuscado_pelo_script_do_embed():
    # Caso AvePoint: a URL não menciona Greenhouse; o token só existe no <script>.
    token, job = GreenhouseAdapter.identificar(
        "https://www.avepoint.com/careers/job-detail?gh_jid=5594102", HTML_COM_EMBED
    )
    assert (token, job) == ("avepoint", "5594102")


def test_gh_jid_sem_o_embed_no_html_nao_identifica():
    # Sem o token não há como consultar a API — a esteira precisa seguir no legado.
    token, job = GreenhouseAdapter.identificar(
        "https://empresa.com/vaga?gh_jid=5594102", "<html>sem script do greenhouse</html>"
    )
    assert (token, job) == (None, None)


def test_gh_jid_nao_numerico_e_recusado():
    assert GreenhouseAdapter.identificar(
        "https://empresa.com/v?gh_jid=abc", HTML_COM_EMBED
    ) == (None, None)


def test_url_de_outro_portal_nao_identifica():
    assert GreenhouseAdapter.identificar("https://www.adzuna.com.br/details/1") == (None, None)
    assert GreenhouseAdapter.identificar("") == (None, None)


# ---------------------------------------------------------------- extrair

def test_extrai_o_contrato_completo_da_vaga_real(monkeypatch):
    _responder(monkeypatch, {"/jobs/5594102": (200, VAGA_REAL)})
    dados = GreenhouseAdapter.extrair("avepoint", "5594102")

    assert set(dados) == {
        "hiringOrganization", "jobLocationType", "addressLocality",
        "datePosted", "validThrough", "description", "title",
    }
    assert dados["hiringOrganization"] == {"name": "AvePoint"}
    assert dados["addressLocality"] == "Da Nang, Da Nang, Vietnam"
    assert dados["title"] == "JUNIOR DEVOPS ENGINEER"


def test_vaga_presencial_no_exterior_nao_vira_telecommute(monkeypatch):
    """O bug de origem: esta vaga foi triada como 'Remoto' com D2 10/10."""
    _responder(monkeypatch, {"/jobs/5594102": (200, VAGA_REAL)})
    assert GreenhouseAdapter.extrair("avepoint", "5594102")["jobLocationType"] is None


def test_location_com_remote_vira_telecommute(monkeypatch):
    vaga = {**VAGA_REAL, "location": {"name": "Remote - Brazil"}}
    _responder(monkeypatch, {"/jobs/1": (200, vaga)})
    assert GreenhouseAdapter.extrair("acme", "1")["jobLocationType"] == "TELECOMMUTE"


def test_date_posted_usa_first_published_e_nao_updated_at(monkeypatch):
    """Medido: publicada em 2025-02-09, `updated_at` dizia 2026-07-10.

    Usar `updated_at` faria um anúncio de 17 meses parecer de 17 dias e atravessar
    o DIAS_MAXIMOS_ANUNCIO, que existe para entregar vaga aberta hoje.
    """
    _responder(monkeypatch, {"/jobs/5594102": (200, VAGA_REAL)})
    assert GreenhouseAdapter.extrair("avepoint", "5594102")["datePosted"].startswith("2025-02-09")


def test_date_posted_cai_para_updated_at_quando_nao_ha_publicacao(monkeypatch):
    vaga = {**VAGA_REAL, "first_published": None}
    _responder(monkeypatch, {"/jobs/1": (200, vaga)})
    assert GreenhouseAdapter.extrair("acme", "1")["datePosted"].startswith("2026-07-10")


def test_valid_through_usa_application_deadline_quando_existe(monkeypatch):
    _responder(monkeypatch, {"/jobs/1": (200, VAGA_REAL)})
    assert GreenhouseAdapter.extrair("acme", "1")["validThrough"] is None

    vaga = {**VAGA_REAL, "application_deadline": "2026-08-30T23:59:59-04:00"}
    _responder(monkeypatch, {"/jobs/1": (200, vaga)})
    assert GreenhouseAdapter.extrair("acme", "1")["validThrough"].startswith("2026-08-30")


def test_descricao_perde_html_escapado_e_tags(monkeypatch):
    _responder(monkeypatch, {"/jobs/1": (200, VAGA_REAL)})
    descricao = GreenhouseAdapter.extrair("acme", "1")["description"]
    assert "<" not in descricao and "&lt;" not in descricao
    assert "YOUR RESPONSIBILITIES WILL INCLUDE" in descricao
    assert "Management of Azure" in descricao


def test_company_name_da_vaga_dispensa_a_segunda_requisicao(monkeypatch):
    chamadas = []

    class _R:
        status_code = 200

        def json(self):
            return VAGA_REAL

    def _get(url, **k):
        chamadas.append(url)
        return _R()

    monkeypatch.setattr("triagem.ats.httpx.get", _get)
    assert GreenhouseAdapter.extrair("avepoint", "1")["hiringOrganization"]["name"] == "AvePoint"
    assert len(chamadas) == 1, "a segunda chamada ao /boards era desnecessária"


def test_sem_company_name_consulta_o_board(monkeypatch):
    vaga = {**VAGA_REAL, "company_name": None}
    _responder(monkeypatch, {"/jobs/1": (200, vaga), "/boards/acme": (200, {"name": "ACME S.A."})})
    assert GreenhouseAdapter.extrair("acme", "1")["hiringOrganization"]["name"] == "ACME S.A."


def test_board_indisponivel_cai_para_o_token_formatado(monkeypatch):
    vaga = {**VAGA_REAL, "company_name": None}
    _responder(monkeypatch, {"/jobs/1": (200, vaga)})  # /boards devolve 404
    nome = GreenhouseAdapter.extrair("acme-corp", "1")["hiringOrganization"]["name"]
    assert nome == "Acme Corp"


# ---------------------------------------------------------------- falha graciosa

@pytest.mark.parametrize("status", [404, 403, 500])
def test_erro_http_devolve_none(monkeypatch, status):
    _responder(monkeypatch, {"/jobs/1": (status, {})})
    assert GreenhouseAdapter.extrair("acme", "1") is None


def test_timeout_devolve_none_sem_levantar(monkeypatch):
    def _estoura(url, **k):
        raise httpx.ConnectTimeout("estourou")

    monkeypatch.setattr("triagem.ats.httpx.get", _estoura)
    assert GreenhouseAdapter.extrair("acme", "1") is None


def test_argumentos_vazios_nao_batem_na_rede():
    # O fixture _sem_rede falha o teste se houver requisição.
    assert GreenhouseAdapter.extrair("", "1") is None
    assert GreenhouseAdapter.extrair("acme", "") is None


# ---------------------------------------------------------------- dispatcher

def test_rotear_devolve_o_contrato_quando_reconhece(monkeypatch):
    _responder(monkeypatch, {"/jobs/5594102": (200, VAGA_REAL)})
    dados = rotear("https://www.avepoint.com/careers/job-detail?gh_jid=5594102", HTML_COM_EMBED)
    assert dados["addressLocality"] == "Da Nang, Da Nang, Vietnam"


def test_rotear_devolve_none_para_url_de_outro_portal():
    assert rotear("https://www.adzuna.com.br/details/5815842825", "<html></html>") is None


def test_rotear_engole_adapter_quebrado(monkeypatch):
    class _Explode:
        @staticmethod
        def identificar(url, html_bruto=None):
            raise RuntimeError("adapter com bug")

    monkeypatch.setattr("triagem.ats.ADAPTADORES", (_Explode,))
    assert rotear("https://qualquer.com/vagas/1", "") is None


def test_texto_limpo_lida_com_vazio_e_none():
    assert _texto_limpo("") == ""
    assert _texto_limpo(None) == ""
