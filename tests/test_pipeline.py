"""Testes do pipeline sem API: entrada, scoring, relatório, histórico e export."""

import json

import pytest
from pydantic import ValidationError

from triagem import historico
from triagem.analisador import MODELO_PADRAO, MODELOS, analisar_vaga
from triagem.buscador import (
    _buscar_adzuna,
    _buscar_jooble,
    _localizacao_compativel,
    _selecionar_candidatas,
    buscar_vagas,
)
from triagem.cli import _inteiro_positivo
from triagem.curriculo import gerar_material
from triagem.entrada import carregar_vagas
from triagem.exportar import exportar
from triagem.relatorio import render_relatorio
from triagem.schema import AnaliseVaga, Dimensao, Notas
from triagem.scoring import pontuar


def _dim(nota, just="x"):
    return Dimensao(nota=nota, justificativa=just)


def _analise(descartada=False, regime="remoto", d2=10, empresa="TechCorp", titulo="Estágio DevOps"):
    return AnaliseVaga(
        titulo_normalizado=titulo,
        empresa=empresa,
        regime=regime,
        localizacao="Remoto (Brasil)",
        nivel_real="estagio",
        stack_exigida=["Azure DevOps", "GitHub Actions"],
        stack_desejavel=["Terraform"],
        idioma_trabalho="misto",
        link="https://example.com/1",
        origem="linkedin",
        descartada=descartada,
        motivo_descarte="híbrido fora de Curitiba" if descartada else None,
        notas=None if descartada else Notas(
            d1_crescimento=_dim(9),
            d2_regime_localizacao=_dim(d2),
            d3_stack_fit=_dim(9),
            d4_ingles=_dim(9),
            d5_nivel_real=_dim(10),
        ),
        alertas=[],
    )


# ---------------------------------------------------------------- Gemini

class _ModelosFake:
    def __init__(self, resposta):
        self.respostas = resposta if isinstance(resposta, list) else [resposta]
        self.chamada = None

    def generate_content(self, **kwargs):
        self.chamada = kwargs
        return self.respostas.pop(0)


class _ClienteFake:
    def __init__(self, resposta):
        self.models = _ModelosFake(resposta)


def _resposta_gemini(texto):
    part = type("Part", (), {"text": texto, "thought": False})()
    content = type("Content", (), {"parts": [part]})()
    candidate = type("Candidate", (), {"content": content, "grounding_metadata": None})()
    return type("Resposta", (), {"candidates": [candidate]})()


def test_analisador_gemini_usa_schema_pydantic():
    resposta = _resposta_gemini(_analise().model_dump_json())
    client = _ClienteFake(resposta)
    analise = analisar_vaga(client, "vaga de teste")
    assert isinstance(analise, AnaliseVaga)
    assert client.models.chamada["model"] == MODELOS[MODELO_PADRAO]
    config = client.models.chamada["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["title"] == "AnaliseVaga"


def test_analisador_rejeita_resposta_sem_parsed():
    client = _ClienteFake(type("Resposta", (), {"candidates": []})())
    with pytest.raises(ValueError, match="estruturada"):
        analisar_vaga(client, "vaga de teste")


def test_gerador_cv_gemini_retorna_texto():
    client = _ClienteFake(_resposta_gemini("material pronto"))
    material = gerar_material(client, "meu cv", "a vaga", _analise().model_dump())
    assert material == "material pronto"
    assert client.models.chamada["config"].system_instruction


def test_busca_web_normaliza_e_remove_links_duplicados(monkeypatch):
    descoberta = _resposta_gemini("Encontrei duas vagas atuais.")
    json_vagas = json.dumps(
        {
            "vagas": [
                {
                    "titulo": "Dev .NET Jr",
                    "empresa": "Empresa",
                    "descricao": "Vaga remota para desenvolvimento C# e .NET com APIs REST.",
                    "link": "https://example.com/vaga",
                    "origem": "site",
                    "publicada_em": "2026-07-20",
                },
                {
                    "titulo": "Dev .NET Jr duplicada",
                    "empresa": "Empresa",
                    "descricao": "Mesma vaga remota para desenvolvimento C# e .NET.",
                    "link": "https://example.com/vaga",
                    "origem": "site",
                    "publicada_em": "",
                },
            ]
        }
    )
    client = _ClienteFake([descoberta, _resposta_gemini(json_vagas)])
    monkeypatch.setattr(
        "triagem.buscador._validar_links", lambda vagas, limite: vagas[:limite]
    )
    vagas = buscar_vagas(client, "CV", "vagas C# Jr", limite=5)
    assert len(vagas) == 1
    assert vagas[0].titulo == "Dev .NET Jr"


def test_busca_jooble_converte_resultados(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "teste")

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "jobs": [
                    {
                        "title": "DevOps Jr",
                        "company": "Empresa",
                        "location": "Remote",
                        "type": "CLT",
                        "updated": "2026-07-24",
                        "link": "https://example.com/job",
                        "snippet": "Azure e CI/CD",
                    }
                ]
            }

    monkeypatch.setattr("triagem.buscador.httpx.post", lambda *args, **kwargs: Resposta())
    texto, fontes = _buscar_jooble("DevOps Jr", 3)
    assert "DevOps Jr" in texto
    assert fontes == ["- Jooble: https://example.com/job"]


def test_busca_adzuna_converte_resultados(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "app")
    monkeypatch.setenv("ADZUNA_API_KEY", "key")

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "DevOps Jr",
                        "company": {"display_name": "Empresa"},
                        "location": {"display_name": "Curitiba"},
                        "created": "2026-07-24T12:00:00Z",
                        "redirect_url": "https://example.com/adzuna-job",
                        "description": "Azure, Docker e CI/CD",
                    }
                ]
            }

    monkeypatch.setattr("triagem.buscador.httpx.get", lambda *args, **kwargs: Resposta())
    texto, fontes = _buscar_adzuna("DevOps Jr", 3)
    assert "DevOps Jr" in texto
    assert "Curitiba" in texto
    assert fontes == ["- Adzuna: https://example.com/adzuna-job"]


@pytest.mark.parametrize(
    "funcao,variaveis",
    [
        (_buscar_jooble, {"JOOBLE_API_KEY": "teste"}),
        (
            _buscar_adzuna,
            {"ADZUNA_APP_ID": "app", "ADZUNA_API_KEY": "key"},
        ),
    ],
)
def test_fonte_ignora_resposta_json_invalida(monkeypatch, funcao, variaveis):
    for nome, valor in variaveis.items():
        monkeypatch.setenv(nome, valor)

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("JSON inválido")

    monkeypatch.setattr("triagem.buscador.httpx.get", lambda *args, **kwargs: Resposta())
    monkeypatch.setattr("triagem.buscador.httpx.post", lambda *args, **kwargs: Resposta())
    assert funcao("DevOps Jr", 3) == ("", [])


def test_prefiltro_remove_senior_e_deduplica_semanticamente():
    def vaga(titulo, empresa, link, descricao):
        from triagem.schema import VagaEncontrada

        return VagaEncontrada(
            titulo=titulo,
            empresa=empresa,
            link=link,
            descricao=descricao,
            origem="teste",
        )

    vagas = [
        vaga(
            "Senior Backend Engineer .NET",
            "Empresa A",
            "https://a.example/senior",
            "Vaga remota com C# e .NET para profissional sênior com seis anos.",
        ),
        vaga(
            "Desenvolvedor .NET Júnior",
            "Empresa B",
            "https://portal1.example/vaga",
            "Vaga remota no Brasil para Júnior com C#, .NET e APIs REST.",
        ),
        vaga(
            "Desenvolvedor .NET Jr",
            "Empresa B",
            "https://portal2.example/mesma-vaga",
            "Mesma vaga remota no Brasil para Jr com C#, .NET e APIs REST.",
        ),
        vaga(
            "Analista Financeiro Júnior",
            "Empresa C",
            "https://c.example/financeiro",
            "Vaga remota para área financeira, conciliação e relatórios.",
        ),
    ]
    selecionadas = _selecionar_candidatas(vagas, 10)
    assert len(selecionadas) == 1
    assert selecionadas[0].empresa == "Empresa B"


def test_portal_internacional_exige_evidencia_de_aceite_no_brasil():
    from triagem.schema import VagaEncontrada

    base = {
        "titulo": "Junior DevOps Engineer",
        "empresa": "Empresa",
        "link": "https://www.virtualvocations.com/job/123",
        "origem": "teste",
    }
    restrita = VagaEncontrada(
        **base,
        descricao="Remote role requiring Linux, Docker and CI/CD experience.",
    )
    global_ = VagaEncontrada(
        **base,
        descricao="Worldwide remote role accepting candidates in Brazil with Docker.",
    )
    assert not _localizacao_compativel(restrita)
    assert _localizacao_compativel(global_)


# ---------------------------------------------------------------- entrada

def test_entrada_json_lista():
    conteudo = json.dumps([{"titulo": "A", "descricao": "x"}, {"titulo": "B", "descricao": "y"}])
    assert len(carregar_vagas(conteudo)) == 2


def test_entrada_json_objeto_unico():
    assert len(carregar_vagas(json.dumps({"titulo": "A", "descricao": "x"}))) == 1


def test_entrada_json_malformado():
    with pytest.raises(ValueError, match="malformado"):
        carregar_vagas('[{"titulo": "A",}]')


def test_entrada_texto_com_separador():
    assert len(carregar_vagas("vaga um\n---\nvaga dois")) == 2


def test_entrada_texto_sem_separador():
    assert len(carregar_vagas("uma vaga só, sem separador")) == 1


def test_entrada_vazia():
    with pytest.raises(ValueError, match="vazio"):
        carregar_vagas("   ")


@pytest.mark.parametrize("conteudo", ["[]", "{}"])
def test_entrada_json_sem_dados(conteudo):
    with pytest.raises(ValueError, match="nenhuma vaga"):
        carregar_vagas(conteudo)


def test_entrada_texto_separador_crlf_com_espacos():
    assert carregar_vagas("vaga um\r\n  ---  \r\nvaga dois") == ["vaga um", "vaga dois"]


def test_paralelismo_precisa_ser_positivo():
    assert _inteiro_positivo("2") == 2
    with pytest.raises(Exception, match="maior que zero"):
        _inteiro_positivo("0")


# ---------------------------------------------------------------- scoring

def test_score_composto():
    vaga = pontuar(_analise(), "abc")
    # 9*.30 + 10*.25 + 9*.20 + 9*.15 + 10*.10 = 9.35 -> 93.5
    assert vaga.score_final == 93.5
    assert vaga.id == "abc"


def test_regra_fixa_d2_sobrescreve_nota_do_modelo():
    analise = _analise(d2=4)
    vaga = pontuar(analise)  # modelo errou: remoto tem que ser 10
    assert vaga.analise.notas.d2_regime_localizacao.nota == 10
    assert vaga.score_final == 93.5
    assert analise.notas.d2_regime_localizacao.nota == 4


def test_schema_rejeita_nota_fora_do_intervalo():
    with pytest.raises(ValidationError):
        _dim(11)


def test_schema_exige_notas_em_vaga_aprovada():
    dados = _analise().model_dump()
    dados["notas"] = None
    with pytest.raises(ValidationError, match="vaga aprovada deve ter notas"):
        AnaliseVaga.model_validate(dados)


@pytest.mark.parametrize("regime,esperado", [("remoto", 10), ("hibrido", 8), ("presencial", 6)])
def test_d2_por_regime(regime, esperado):
    vaga = pontuar(_analise(regime=regime, d2=0))
    assert vaga.analise.notas.d2_regime_localizacao.nota == esperado


def test_descartada_fica_sem_score():
    assert pontuar(_analise(descartada=True)).score_final is None


# ---------------------------------------------------------------- relatorio

def test_relatorio_ordena_e_resume():
    alta = pontuar(_analise(), "id1")
    baixa = pontuar(_analise(regime="presencial", d2=6, empresa="Outra"), "id2")
    desc = pontuar(_analise(descartada=True, empresa="Ruim"), "id3")
    saida = render_relatorio([baixa, desc, alta])
    assert saida.index("TechCorp") < saida.index("Outra")  # maior score primeiro
    assert "TOTAL ANALISADAS: 3 | DESCARTADAS (hard filter): 1 | APROVADAS: 2" in saida
    assert "TOP RECOMENDADA: TechCorp" in saida
    assert "id3" in saida  # descartada listada com id


# ---------------------------------------------------------------- historico

def test_historico_id_estavel_ignora_espacos_e_caixa():
    assert historico.gerar_id("Vaga DevOps  Remoto") == historico.gerar_id("vaga devops remoto")


def test_historico_registrar_e_preservar_status(tmp_path, monkeypatch):
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "historico.json")
    hist = historico.carregar()
    vaga = pontuar(_analise(), historico.gerar_id("texto da vaga"))

    historico.registrar(hist, vaga, "texto da vaga")
    assert hist[vaga.id]["status"] == "novo"

    historico.atualizar_status(hist, vaga.id[:4], "aplicado")
    historico.registrar(hist, vaga, "texto da vaga")  # re-análise não pode resetar
    assert hist[vaga.id]["status"] == "aplicado"

    historico.salvar(hist)
    assert historico.carregar()[vaga.id]["score_final"] == 93.5


def test_historico_buscar_prefixo_ambiguo_ou_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "historico.json")
    hist = {"aa11": {}, "aa22": {}}
    with pytest.raises(KeyError, match="ambíguo"):
        historico.buscar(hist, "aa")
    with pytest.raises(KeyError, match="Nenhuma"):
        historico.buscar(hist, "zz")


def test_historico_corrompido_da_erro_claro(tmp_path, monkeypatch):
    arquivo = tmp_path / "historico.json"
    arquivo.write_text("{", encoding="utf-8")
    monkeypatch.setattr(historico, "ARQUIVO", arquivo)
    with pytest.raises(ValueError, match="Não foi possível ler"):
        historico.carregar()


def test_historico_cria_backup_antes_de_substituir(tmp_path, monkeypatch):
    arquivo = tmp_path / "historico.json"
    arquivo.write_text('{"antigo": {}}', encoding="utf-8")
    monkeypatch.setattr(historico, "ARQUIVO", arquivo)
    historico.salvar({"novo": {}})
    assert historico.carregar() == {"novo": {}}
    assert (tmp_path / "historico.json.bak").read_text(encoding="utf-8") == '{"antigo": {}}'


# ---------------------------------------------------------------- exportar

def test_exportar_markdown(tmp_path):
    caminho = tmp_path / "subpasta" / "rel.md"
    exportar([pontuar(_analise(), "id1"), pontuar(_analise(descartada=True), "id2")], str(caminho))
    md = caminho.read_text(encoding="utf-8")
    assert "TechCorp" in md and "| Dimensão | Nota |" in md and "Descartadas" in md


def test_exportar_csv(tmp_path):
    caminho = tmp_path / "rel.csv"
    exportar([pontuar(_analise(), "id1")], str(caminho))
    linhas = caminho.read_text(encoding="utf-8-sig").splitlines()
    assert linhas[0].startswith("id,score,empresa")
    assert "TechCorp" in linhas[1]


def test_exportar_extensao_invalida(tmp_path):
    with pytest.raises(ValueError, match="não suportada"):
        exportar([pontuar(_analise())], str(tmp_path / "rel.pdf"))
