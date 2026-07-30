"""Testes do pipeline sem API: entrada, scoring, relatório, histórico e export."""

import json
import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from triagem import cache, historico, replay
from triagem.analisador import (
    MODELO_PADRAO,
    MODELOS,
    TIMEOUT_ANALISE_MS,
    _bloco_autoritativo,
    analisar_vaga,
    gerar_com_retentativa,
    system_prompt,
)
from triagem.buscador import (
    DIAS_MAXIMOS_ANUNCIO,
    MARCADORES_EXPIRADOS,
    MODELO_BUSCA,
    ContratoFonteAlterado,
    Inspecao,
    _ancorar_empresa,
    _ancorar_localizacao,
    _busca_metasearch,
    _buscar_adzuna,
    _buscar_jooble,
    _dias_desde,
    _e_router_de_redirect,
    _empresa_do_jsonld_confiavel,
    _enriquecer_descricao,
    _extrair_jobposting,
    _fonte_estruturada,
    _host_de_anuncio,
    _host_e_seguro,
    _inspecionar_link,
    _limpar_url,
    _local_declarado_incompativel,
    _localizacao_compativel,
    _motivo_reprovacao,
    _normalizar,
    _normalizar_com_indices,
    _obter,
    _pontuacao_preliminar,
    _redigir_segredos,
    _resolver_router,
    _resumo_erro,
    _selecionar_candidatas,
    _texto_livre_com_cache,
    _texto_visivel,
    _url_canonica,
    _validar_links,
    buscar_vagas,
)
from triagem.cli import _impor_campos_autoritativos, _inteiro_positivo, _montar_parser
from triagem.curriculo import carregar_cv_base, gerar_material, remover_blocos_privados
from triagem.entrada import carregar_vagas
from triagem.exportar import exportar
from triagem.relatorio import render_relatorio
from triagem.schema import AnaliseVaga, Dimensao, Notas, VagaEncontrada
from triagem.scoring import (
    ALERTA_REGIME_INDEFINIDO,
    D2_HIBRIDO_FORA_DO_RAIO,
    D2_POR_REGIME,
    D2_PRESENCIAL_FORA_DO_RAIO,
    PESOS,
    parse_pesos,
    pontuar,
)


def _vaga(titulo, empresa="Empresa", link="https://exemplo.com.br/vagas/anuncio-12345",
          descricao=None,
          localizacao="", publicada_em=""):
    return VagaEncontrada(
        titulo=titulo,
        empresa=empresa,
        link=link,
        descricao=descricao or "Vaga remota no Brasil para Júnior com C#, .NET e APIs REST.",
        origem="teste",
        localizacao=localizacao,
        publicada_em=publicada_em,
    )


@pytest.fixture(autouse=True)
def _sem_espera_de_backoff(monkeypatch):
    """Neutraliza o sleep do backoff: a suíte testa a lógica, não o relógio."""
    monkeypatch.setattr("triagem.buscador.time.sleep", lambda _: None)
    monkeypatch.setattr("triagem.analisador.time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _cache_isolado(tmp_path, monkeypatch):
    """Nenhum teste pode ler ou escrever o cache real do usuário."""
    monkeypatch.setattr(cache, "ARQUIVO", tmp_path / "cache_busca.json")


@pytest.fixture(autouse=True)
def _replay_isolado(tmp_path, monkeypatch):
    """Mesmo motivo do cache: a suíte estava gravando no .replay real do usuário.

    Descoberto ao inspecionar um payload de produção e encontrar dentro dele a
    string de uma fixture. Sem isto, o diretório de replay — que existe para
    reproduzir falhas reais — fica contaminado com dados sintéticos.
    """
    monkeypatch.setattr(replay, "DIRETORIO", tmp_path / "replay")


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


def _material_json(evidencia="meu cv"):
    return json.dumps(
        {
            "fit": [
                {"texto": "Fit 1", "evidencia_cv": evidencia},
                {"texto": "Fit 2", "evidencia_cv": evidencia},
                {"texto": "Fit 3", "evidencia_cv": evidencia},
            ],
            "bullets_cv": [{"texto": "Bullet", "evidencia_cv": evidencia}],
            "gaps": ["Gap"],
            "mensagem": "Mensagem profissional.",
            "evidencias_mensagem": [evidencia],
            "ats_cobertas": ["Python"],
            "ats_ausentes": ["Terraform"],
        },
        ensure_ascii=False,
    )


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
    client = _ClienteFake(_resposta_gemini(_material_json()))
    material = gerar_material(client, "meu cv", "a vaga", _analise().model_dump())
    assert "### 1. Fit em 3 bullets" in material
    assert "Mensagem profissional." in material
    assert client.models.chamada["config"].system_instruction
    assert client.models.chamada["config"].response_mime_type == "application/json"


# Medido ao vivo em 2026-07-27: com a ferramenta google_search, toda a família 2.0 e
# toda a 3.x devolvem 429 RESOURCE_EXHAUSTED no tier gratuito, mesmo com chave nova e
# zero uso. Trocar MODELO_BUSCA por um destes mata a fonte Google Search em silêncio —
# a exceção é engolida e a busca só segue com Jooble/Adzuna/DDGS.
MODELOS_SEM_COTA_DE_GROUNDING = frozenset(
    {
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
    }
)


# ---- regressões da Fase 3 (validação ao vivo de 2026-07-27) ----------------
#
# Os quatro casos abaixo saíram da primeira busca real. Cada um testa os dois
# lados: o que a correção precisa cortar e o que ela não pode cortar.


def test_localizacao_remoto_sem_respaldo_na_origem_vira_vazio():
    # Caso AvePoint: veio do texto livre com `localizacao: "Remoto"`, virou a #1 com
    # D2 10/10, e o anúncio real é presencial em Da Nang, no Vietnã.
    vaga = _vaga(
        "Junior DevOps Engineer",
        empresa="AvePoint",
        link="https://www.avepoint.com/careers/job-detail?gh_jid=5594102",
        descricao="Engenheiro DevOps Júnior para atuar com governança e resiliência.",
        localizacao="Remoto",
    )
    origem = _normalizar(
        "Junior DevOps Engineer AvePoint https://www.avepoint.com/careers/job-detail"
        "?gh_jid=5594102 data protection governance Da Nang Vietnam"
    )
    assert _ancorar_localizacao(vaga, origem).localizacao == ""


def test_localizacao_remoto_com_respaldo_na_origem_e_preservada():
    vaga = _vaga(
        "DevOps Jr",
        empresa="nstech",
        link="https://br.linkedin.com/jobs/view/devops-jr-remoto-at-nstech-4434183693",
        localizacao="Remoto",
    )
    origem = _normalizar(
        "DevOps Jr | Remoto nstech https://br.linkedin.com/jobs/view/"
        "devops-jr-remoto-at-nstech-4434183693 vaga 100% remota para todo o Brasil"
    )
    assert _ancorar_localizacao(vaga, origem).localizacao == "Remoto"


def test_localizacao_nao_le_o_remoto_de_uma_vaga_vizinha_no_blob():
    # O texto livre traz dezenas de vagas coladas: sem a janela, o "remoto" de
    # qualquer outra vaga da lista aprovaria esta.
    vaga = _vaga(
        "Junior DevOps Engineer",
        link="https://exemplo.com/vaga-a",
        localizacao="Remoto",
    )
    origem = _normalizar(
        "Junior DevOps Engineer https://exemplo.com/vaga-a presencial no escritorio "
        + "recheio irrelevante " * 120
        + "Outra Vaga Qualquer https://exemplo.com/vaga-b trabalho 100% remoto"
    )
    assert _ancorar_localizacao(vaga, origem).localizacao == ""


def test_localizacao_ja_vazia_continua_vazia_sem_estourar():
    vaga = _vaga("DevOps Júnior", localizacao="")
    assert _ancorar_localizacao(vaga, "qualquer texto").localizacao == ""


def test_empresa_com_nome_de_portal_vira_desconhecida():
    # Caso Nerdin: "Nerdin Vagas de TI" é o site, e o anunciante real é anônimo.
    for nome in ("Nerdin Vagas de TI", "Buscar Vagas | Emprego", "Caderno Nacional"):
        vaga = _vaga("DevOps Junior", empresa=nome)
        ancorada = _ancorar_empresa(vaga, _normalizar(f"{nome} DevOps Junior Curitiba"))
        assert ancorada.empresa == "Desconhecida", nome
        assert ancorada.confianca_empresa == "baixa", nome


def test_empresa_real_no_material_de_origem_nao_vira_desconhecida():
    vaga = _vaga("DevOps Júnior", empresa="RedFox Digital Solutions")
    origem = _normalizar("DevOps Júnior RedFox Digital Solutions Curitiba Paraná")
    assert _ancorar_empresa(vaga, origem).empresa == "RedFox Digital Solutions"


def test_titulo_que_afirma_remoto_vence_a_praca_declarada():
    # Caso BairesDev: título "Work From Home", a Adzuna carimbou "Rio de Janeiro".
    vaga = _vaga(
        "Work From Home Junior DevOps / Rd",
        empresa="BairesDev",
        localizacao="Rio de Janeiro, Estado do Rio de Janeiro",
    )
    assert _local_declarado_incompativel(vaga) is False


def test_remoto_solto_na_descricao_continua_sem_vencer_a_praca_declarada():
    # O outro lado: abrir para a descrição inteira reabriria o buraco que este
    # filtro fechou. Só título e campo de localização valem como declaração.
    vaga = _vaga(
        "Analista DevOps Júnior",
        empresa="BELTIS TECNOLOGIA",
        descricao="Ambiente moderno, com possibilidade de trabalho remoto eventual.",
        localizacao="Barueri, Estado de São Paulo",
    )
    assert _local_declarado_incompativel(vaga) is True


def test_pagina_avisa_que_nao_aceita_candidatura_conta_como_expirada():
    texto = "<html><body>Esta vaga não está mais recebendo currículos. "
    texto += "Não estamos aceitando novas candidaturas para esta vaga.</body></html>"
    assert any(m in _normalizar(texto) for m in MARCADORES_EXPIRADOS)


def test_pagina_de_vaga_ativa_nao_e_confundida_com_expirada():
    texto = "<html><body>Candidate-se agora! Estamos aceitando candidaturas.</body></html>"
    assert not any(m in _normalizar(texto) for m in MARCADORES_EXPIRADOS)


def test_empresa_no_proprio_dominio_de_carreiras_e_confiavel():
    # `avepoint.com` publicando "AvePoint" é a confirmação mais forte que existe.
    # A regra de coincidência nome/domínio reprovava justamente esses casos.
    assert _empresa_do_jsonld_confiavel("AvePoint", "www.avepoint.com") is True
    assert _empresa_do_jsonld_confiavel("Gupy", "carreiras.gupy.com.br") is True


def test_agregador_se_declarando_empregador_continua_reprovado():
    # O outro lado: num host que É agregador, a coincidência denuncia o portal.
    assert _empresa_do_jsonld_confiavel("Catho", "www.catho.com.br") is False
    assert _empresa_do_jsonld_confiavel("Adzuna", "www.adzuna.com.br") is False


def test_empregador_real_hospedado_em_agregador_continua_confiavel():
    assert _empresa_do_jsonld_confiavel("RedFox Digital Solutions", "www.adzuna.com.br") is True


def test_schema_aceita_regime_indefinido():
    """Sem este estado o Literal forçava o modelo a chutar para evitar ValidationError."""
    analise = _analise(regime="indefinido")
    assert analise.regime == "indefinido"


def test_regime_indefinido_recebe_nota_quatro_e_alerta():
    vaga = pontuar(_analise(regime="indefinido").model_copy(update={"localizacao": ""}))
    assert vaga.analise.notas.d2_regime_localizacao.nota == 4
    assert any("não declarado" in a.lower() for a in vaga.analise.alertas)


def test_relatorio_renderiza_regime_indefinido():
    texto = render_relatorio([pontuar(_analise(regime="indefinido"), "id")])
    assert "REGIME: INDEFINIDO" in texto


def test_indefinido_vale_menos_que_presencial_declarado():
    # Omissão de metadado é pior que condição ruim conhecida: com presencial em
    # Curitiba dá para decidir; sem regime nenhum, não.
    assert D2_POR_REGIME["indefinido"] < D2_POR_REGIME["presencial"]
    assert D2_POR_REGIME["presencial"] < D2_POR_REGIME["hibrido"] < D2_POR_REGIME["remoto"]


def test_alerta_de_regime_indefinido_nao_duplica_em_reprocesso():
    analise = _analise(regime="indefinido")
    primeira = pontuar(analise)
    segunda = pontuar(primeira.analise)
    assert segunda.analise.alertas.count(ALERTA_REGIME_INDEFINIDO) == 1


def test_regime_declarado_nao_ganha_o_alerta_de_omissao():
    for regime in ("remoto", "hibrido", "presencial"):
        vaga = pontuar(_analise(regime=regime))
        assert ALERTA_REGIME_INDEFINIDO not in vaga.analise.alertas, regime


def test_prompt_ensina_o_modelo_a_usar_indefinido():
    # A trava só funciona se as três camadas concordarem: schema, scoring e prompt.
    prompt = system_prompt()
    assert '"indefinido"' in prompt or "`indefinido`" in prompt
    assert f"= {D2_POR_REGIME['indefinido']}" in prompt


def test_d2_pune_presencial_fora_do_raio_de_deslocamento():
    # Caso AvePoint: presencial em Da Nang valia os mesmos 6/10 que presencial em
    # Curitiba, porque a D2 olhava só o regime.
    analise = _analise(regime="presencial").model_copy(
        update={"localizacao": "Da Nang, Vietnam"}
    )
    vaga = pontuar(analise)
    assert vaga.analise.notas.d2_regime_localizacao.nota == D2_PRESENCIAL_FORA_DO_RAIO
    assert "fora do raio" in vaga.analise.notas.d2_regime_localizacao.justificativa


def test_d2_nao_pune_presencial_em_curitiba():
    analise = _analise(regime="presencial").model_copy(
        update={"localizacao": "Curitiba, Paraná"}
    )
    assert pontuar(analise).analise.notas.d2_regime_localizacao.nota == D2_POR_REGIME["presencial"]


def test_d2_nao_pune_vaga_remota_por_distancia():
    # Remoto não é afetado por praça: é o ponto de ser remoto.
    analise = _analise(regime="remoto").model_copy(update={"localizacao": "Da Nang, Vietnam"})
    assert pontuar(analise).analise.notas.d2_regime_localizacao.nota == D2_POR_REGIME["remoto"]


def test_d2_nao_pune_localizacao_desconhecida_ou_generica():
    # Ausência de dado não é prova de distância — punir o vazio reintroduziria o
    # palpite que esta sessão inteira passou removendo.
    for local in ("", "Brasil", "Remoto", "Nacional"):
        analise = _analise(regime="presencial").model_copy(update={"localizacao": local})
        nota = pontuar(analise).analise.notas.d2_regime_localizacao.nota
        assert nota == D2_POR_REGIME["presencial"], local


def test_d2_pune_hibrido_fora_do_raio_menos_que_presencial():
    analise = _analise(regime="hibrido").model_copy(update={"localizacao": "Maringá, PR"})
    nota = pontuar(analise).analise.notas.d2_regime_localizacao.nota
    assert nota == D2_HIBRIDO_FORA_DO_RAIO
    assert D2_PRESENCIAL_FORA_DO_RAIO < D2_HIBRIDO_FORA_DO_RAIO


def test_bloco_autoritativo_injeta_os_campos_antes_do_texto():
    origem = json.dumps(
        {
            "titulo": "Junior DevOps Engineer",
            "empresa": "AvePoint",
            "localizacao": "Da Nang, Vietnam",
            "regime": "",
            "confianca_empresa": "alta",
        },
        ensure_ascii=False,
    )
    bloco = _bloco_autoritativo(origem)
    assert "IMUTÁVEIS" in bloco
    assert "Da Nang, Vietnam" in bloco
    assert "AvePoint" in bloco
    assert "(não declarado pela fonte)" in bloco  # regime vazio, marcado como tal
    assert "cite explicitamente" in bloco


def test_bloco_autoritativo_nao_quebra_com_texto_invalido():
    assert _bloco_autoritativo("{não é json}") == ""
    assert _bloco_autoritativo("") == ""


def test_campos_autoritativos_sobrescrevem_o_palpite_do_modelo():
    """Caso RedFox: JSON-LD dizia 'Curitiba (Remoto)', o modelo devolveu 'Brasil'.

    O relatório mostra a análise, então sem esta trava a extração autoritativa
    ganhava a batalha e perdia a guerra — o usuário lia o palpite.
    """
    origem = json.dumps(
        {
            "titulo": "DevOps Júnior",
            "empresa": "RedFox Digital Solutions",
            "confianca_empresa": "alta",
            "localizacao": "Curitiba (Remoto)",
        },
        ensure_ascii=False,
    )
    analise = _analise(regime="presencial", empresa="Redfox")
    imposta = _impor_campos_autoritativos(analise.model_copy(update={"localizacao": "Brasil"}), origem)
    assert imposta.localizacao == "Curitiba (Remoto)"
    assert imposta.regime == "remoto"
    assert imposta.empresa == "RedFox Digital Solutions"


def test_campos_autoritativos_nao_inventam_quando_a_origem_esta_vazia():
    origem = json.dumps({"titulo": "DevOps Júnior", "empresa": "", "localizacao": ""})
    analise = _analise(regime="hibrido", empresa="Inferida pelo modelo")
    imposta = _impor_campos_autoritativos(analise, origem)
    assert imposta.empresa == "Desconhecida"
    assert imposta.regime == "indefinido"


def test_campos_autoritativos_impoem_indefinido_quando_so_a_cidade_e_declarada():
    origem = json.dumps(
        {"empresa": "Empresa Estruturada", "confianca_empresa": "alta", "localizacao": "Curitiba, PR"}
    )
    imposta = _impor_campos_autoritativos(_analise(regime="remoto"), origem)
    assert imposta.regime == "indefinido"


def test_campos_autoritativos_preservam_modalidade_declarada_pela_fonte():
    origem = json.dumps({"localizacao": "Híbrido em Curitiba, PR"})
    imposta = _impor_campos_autoritativos(_analise(regime="remoto"), origem)
    assert imposta.regime == "hibrido"


def test_campos_autoritativos_ignoram_empresa_de_baixa_confianca():
    origem = json.dumps(
        {"empresa": "Nerdin Vagas de TI", "confianca_empresa": "baixa", "localizacao": ""}
    )
    analise = _analise(empresa="Desconhecida")
    assert _impor_campos_autoritativos(analise, origem).empresa == "Desconhecida"


def test_campos_autoritativos_sobrevivem_a_texto_corrompido():
    analise = _analise()
    assert _impor_campos_autoritativos(analise, "{não é json") is analise


def test_prompt_de_analise_proibe_inferir_regime():
    # Guarda o texto do prompt: foi a linha "infira com cautela" que produziu a
    # vaga do Vietnã classificada como 100% remota com D2 10/10.
    prompt = system_prompt()
    assert "infira com cautela" not in prompt
    assert "NÃO infira" in prompt
    assert "copie-o" in prompt
    assert "deixe o campo vazio" not in prompt


def test_prompt_descreve_as_dimensoes_que_o_schema_realmente_tem():
    """Guarda contra rótulo trocado de dimensão — a falha que não quebra nada.

    Os nomes dos campos são forçados pelo Pydantic, então um prompt que descreva D1
    como 'stack' faria o modelo escrever avaliação de stack dentro de
    `d1_crescimento`, que pesa 30%. O score sairia errado e nada acusaria.
    """
    prompt = system_prompt().lower()
    esperado = {
        "d1": "crescimento",
        "d2": "regime",
        "d3": "stack fit",
        "d4": "inglês",
        "d5": "nível real",
    }
    for dimensao, termo in esperado.items():
        linha = next(
            (linha for linha in prompt.splitlines() if linha.strip().startswith(f"**{dimensao} —")), None
        )
        assert linha is not None, f"prompt não descreve {dimensao}"
        assert termo in linha, f"{dimensao} deveria ser sobre {termo!r}, veio: {linha!r}"
    # E os pesos citados no prompt precisam bater com os de scoring.py.
    for chave, peso in PESOS.items():
        assert f"peso {int(peso * 100)}%" in prompt, f"peso de {chave} divergente no prompt"


def test_prompt_exige_json_puro_sem_cerca_de_markdown():
    prompt = system_prompt()
    assert "ETAPA 3" in prompt
    assert "AnaliseVaga" in prompt
    assert "sem cerca de markdown" in prompt.lower()


def test_redirect_para_listagem_generica_nao_vira_chave_de_dedup(monkeypatch):
    """Caso GeekHunter: a página da vaga redireciona para `/pt/vagas`.

    Sem o guarda, duas vagas distintas do mesmo portal ganham `link_final`
    idêntico, `chave_dedup()` devolve a mesma string e a Camada A as funde.
    """
    vagas = [
        _vaga("DevOps Júnior", empresa="Code Group",
              link="https://geekhunter.com.br/vagas/analista-devops-junior-em-code-group"),
        _vaga("Desenvolvedor C# Júnior", empresa="Outra Empresa",
              link="https://geekhunter.com.br/vagas/desenvolvedor-csharp-junior-em-outra"),
    ]
    monkeypatch.setattr(
        "triagem.buscador._inspecionar_link",
        lambda vaga: Inspecao(ativo=True, url_final="https://www.geekhunter.com.br/pt/vagas"),
    )
    ativas = _validar_links(vagas, 10)
    assert len(ativas) == 2, "vagas distintas foram fundidas pelo redirect genérico"
    assert all(v.link_final == "" for v in ativas)
    assert len({v.chave_dedup() for v in ativas}) == 2


def test_redirect_para_pagina_de_vaga_real_continua_virando_link_final(monkeypatch):
    vaga = _vaga("DevOps Júnior", link="https://agregador.com.br/vagas/123456")
    destino = "https://empresa.com.br/carreiras/devops-junior-987654"
    monkeypatch.setattr(
        "triagem.buscador._inspecionar_link",
        lambda v: Inspecao(ativo=True, url_final=destino),
    )
    ativas = _validar_links([vaga], 10)
    assert ativas[0].link_final == destino


def test_router_de_redirect_reconhece_so_o_vertexaisearch():
    assert _e_router_de_redirect(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/ABC"
    )
    # A exceção não pode vazar para host de conteúdo: o LinkedIn continua bloqueado.
    assert not _e_router_de_redirect("https://br.linkedin.com/jobs/view/x-4416146595")
    assert not _e_router_de_redirect("https://www.adzuna.com.br/details/1")
    assert not _e_router_de_redirect("https://cloud.google.com/vertexaisearch")


def test_resolver_router_segue_location_sem_baixar_corpo(monkeypatch):
    chamadas = []

    class _Resposta:
        def __init__(self, destino):
            self.headers = {"location": destino} if destino else {}

        @property
        def text(self):  # pragma: no cover - falha o teste se alguém ler o corpo
            raise AssertionError("o corpo do roteador não deve ser lido")

    def _head(url, **kwargs):
        chamadas.append(("head", url, kwargs.get("follow_redirects")))
        return _Resposta("https://www.adzuna.com.br/details/5815842825")

    monkeypatch.setattr("triagem.buscador.httpx.head", _head)
    monkeypatch.setattr("triagem.buscador._esperar_vez", lambda _: None)

    final = _resolver_router(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/ABC"
    )
    assert final == "https://www.adzuna.com.br/details/5815842825"
    # HEAD, e explicitamente sem seguir redirect sozinho.
    assert chamadas and chamadas[0][0] == "head" and chamadas[0][2] is False


def test_resolver_router_devolve_a_original_quando_nao_ha_location(monkeypatch):
    sem_location = type("R", (), {"headers": {}})()
    monkeypatch.setattr("triagem.buscador.httpx.head", lambda url, **k: sem_location)
    monkeypatch.setattr("triagem.buscador.httpx.get", lambda url, **k: sem_location)
    monkeypatch.setattr("triagem.buscador._esperar_vez", lambda _: None)
    url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/X"
    assert _resolver_router(url) == url


def test_resolver_router_nao_toca_em_url_que_nao_e_roteador(monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("não deveria haver requisição para host normal")

    monkeypatch.setattr("triagem.buscador.httpx.head", _explode)
    monkeypatch.setattr("triagem.buscador.httpx.get", _explode)
    url = "https://www.adzuna.com.br/details/1"
    assert _resolver_router(url) == url


def test_resumo_erro_preserva_o_motivo_e_nao_so_o_tipo(monkeypatch):
    # Sem a mensagem, 429 de cota e 404 de modelo inexistente ficam indistinguíveis.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    erro = RuntimeError("429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}")
    resumo = _resumo_erro(erro)
    assert "RuntimeError" in resumo
    assert "429" in resumo
    assert "RESOURCE_EXHAUSTED" in resumo


def test_resumo_erro_achata_e_trunca_payload_gigante(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resumo = _resumo_erro(RuntimeError("linha1\nlinha2\n" + "x" * 5000), limite=100)
    assert "\n" not in resumo
    assert resumo.endswith("…")
    assert len(resumo) < 200


def test_resumo_erro_redige_credencial_vinda_na_mensagem(monkeypatch):
    # A mensagem de erro da API pode ecoar a URL com a credencial embutida.
    monkeypatch.setenv("ADZUNA_APP_ID", "app_id_secreto_123")
    erro = RuntimeError("400 em https://api.adzuna.com/x?app_id=app_id_secreto_123&q=1")
    resumo = _resumo_erro(erro)
    assert "app_id_secreto_123" not in resumo
    assert "<ADZUNA_APP_ID>" in resumo


def test_modelo_de_busca_tem_cota_de_grounding():
    assert MODELO_BUSCA not in MODELOS_SEM_COTA_DE_GROUNDING


def test_modelo_de_busca_nao_e_o_modelo_de_analise():
    # A análise roda uma vez por vaga e precisa continuar num modelo barato; a busca
    # roda uma vez por execução. Igualar os dois joga o volume no modelo caro.
    assert MODELO_BUSCA != MODELOS["lite"]


def test_busca_web_normaliza_e_remove_links_duplicados(monkeypatch):
    descoberta = _resposta_gemini("Encontrei duas vagas atuais.")
    json_vagas = json.dumps(
        {
            "vagas": [
                {
                    "titulo": "Dev .NET Jr",
                    "empresa": "Empresa",
                        "descricao": "Vaga remota no Brasil para desenvolvimento C# e .NET com APIs REST.",
                    "link": "https://example.com/vagas/dev-net-jr-8821",
                    "origem": "site",
                    "publicada_em": "2026-07-20",
                },
                {
                    "titulo": "Dev .NET Jr duplicada",
                    "empresa": "Empresa",
                        "descricao": "Mesma vaga remota no Brasil para desenvolvimento C# e .NET.",
                    "link": "https://example.com/vagas/dev-net-jr-8821",
                    "origem": "site",
                    "publicada_em": "",
                },
            ]
        }
    )
    client = _ClienteFake([descoberta, _resposta_gemini(json_vagas)])
    monkeypatch.setattr(
        "triagem.buscador._validar_links", lambda vagas, limite, log=None: vagas[:limite]
    )
    vagas = buscar_vagas(client, "CV", "vagas C# Jr", limite=5)
    assert len(vagas) == 1
    assert vagas[0].titulo == "Dev .NET Jr"


def test_busca_texto_livre_descarta_item_invalido_sem_perder_o_lote(monkeypatch):
    """Antes, um único item fora do schema invalidava a lista inteira."""
    descoberta = _resposta_gemini("Resultados da web.")
    json_vagas = json.dumps(
        {
            "vagas": [
                {"titulo": "DevOps Jr", "empresa": "A", "descricao": "curta", "link": "x"},
                {
                    "titulo": "DevOps Júnior",
                    "empresa": "B",
                    "descricao": "Vaga remota no Brasil com Azure DevOps, Docker e CI/CD.",
                    "link": "https://exemplo.com.br/vaga-boa",
                },
            ]
        }
    )
    client = _ClienteFake([descoberta, _resposta_gemini(json_vagas)])
    monkeypatch.setattr(
        "triagem.buscador._validar_links", lambda vagas, limite, log=None: vagas[:limite]
    )
    vagas = buscar_vagas(client, "CV", "DevOps Jr", limite=5)
    assert [v.empresa for v in vagas] == ["B"]


def test_busca_jooble_devolve_vagas_estruturadas(monkeypatch):
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
                        "location": "Curitiba",
                        "type": "CLT",
                        "updated": "2026-07-24T10:00:00.1234567",
                        "link": "https://example.com/job?utm_source=segredo&id=7",
                        "snippet": "&nbsp;Azure, <b>CI/CD</b> e Docker para pipelines internos.",
                    }
                ]
            }

    monkeypatch.setattr("triagem.buscador.httpx.post", lambda *args, **kwargs: Resposta())
    vagas = _buscar_jooble("DevOps Jr", 3)
    assert len(vagas) == 1
    assert vagas[0].titulo == "DevOps Jr"
    assert vagas[0].localizacao == "Curitiba"
    # HTML do snippet limpo e parâmetro de rastreio removido do link.
    assert "&nbsp;" not in vagas[0].descricao and "<b>" not in vagas[0].descricao
    assert vagas[0].link == "https://example.com/job?id=7"


def test_busca_adzuna_nao_vaza_app_id_no_link(monkeypatch):
    """O redirect_url da Adzuna carrega `utm_source=<ADZUNA_APP_ID>`."""
    monkeypatch.setenv("ADZUNA_APP_ID", "app-id-secreto")
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
                        "location": {"display_name": "Curitiba, Paraná"},
                        "created": "2026-07-24T12:00:00Z",
                        "redirect_url": "https://www.adzuna.com.br/details/1?utm_medium=api&utm_source=app-id-secreto",
                        "description": "Azure, Docker e CI/CD em pipelines de entrega contínua.",
                    }
                ]
            }

    monkeypatch.setattr("triagem.buscador.httpx.get", lambda *args, **kwargs: Resposta())
    vagas = _buscar_adzuna("DevOps Jr", 3)
    assert len(vagas) == 1
    assert vagas[0].link == "https://www.adzuna.com.br/details/1"
    assert "app-id-secreto" not in vagas[0].link
    assert vagas[0].localizacao == "Curitiba, Paraná"


@pytest.mark.parametrize(
    "funcao,variaveis",
    [
        (_buscar_jooble, {"JOOBLE_API_KEY": "teste"}),
        (_buscar_adzuna, {"ADZUNA_APP_ID": "app", "ADZUNA_API_KEY": "key"}),
    ],
)
def test_fonte_sinaliza_resposta_json_invalida(monkeypatch, funcao, variaveis):
    for nome, valor in variaveis.items():
        monkeypatch.setenv(nome, valor)

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("JSON inválido")

    monkeypatch.setattr("triagem.buscador.httpx.get", lambda *args, **kwargs: Resposta())
    monkeypatch.setattr("triagem.buscador.httpx.post", lambda *args, **kwargs: Resposta())
    with pytest.raises(ContratoFonteAlterado):
        funcao("DevOps Jr", 3)


@pytest.mark.parametrize(
    "funcao,variaveis",
    [
        (_buscar_jooble, {"JOOBLE_API_KEY": "teste"}),
        (_buscar_adzuna, {"ADZUNA_APP_ID": "app", "ADZUNA_API_KEY": "key"}),
    ],
)
def test_adapter_propaga_erro_para_orquestrador_servir_cache(monkeypatch, funcao, variaveis):
    for nome, valor in variaveis.items():
        monkeypatch.setenv(nome, valor)

    def explode(*args, **kwargs):
        raise RuntimeError("erro que não herda de httpx.HTTPError")

    monkeypatch.setattr("triagem.buscador.httpx.get", explode)
    monkeypatch.setattr("triagem.buscador.httpx.post", explode)
    with pytest.raises(RuntimeError, match="não herda"):
        funcao("DevOps Jr", 3)


def test_redigir_segredos_mascara_chaves_do_ambiente(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "chave-super-secreta")
    texto = "falha ao chamar https://jooble.org/api/chave-super-secreta"
    assert "chave-super-secreta" not in _redigir_segredos(texto)
    assert "<JOOBLE_API_KEY>" in _redigir_segredos(texto)


def test_url_canonica_preserva_id_na_query():
    """Indeed/LinkedIn identificam a vaga na query: descartá-la fundia vagas distintas."""
    a = _url_canonica("https://br.indeed.com/viewjob?jk=AAA&utm_source=x")
    b = _url_canonica("https://br.indeed.com/viewjob?jk=BBB")
    assert a != b
    assert "utm_source" not in a


def test_limpar_url_remove_apenas_rastreio():
    limpo = _limpar_url("https://x.com.br/v?jk=1&utm_medium=api&gclid=2&page=3")
    assert "jk=1" in limpo and "page=3" in limpo
    assert "utm_medium" not in limpo and "gclid" not in limpo


def test_url_do_linkedin_estavel_entre_execucoes():
    """`position`/`pageNum` mudam a cada busca e quebravam o dedup do histórico."""
    base = "https://br.linkedin.com/jobs/view/devsecops-jr-at-x-4416146595"
    assert _url_canonica(f"{base}?position=52&pageNum=0") == _url_canonica(f"{base}?position=3&pageNum=1")


@pytest.mark.parametrize(
    "bruto,esperado_none",
    [("2026-07-24T10:00:00.1234567", False), ("2026-07-24", False), ("", True), ("ontem", True)],
)
def test_dias_desde_aceita_formatos_das_apis(bruto, esperado_none):
    assert (_dias_desde(bruto) is None) is esperado_none


def test_data_de_publicacao_muito_no_futuro_e_reprovada():
    futura = _vaga(
        "DevOps Júnior",
        publicada_em=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    )
    assert _dias_desde(futura.publicada_em) == -1
    assert _motivo_reprovacao(futura) == "antiga"


def test_area_e_decidida_pelo_titulo_e_nao_pela_descricao():
    """Regressão real: 'Data Engineer' e 'Talent Sourcer' entravam porque a descrição citava cloud."""
    fora = [
        _vaga("Junior Data Scientist", descricao="Vaga remota usando cloud, Python e modelos de ML."),
        _vaga("Talent Sourcer (Contract)", descricao="Remote role supporting cloud engineering hiring."),
        _vaga("Full Stack Developer", descricao="Vaga remota com React, Node e cloud na AWS para o time."),
    ]
    dentro = [
        _vaga("DevSecOps Júnior", link="https://exemplo.com.br/vagas/1001",
              descricao="Vaga remota no Brasil com pipelines e segurança."),
        _vaga("Cloud Engineer Jr", empresa="Outra", link="https://exemplo.com.br/vagas/1002",
              descricao="Vaga remota no Brasil com Azure e Terraform."),
    ]
    assert _selecionar_candidatas(fora, 10) == []
    assert len(_selecionar_candidatas(dentro, 10)) == 2


@pytest.mark.parametrize(
    "link",
    [
        "https://x.com/liftmycv/status/2077155032739254370",
        "https://twitter.com/vagas/status/123",
        "https://www.linkedin.com/feed/update/urn:li:activity:1",  # feed, não /jobs/view/
        "https://t.me/vagasdevops/456",
        "https://bit.ly/vaga-devops",
    ],
)
def test_post_de_rede_social_nao_e_anuncio_de_vaga(link):
    """Regressão real: um tweet de bot foi aprovado com 74/100."""
    assert _selecionar_candidatas([_vaga("DevOps Júnior", link=link)], 10) == []


@pytest.mark.parametrize(
    "link",
    [
        "https://gupy.io/v/1",
        "https://br.linkedin.com/jobs/view/devops-junior-4416146595",
        "https://www.adzuna.com.br/details/123",
    ],
)
def test_pagina_real_de_anuncio_continua_passando(link):
    assert len(_selecionar_candidatas([_vaga("DevOps Júnior", link=link)], 10)) == 1


def test_vaga_antiga_e_banco_de_talentos_sao_descartados():
    antiga = _vaga("DevOps Júnior", publicada_em="2020-01-01T00:00:00Z")
    pool = _vaga("Banco de Talentos - Desenvolvedor .NET C#")
    assert _selecionar_candidatas([antiga, pool], 10) == []


def test_prefiltro_penaliza_experiencia_em_ingles_e_senioridade_na_descricao():
    ingles = _vaga(
        "Cloud Engineer",
        descricao="Remote position requiring 6+ years of experience with AWS and Kubernetes.",
    )
    senior_na_descricao = _vaga(
        "Engenheiro DevOps",
        descricao="Buscamos profissional senior para liderar a plataforma de CI/CD em Azure.",
    )
    assert _pontuacao_preliminar(ingles) < 8
    assert _pontuacao_preliminar(senior_na_descricao) < 8


def test_localizacao_declarada_vence_texto_do_anuncio():
    """Regressão real: a Adzuna dizia 'Recife' e o modelo classificou como remoto.

    Regra estrita: a praça declarada pela fonte vence o texto. Nem uma descrição
    dizendo "100% remota" resgata uma vaga carimbada com outra cidade — era assim
    que a vaga presencial em Recife entrava e ainda levava 10/10 no D2.
    """
    presencial_recife = _vaga(
        "Desenvolvedor Back-end Júnior com Foco em DevOps",
        descricao="Requisitos: Java, Spring, JSF, Tomcat, WebServices e PostgreSQL na sede.",
        localizacao="Recife, Pernambuco",
    )
    remota_recife = _vaga(
        "Desenvolvedor .NET Júnior",
        descricao="Vaga 100% remota para todo o Brasil com C#, .NET e Azure DevOps.",
        localizacao="Recife, Pernambuco",
    )
    curitiba = _vaga("Estágio DevOps", localizacao="Curitiba, Paraná")
    generica = _vaga("Estágio DevOps", localizacao="Brasil")
    assert _local_declarado_incompativel(presencial_recife)
    assert _local_declarado_incompativel(remota_recife)
    assert not _local_declarado_incompativel(curitiba)
    assert not _local_declarado_incompativel(generica)


def test_vaga_com_restricao_explicita_aos_eua_e_reprovada():
    restrita = _vaga(
        "DevOps Engineer Junior",
        link="https://boards.greenhouse.io/x/jobs/1",
        descricao="Remote role, US only. Must be located in the United States to apply.",
    )
    aberta = _vaga(
        "DevOps Engineer Junior",
        link="https://boards.greenhouse.io/x/jobs/2",
        descricao="Worldwide remote role hiring in Brazil and LATAM, Docker and Terraform.",
    )
    assert not _localizacao_compativel(restrita)
    assert _localizacao_compativel(aberta)


def test_retentativa_repete_erro_transitorio_e_desiste_de_erro_definitivo(monkeypatch):
    monkeypatch.setattr("triagem.analisador.time.sleep", lambda _: None)

    class ClienteInstavel:
        def __init__(self, erros):
            self.erros = erros
            self.chamadas = 0
            self.models = self

        def generate_content(self, **kwargs):
            self.chamadas += 1
            if self.erros:
                raise self.erros.pop(0)
            return "ok"

    transitorio = ClienteInstavel([RuntimeError("429 RESOURCE_EXHAUSTED")])
    assert gerar_com_retentativa(transitorio, model="m") == "ok"
    assert transitorio.chamadas == 2

    definitivo = ClienteInstavel([ValueError("400 INVALID_ARGUMENT")])
    with pytest.raises(ValueError):
        gerar_com_retentativa(definitivo, model="m")
    assert definitivo.chamadas == 1


def test_chamadas_de_api_tem_timeout_explicito():
    """Sem timeout, uma conexão pendurada trava a thread e o lote nunca fecha."""
    client = _ClienteFake(_resposta_gemini(_analise().model_dump_json()))
    analisar_vaga(client, "vaga de teste")
    assert client.models.chamada["config"].http_options.timeout == TIMEOUT_ANALISE_MS
    client = _ClienteFake(_resposta_gemini(_material_json("curriculo")))
    gerar_material(client, "curriculo", "vaga", _analise().model_dump())
    assert client.models.chamada["config"].http_options.timeout == TIMEOUT_ANALISE_MS


def test_material_cv_rejeita_evidencia_inventada():
    client = _ClienteFake(_resposta_gemini(_material_json("certificação inexistente")))
    with pytest.raises(ValueError, match="sem evidência literal"):
        gerar_material(client, "meu cv real", "vaga", _analise().model_dump())


def test_prompts_sao_lidos_do_disco_uma_vez_so():
    """Antes, cada vaga analisada relia system_prompt.md — I/O redundante no hot path."""
    system_prompt.cache_clear()
    primeiro = system_prompt()
    system_prompt()
    assert system_prompt.cache_info().misses == 1
    assert system_prompt.cache_info().hits == 1
    assert primeiro.strip()


def test_link_com_erro_de_rede_repetido_e_considerado_morto(monkeypatch):
    tentativas = {"n": 0}

    def sempre_falha(link):
        tentativas["n"] += 1
        raise httpx.ConnectError("dns")

    monkeypatch.setattr("triagem.buscador._obter", sempre_falha)
    assert _inspecionar_link(_vaga("DevOps Jr")).ativo is False
    assert tentativas["n"] == 2  # uma segunda chance antes de descartar


def test_link_com_soluco_de_rede_sobrevive(monkeypatch):
    class Resposta:
        status_code = 200
        url = "https://exemplo.com.br/v"
        text = "DevOps Jr — candidate-se agora para esta vaga aberta"

    chamadas = {"n": 0}

    def instavel(link):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise httpx.ReadTimeout("lento")
        return Resposta()

    monkeypatch.setattr("triagem.buscador._obter", instavel)
    assert _inspecionar_link(_vaga("DevOps Jr")).ativo is True


def test_url_invalida_nao_derruba_a_validacao(monkeypatch):
    def url_ruim(link):
        raise httpx.InvalidURL("host inválido")

    monkeypatch.setattr("triagem.buscador._obter", url_ruim)
    assert _inspecionar_link(_vaga("DevOps Jr")).ativo is True


@pytest.mark.parametrize("titulo", ["DevOps Engineer Mid-Level", "Cloud Team Lead", ".NET Midlevel"])
def test_senioridade_bloqueia_termos_em_ingles(titulo):
    assert _pontuacao_preliminar(_vaga(titulo)) == -100


def test_vaga_sem_patrocinio_de_visto_e_reprovada():
    sem_visto = _vaga(
        "DevOps Engineer Junior",
        link="https://boards.greenhouse.io/x/jobs/9",
        descricao="Fully remote position. We do not sponsor visas for this role at this time.",
    )
    assert not _localizacao_compativel(sem_visto)


def test_cv_base_remove_blocos_marcados_como_privados():
    """O bloco marcado nunca é enviado à API do Gemini."""
    texto = (
        "# CV\n\n"
        "<!-- PRIVADO -->\nCPF: 000.000.000-00\nTelefone: (41) 90000-0000\n<!-- /PRIVADO -->\n"
        "## Experiência\n\nEstágio DevSecOps na Volvo.\n"
    )
    limpo, removidos = remover_blocos_privados(texto)
    assert removidos == 1
    assert "CPF" not in limpo and "90000" not in limpo
    assert "Estágio DevSecOps na Volvo." in limpo


def test_cv_base_sem_marcadores_fica_intacto():
    texto = "# CV\n\nEstágio DevSecOps na Volvo.\n"
    assert remover_blocos_privados(texto) == (texto, 0)


# ---------------------------------------------------------------- cache e circuito

def test_cache_serve_dentro_do_ttl_e_expira_depois():
    estado = cache.carregar()
    cache.guardar(estado, "Jooble", "consulta", [{"titulo": "x"}])
    dados, idade = cache.obter(estado, "Jooble", "consulta")
    assert dados == [{"titulo": "x"}] and idade is not None

    # Envelhece a entrada além do TTL da fonte.
    chave = next(iter(estado["entradas"]))
    vencido = datetime.now(timezone.utc) - timedelta(seconds=cache.TTL_SEGUNDOS["Jooble"] + 60)
    estado["entradas"][chave]["gravado_em"] = vencido.isoformat(timespec="seconds")

    assert cache.obter(estado, "Jooble", "consulta")[0] is None
    assert cache.obter_vencido(estado, "Jooble", "consulta")[0] == [{"titulo": "x"}]


def test_cache_corrompido_nao_derruba_a_busca(tmp_path, monkeypatch):
    arquivo = tmp_path / "cache_busca.json"
    arquivo.write_text("{ isso não é json", encoding="utf-8")
    monkeypatch.setattr(cache, "ARQUIVO", arquivo)
    assert cache.carregar() == {"entradas": {}, "circuitos": {}}


def test_cache_json_valido_com_shape_errado_nao_derruba(tmp_path, monkeypatch):
    arquivo = tmp_path / "cache_busca.json"
    arquivo.write_text('{"entradas": [], "circuitos": {}}', encoding="utf-8")
    monkeypatch.setattr(cache, "ARQUIVO", arquivo)
    assert cache.carregar() == {"entradas": {}, "circuitos": {}}


def test_circuito_abre_apos_falhas_consecutivas_e_fecha_no_sucesso():
    estado = cache.carregar()
    for _ in range(cache.FALHAS_PARA_ABRIR - 1):
        assert cache.registrar_falha(estado, "Google Search") is False
        assert cache.circuito_aberto(estado, "Google Search") is None

    assert cache.registrar_falha(estado, "Google Search") is True
    restante = cache.circuito_aberto(estado, "Google Search")
    assert restante is not None and 0 < restante <= cache.HORAS_CIRCUITO_ABERTO

    cache.registrar_sucesso(estado, "Google Search")
    assert cache.circuito_aberto(estado, "Google Search") is None


def test_fonte_estruturada_usa_cache_em_vez_de_chamar_a_api():
    estado = cache.carregar()
    cache.guardar(
        estado, "Jooble", "pedido|20",
        [_vaga("DevOps Júnior", link="https://x.com.br/1").model_dump()],
    )
    chamadas = {"n": 0}

    def nunca_chamada(pedido, limite):
        chamadas["n"] += 1
        return []

    linhas = []
    vagas = _fonte_estruturada(
        "Jooble", nunca_chamada, "pedido", 20, estado, "pedido|20", True, linhas.append
    )
    assert chamadas["n"] == 0
    assert len(vagas) == 1
    assert "cache" in linhas[0]


def test_fonte_sem_resposta_cai_no_cache_vencido():
    estado = cache.carregar()
    cache.guardar(
        estado, "Adzuna", "pedido|20",
        [_vaga("Cloud Engineer Jr", link="https://x.com.br/2").model_dump()],
    )
    chave = next(iter(estado["entradas"]))
    vencido = datetime.now(timezone.utc) - timedelta(days=3)
    estado["entradas"][chave]["gravado_em"] = vencido.isoformat(timespec="seconds")

    linhas = []
    vagas = _fonte_estruturada(
        "Adzuna", lambda pedido, limite: [], "pedido", 20,
        estado, "pedido|20", True, linhas.append,
    )
    assert len(vagas) == 1
    assert "cache" in linhas[0]


def test_sem_cache_forca_consulta_fresca_e_ignora_entradas_gravadas():
    """--sem-cache tem que chamar a fonte E não servir entrada fresca nem vencida."""
    estado = cache.carregar()
    cache.guardar(estado, "Jooble", "pedido|20", [_vaga("Vaga Do Cache DevOps").model_dump()])
    chamadas = {"n": 0}

    def fonte_viva(pedido, limite):
        chamadas["n"] += 1
        return [_vaga("DevOps Júnior fresca", link="https://x.com.br/novo")]

    vagas = _fonte_estruturada(
        "Jooble", fonte_viva, "pedido", 20, estado, "pedido|20", False, lambda _: None
    )
    assert chamadas["n"] == 1                       # a fonte foi realmente consultada
    assert vagas[0].titulo == "DevOps Júnior fresca"  # e não a entrada do cache

    # Mesmo quando a fonte não responde, --sem-cache não pode cair no cache vencido.
    vazio = _fonte_estruturada(
        "Jooble", lambda pedido, limite: [], "pedido", 20,
        estado, "pedido|20", False, lambda _: None,
    )
    assert vazio == []


def test_metabusca_espaca_consultas_e_para_quando_ja_tem_material(monkeypatch):
    """Em rajada o DDG estrangula a última consulta; medido 25 vs 32 resultados."""
    pausas = []
    monkeypatch.setattr("triagem.buscador.time.sleep", pausas.append)
    consultas = []

    class DDGSFake:
        def text(self, consulta, **kwargs):
            consultas.append(consulta)
            n = len(consultas)
            return [
                {"title": f"Vaga {n}-{i}", "href": f"https://x.com.br/{n}-{i}", "body": "descrição"}
                for i in range(8)
            ]

    monkeypatch.setattr("triagem.buscador.DDGS", DDGSFake)

    _, fontes = _busca_metasearch("pedido", 30)
    assert len(consultas) == 4                      # nenhuma consulta pulada
    assert len(fontes) == 32                        # 4 x 8, sem perder resultado
    assert len(pausas) == 3                         # pausa entre elas, não antes da 1ª
    assert all(1.5 <= p <= 3.0 for p in pausas)

    # Com limite baixo, para assim que já tem material suficiente.
    consultas.clear()
    _busca_metasearch("pedido", 8)
    assert len(consultas) < 4


def test_metabusca_cai_no_cache_vencido_quando_o_ddgs_bloqueia():
    """O DuckDuckGo bloqueia rajadas; sem cache a busca perde a fonte inteira."""
    estado = cache.carregar()
    cache.guardar(
        estado, "Metabusca DDGS", "pedido|20",
        {"texto": "Título: DevOps Jr\nLink: https://x.com.br/1", "fontes": ["- resultado: a"]},
    )
    chave = next(iter(estado["entradas"]))
    velho = datetime.now(timezone.utc) - timedelta(hours=5)
    estado["entradas"][chave]["gravado_em"] = velho.isoformat(timespec="seconds")

    linhas = []
    texto, fontes = _texto_livre_com_cache(
        "Metabusca DDGS", lambda: ("", []), estado, "pedido|20", True, linhas.append
    )
    assert "DevOps Jr" in texto and fontes == ["- resultado: a"]
    assert any("cache" in linha for linha in linhas)


def test_metabusca_usa_cache_fresco_sem_consultar_o_ddgs():
    estado = cache.carregar()
    cache.guardar(
        estado, "Metabusca DDGS", "pedido|20",
        {"texto": "Título: DevOps Jr", "fontes": ["- resultado: a"]},
    )
    chamadas = {"n": 0}

    def nunca(*_):
        chamadas["n"] += 1
        return "", []

    texto, _ = _texto_livre_com_cache(
        "Metabusca DDGS", nunca, estado, "pedido|20", True, lambda _: None
    )
    assert chamadas["n"] == 0 and "DevOps Jr" in texto


def test_metabusca_que_explode_nao_derruba_a_busca():
    def explode():
        raise RuntimeError("ddgs fora do ar")

    texto, fontes = _texto_livre_com_cache(
        "Metabusca DDGS", explode, cache.carregar(), "p|1", True, lambda _: None
    )
    assert (texto, fontes) == ("", [])


def test_cache_poda_entradas_alem_da_retencao():
    """TTL decide o que é servido; sem poda o arquivo cresceria para sempre."""
    estado = cache.carregar()
    cache.guardar(estado, "Jooble", "recente", [{"t": 1}])
    cache.guardar(estado, "Jooble", "antiga", [{"t": 2}])
    muito_velha = datetime.now(timezone.utc) - timedelta(days=cache.DIAS_RETENCAO + 5)
    estado["entradas"][cache._chave("Jooble", "antiga")]["gravado_em"] = (
        muito_velha.isoformat(timespec="seconds")
    )

    assert cache.podar(estado) == 1
    assert len(estado["entradas"]) == 1
    assert cache.obter(estado, "Jooble", "recente")[0] == [{"t": 1}]


def test_cache_poda_sozinho_ao_carregar(tmp_path, monkeypatch):
    arquivo = tmp_path / "cache_busca.json"
    monkeypatch.setattr(cache, "ARQUIVO", arquivo)
    estado = cache.carregar()
    cache.guardar(estado, "Adzuna", "antiga", [{"t": 1}])
    chave = next(iter(estado["entradas"]))
    velha = datetime.now(timezone.utc) - timedelta(days=cache.DIAS_RETENCAO + 1)
    estado["entradas"][chave]["gravado_em"] = velha.isoformat(timespec="seconds")
    cache.salvar(estado)

    assert cache.carregar()["entradas"] == {}


def test_esvaziar_zera_entradas_e_circuitos():
    estado = cache.carregar()
    cache.guardar(estado, "Jooble", "x", [{"t": 1}])
    for _ in range(cache.FALHAS_PARA_ABRIR):
        cache.registrar_falha(estado, "Google Search")

    assert cache.esvaziar(estado) == 1
    assert estado["entradas"] == {} and estado["circuitos"] == {}
    assert cache.circuito_aberto(estado, "Google Search") is None


def test_cli_expoe_limpar_cache():
    parser = _montar_parser()
    assert parser.parse_args(["limpar-cache"]).tudo is False
    assert parser.parse_args(["limpar-cache", "--tudo"]).tudo is True


def test_circuito_aberto_pula_a_chamada_ao_google_search(monkeypatch):
    """Com a cota esgotada, insistir só gasta latência para receber o mesmo 429."""
    estado = cache.carregar()
    for _ in range(cache.FALHAS_PARA_ABRIR):
        cache.registrar_falha(estado, "Google Search")
    cache.salvar(estado)

    chamadas = {"n": 0}

    class ClienteContador:
        def __init__(self):
            self.models = self

        def generate_content(self, **kwargs):
            chamadas["n"] += 1
            raise RuntimeError("não deveria ter sido chamado")

    monkeypatch.setattr("triagem.buscador._buscar_jooble", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._buscar_adzuna", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._busca_metasearch", lambda pedido, limite: ("", []))

    linhas = []
    buscar_vagas(ClienteContador(), "cv", "pedido", limite=3, log=linhas.append)
    assert chamadas["n"] == 0
    assert any("CIRCUITO ABERTO" in linha for linha in linhas)


def test_adzuna_limita_idade_do_anuncio_na_propria_consulta(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "a")
    monkeypatch.setenv("ADZUNA_API_KEY", "b")
    capturado = {}

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def espiar(url, **kwargs):
        capturado.update(kwargs.get("params", {}))
        return Resposta()

    monkeypatch.setattr("triagem.buscador.httpx.get", espiar)
    _buscar_adzuna("DevOps Jr", 5)
    assert capturado["max_days_old"] == DIAS_MAXIMOS_ANUNCIO


# ---------------------------------------------------------------- dedup e enriquecimento

def test_chave_dedup_cai_no_link_original_sem_redirect():
    vaga = _vaga("DevOps Jr", link="https://x.com.br/1")
    assert vaga.chave_dedup() == "https://x.com.br/1"
    assert vaga.model_copy(update={"link_final": "https://y.com.br/2"}).chave_dedup() == (
        "https://y.com.br/2"
    )


def test_texto_visivel_descarta_script_e_style():
    html_bruto = "<html><script>var x=1</script><style>p{}</style><p>Vaga real</p></html>"
    visivel = _texto_visivel(html_bruto)
    assert "Vaga real" in visivel
    assert "var x" not in visivel and "p{}" not in visivel


def test_cli_aceita_novas_flags_de_busca():
    parser = _montar_parser()
    args = parser.parse_args(["buscar", "vagas", "--sem-cache", "--pesos", "d1=0.2"])
    assert args.sem_cache is True and args.pesos == "d1=0.2"
    assert parser.parse_args(["buscar", "--testar-fontes"]).testar_fontes is True
    # Retrocompatibilidade do CLI continua valendo.
    assert parser.parse_args(["analisar", "vagas.json"]).arquivo == "vagas.json"




def test_mesma_vaga_em_duas_fontes_vira_uma_entrada(monkeypatch):
    """Jooble e Adzuna redirecionam para o mesmo anúncio: 2 URLs, 1 vaga."""
    jooble = _vaga("DevOps Júnior", link="https://jooble.org/jdp/123")
    adzuna = _vaga("DevOps Júnior", link="https://www.adzuna.com.br/details/456")
    destino = "https://empresa.com.br/carreiras/devops-junior"

    monkeypatch.setattr(
        "triagem.buscador._inspecionar_link",
        lambda vaga: Inspecao(ativo=True, url_final=destino),
    )
    resultado = _validar_links([jooble, adzuna], 10)
    assert len(resultado) == 1
    assert resultado[0].chave_dedup() == destino


def test_enriquecimento_substitui_descricao_truncada():
    vaga = _vaga(
        "DevOps Júnior",
        descricao="Requisitos: experiencia com pipelines de entrega continua e Docker",
    )
    pagina = (
        "<html><head><style>.x{color:red}</style></head><body><nav>menu</nav>"
        "<p>Requisitos: experiencia com pipelines de entrega continua e Docker, "
        "Kubernetes, Terraform e Azure. Diferenciais: certificacao AZ-900. "
        "Beneficios: vale refeicao, plano de saude e auxilio home office. "
        "Contratacao CLT com salario a combinar conforme experiencia do candidato.</p>"
        "</body></html>"
    )
    enriquecida = _enriquecer_descricao(vaga, pagina)
    assert enriquecida.descricao_completa is True
    assert "Kubernetes" in enriquecida.descricao
    assert "color:red" not in enriquecida.descricao


def test_enriquecimento_recusa_pagina_sem_ancora():
    """Sem a âncora, estaríamos colando o menu do portal no lugar dos requisitos."""
    vaga = _vaga("DevOps Júnior", descricao="Requisitos muito especificos de pipeline e Docker")
    pagina = "<html><body>" + ("Página institucional sobre a empresa. " * 40) + "</body></html>"
    assert _enriquecer_descricao(vaga, pagina).descricao == vaga.descricao


def test_empresa_sem_respaldo_no_texto_vira_desconhecida():
    origem = _normalizar("Vaga de DevOps Júnior na Contoso Brasil, remoto.")
    real = _ancorar_empresa(_vaga("DevOps Jr", empresa="Contoso Brasil"), origem)
    inventada = _ancorar_empresa(_vaga("DevOps Jr", empresa="Sylision"), origem)
    assert real.empresa == "Contoso Brasil" and real.confianca_empresa == "media"
    assert inventada.empresa == "Desconhecida" and inventada.confianca_empresa == "baixa"


# ---------------------------------------------------------------- robustez

def test_timeout_em_todas_as_fontes_devolve_lista_vazia_sem_traceback(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    monkeypatch.setenv("ADZUNA_APP_ID", "a")
    monkeypatch.setenv("ADZUNA_API_KEY", "b")

    def estourou(*args, **kwargs):
        raise httpx.ReadTimeout("tempo esgotado")

    monkeypatch.setattr("triagem.buscador.httpx.get", estourou)
    monkeypatch.setattr("triagem.buscador.httpx.post", estourou)
    monkeypatch.setattr("triagem.buscador._busca_metasearch", lambda pedido, limite: ("", []))

    class ClienteQueFalha:
        def __init__(self):
            self.models = self

        def generate_content(self, **kwargs):
            raise httpx.ReadTimeout("tempo esgotado")

    linhas = []
    assert buscar_vagas(ClienteQueFalha(), "cv", "pedido", limite=3, log=linhas.append) == []
    assert any("Jooble" in linha for linha in linhas)
    assert any("Google Search" in linha for linha in linhas)


def test_nenhuma_credencial_vaza_para_relatorio_historico_ou_csv(tmp_path, monkeypatch):
    """Regressão de segurança: o app_id da Adzuna já vazou por `utm_source`."""
    monkeypatch.setenv("ADZUNA_APP_ID", "APPID-SECRETO")
    monkeypatch.setenv("ADZUNA_API_KEY", "APPKEY-SECRETO")

    class Resposta:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [{
                    "title": "DevOps Júnior",
                    "company": {"display_name": "Empresa"},
                    "location": {"display_name": "Curitiba"},
                    "created": "2026-07-24T12:00:00Z",
                    "redirect_url": (
                        "https://www.adzuna.com.br/details/1"
                        "?utm_medium=api&utm_source=APPID-SECRETO"
                    ),
                    "description": "Azure, Docker e CI/CD em pipelines de entrega contínua.",
                }]
            }

    monkeypatch.setattr("triagem.buscador.httpx.get", lambda *a, **k: Resposta())
    vagas = _buscar_adzuna("DevOps Jr", 5)

    md = tmp_path / "rel.md"
    csv_saida = tmp_path / "rel.csv"
    exportar([pontuar(_analise(titulo=vagas[0].titulo), "id1")], str(md))
    exportar([pontuar(_analise(titulo=vagas[0].titulo), "id1")], str(csv_saida))

    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "historico.json")
    hist = historico.carregar()
    historico.registrar(hist, pontuar(_analise(), "id1"), vagas[0].model_dump_json())
    historico.salvar(hist)

    artefatos = [md, csv_saida, tmp_path / "historico.json"]
    for artefato in artefatos:
        conteudo = artefato.read_text(encoding="utf-8-sig")
        assert "APPID-SECRETO" not in conteudo
        assert "APPKEY-SECRETO" not in conteudo


# ---------------------------------------------------------------- pesos

def test_pesos_personalizados_mudam_o_score():
    pesos = parse_pesos("d1=0.15,d2=0.30,d3=0.25,d4=0.15,d5=0.15")
    # 9*.15 + 10*.30 + 9*.25 + 9*.15 + 10*.15 = 9.45 -> 94.5
    assert pontuar(_analise(), "id", pesos).score_final == 94.5
    assert pontuar(_analise(), "id").score_final == 93.5  # padrão intacto


@pytest.mark.parametrize(
    "texto,erro",
    [
        ("d1=0.5", "somar 1.0"),
        ("d9=0.2,d1=0.1", "desconhecida"),
        ("d1=muito", "inválido"),
    ],
)
def test_pesos_invalidos_dao_erro_claro(texto, erro):
    with pytest.raises(ValueError, match=erro):
        parse_pesos(texto)


def test_prefiltro_remove_senior_sem_fundir_requisicoes_por_titulo():
    def vaga(titulo, empresa, link, descricao):
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
            "https://portal1.example/vagas/net-junior-4471",
            "Vaga remota no Brasil para Júnior com C#, .NET e APIs REST.",
        ),
        vaga(
            "Desenvolvedor .NET Jr",
            "Empresa B",
            "https://portal2.example/vagas/mesma-vaga-net-jr-9912",
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
    assert len(selecionadas) == 2
    assert {vaga.empresa for vaga in selecionadas} == {"Empresa B"}


def test_politica_e_reaplicada_a_data_descoberta_no_enriquecimento():
    antiga = _vaga(
        "DevOps Júnior",
        publicada_em=(
            datetime.now(timezone.utc) - timedelta(days=DIAS_MAXIMOS_ANUNCIO + 1)
        ).isoformat(),
    )
    assert _motivo_reprovacao(antiga, validar_url=False) == "antiga"


def test_jsonld_com_varias_vagas_escolhe_titulo_correspondente():
    html = """
    <script type="application/ld+json">
    {"@graph": [
      {"@type": "JobPosting", "title": "Data Engineer", "url": "https://x.test/jobs/1",
       "jobLocation": {"address": {"addressLocality": "Recife"}}},
      {"@type": "JobPosting", "title": "DevOps Junior", "url": "https://x.test/jobs/2",
       "jobLocation": {"address": {"addressLocality": "Curitiba"}}}
    ]}
    </script>
    """
    anuncio = _extrair_jobposting(
        html, "x.test", "https://x.test/jobs/desconhecida", "DevOps Junior"
    )
    assert anuncio.titulo == "DevOps Junior"
    assert anuncio.localidade == "Curitiba"


def test_jsonld_ambiguo_falha_fechado():
    html = """
    <script type="application/ld+json">
    [{"@type": "JobPosting", "title": "DevOps Junior A"},
     {"@type": "JobPosting", "title": "DevOps Junior B"}]
    </script>
    """
    assert _extrair_jobposting(html, "x.test", "", "DevOps Junior").vazio()


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


def test_schema_estrito_rejeita_coercao_e_campos_extras():
    with pytest.raises(ValidationError):
        Dimensao(nota="9", justificativa="ok")
    with pytest.raises(ValidationError):
        Notas(**{**_analise().notas.model_dump(), "inventada": {"nota": 10, "justificativa": "x"}})


def test_host_de_rede_rejeita_enderecos_nao_globais(monkeypatch):
    _host_e_seguro.cache_clear()
    monkeypatch.setattr("triagem.buscador.socket.getaddrinfo", lambda *args, **kwargs: [
        (None, None, None, None, ("127.0.0.1", 0)),
    ])
    assert not _host_e_seguro("agregador.teste")


def test_host_de_rede_aceita_apenas_resolucoes_totalmente_globais(monkeypatch):
    _host_e_seguro.cache_clear()
    monkeypatch.setattr("triagem.buscador.socket.getaddrinfo", lambda *args, **kwargs: [
        (None, None, None, None, ("8.8.8.8", 0)),
        (None, None, None, None, ("1.1.1.1", 0)),
    ])
    assert _host_e_seguro("agregador.teste")


def test_redirect_para_rede_privada_e_bloqueado_antes_da_segunda_requisicao(monkeypatch):
    chamadas = []

    class Resposta:
        status_code = 302
        headers = {"location": "http://127.0.0.1/admin"}

    monkeypatch.setattr(
        "triagem.buscador._url_de_rede_segura", lambda url: "127.0.0.1" not in url
    )
    monkeypatch.setattr("triagem.buscador._permitido_por_robots", lambda url: True)
    monkeypatch.setattr("triagem.buscador._esperar_vez", lambda host: None)

    def redireciona(url, **kwargs):
        chamadas.append((url, kwargs["follow_redirects"]))
        return Resposta()

    monkeypatch.setattr("triagem.buscador.httpx.get", redireciona)
    with pytest.raises(httpx.InvalidURL):
        _obter("https://agregador.teste/vaga/123")
    assert chamadas == [("https://agregador.teste/vaga/123", False)]


def test_schema_exige_notas_em_vaga_aprovada():
    dados = _analise().model_dump()
    dados["notas"] = None
    with pytest.raises(ValidationError, match="vaga aprovada deve ter notas"):
        AnaliseVaga.model_validate(dados)


@pytest.mark.parametrize(
    "regime,esperado",
    [("remoto", 10), ("hibrido", 7), ("presencial", 6), ("indefinido", 4)],
)
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


def test_delta_sync_fecha_apenas_vaga_nova_removida_do_ats():
    origem = json.dumps(
        {"ats_provedor": "greenhouse", "ats_token": "acme", "ats_job_id": "10"}
    )
    aplicada = json.dumps(
        {"ats_provedor": "greenhouse", "ats_token": "acme", "ats_job_id": "20"}
    )
    hist = {"nova": {"status": "novo", "texto": origem}, "aplicada": {"status": "aplicado", "texto": aplicada}}
    assert historico.marcar_fechadas_por_ats(hist, "greenhouse", "acme", {"30"}) == ["nova"]
    assert hist["nova"]["status"] == "fechada"
    assert "fechada_pelo_ats_em" in hist["aplicada"]
    assert hist["aplicada"]["status"] == "aplicado"


def test_historico_buscar_prefixo_ambiguo_ou_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "historico.json")
    hist = {"aa11": {}, "aa22": {}}
    with pytest.raises(KeyError, match="ambíguo"):
        historico.buscar(hist, "aa")
    with pytest.raises(KeyError, match="Nenhuma"):
        historico.buscar(hist, "zz")


def test_historico_respeita_triagem_historico_definido_no_env(tmp_path, monkeypatch):
    """O módulo é importado antes do load_dotenv(); sem re-resolver, a variável era ignorada."""
    alvo = tmp_path / "outro.json"
    monkeypatch.setenv("TRIAGEM_HISTORICO", str(alvo))
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "errado.json")
    historico.aplicar_config_do_ambiente()
    assert historico.ARQUIVO == alvo

    monkeypatch.delenv("TRIAGEM_HISTORICO")
    historico.aplicar_config_do_ambiente()
    assert historico.ARQUIVO == historico.PADRAO


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


def test_exportar_csv_neutraliza_formula(tmp_path):
    caminho = tmp_path / "rel.csv"
    exportar([pontuar(_analise(empresa="=1+1", titulo="@SUM(A1)"), "id1")], str(caminho))
    conteudo = caminho.read_text(encoding="utf-8-sig")
    assert ",'=1+1,'@SUM(A1)," in conteudo


def test_exportar_extensao_invalida(tmp_path):
    with pytest.raises(ValueError, match="não suportada"):
        exportar([pontuar(_analise())], str(tmp_path / "rel.pdf"))


# ------------------------------------------------- regressões da validação v2.1

def test_item_sem_dados_nao_derruba_o_resto_do_lote():
    """Um `{}` no meio do JSON invalidava o arquivo inteiro e nada era analisado."""
    conteudo = json.dumps([
        {"titulo": "DevOps Júnior", "link": "https://gupy.io/1", "descricao": "Pipelines e Docker."},
        {"titulo": "DevSecOps Jr", "link": "https://gupy.io/2", "descricao": "Segurança em CI/CD."},
        {},
    ])
    avisos = []
    vagas = carregar_vagas(conteudo, log=avisos.append)
    assert len(vagas) == 2
    assert any("descartado" in aviso for aviso in avisos)


def test_redirect_para_post_de_rede_social_e_descartado(monkeypatch):
    """O pré-filtro julga o link anunciado; o encurtador só mostra o destino no redirect."""
    monkeypatch.setattr(
        "triagem.buscador._inspecionar_link",
        lambda vaga: Inspecao(ativo=True, url_final="https://x.com/bot/status/123"),
    )
    vaga = _vaga("DevOps Júnior", link="https://portal-desconhecido.example/vaga/1")
    linhas = []
    assert _validar_links([vaga], 10, linhas.append) == []
    assert any("redirect terminou fora" in linha for linha in linhas)


def test_redirect_para_anuncio_legitimo_continua_passando(monkeypatch):
    monkeypatch.setattr(
        "triagem.buscador._inspecionar_link",
        lambda vaga: Inspecao(ativo=True, url_final="https://acme.gupy.io/jobs/998877"),
    )
    vaga = _vaga("DevOps Júnior", link="https://jooble.org/jdp/-1")
    assert len(_validar_links([vaga], 10)) == 1


@pytest.mark.parametrize(
    "link",
    ["https://ow.ly/abc", "https://buff.ly/3xy", "https://is.gd/x", "https://rb.gy/z",
     "https://shorturl.at/abc", "https://cutt.ly/a"],
)
def test_encurtadores_conhecidos_nao_passam_do_prefiltro(link):
    assert not _host_de_anuncio(_vaga("DevOps Júnior", link=link))


@pytest.mark.parametrize(
    "frase",
    [
        "Vaga 100% remota para todo o Brasil.",
        "Trabalho remoto com encontros trimestrais.",
        "Atuação em home office, sem necessidade de comparecer.",
        "Fully remote position open to Brazil.",
    ],
)
def test_descricao_remota_nao_resgata_praca_declarada(frase):
    """O texto não desempata: quem manda é o campo de localização da fonte."""
    vaga = _vaga("DevOps Júnior", descricao=f"Requisitos de CI/CD. {frase}",
                 localizacao="Recife, Pernambuco")
    assert _local_declarado_incompativel(vaga)


@pytest.mark.parametrize(
    "local",
    ["Remoto", "Brasil", "Home Office", "Remoto - Brasil", "São Paulo, SP (Remoto)",
     "Belo Horizonte - MG / Home Office",
     # plurais: sem eles a fonte declarava remoto e a vaga era reprovada mesmo assim
     "Vagas remotas", "Recife, PE - vagas remotas", "Curitiba/SP - postos remotos"],
)
def test_fonte_que_declara_remoto_no_proprio_campo_passa(local):
    """Remoto dito pela própria fonte vale — inclusive junto com a cidade-sede."""
    vaga = _vaga("DevOps Júnior", descricao="Requisitos: pipelines de CI/CD, Docker e automacao de infraestrutura.", localizacao=local)
    assert not _local_declarado_incompativel(vaga)


@pytest.mark.parametrize(
    "local",
    ["Recife, Pernambuco (não remoto)", "São Paulo - sem home office",
     "Fortaleza, CE - not remote", "Salvador, BA - sem vagas remotas"],
)
def test_remoto_negado_no_campo_de_localizacao_nao_vale(local):
    """"não remoto" contém "remoto" e provava o contrário do que a fonte declara."""
    vaga = _vaga("DevOps Júnior", descricao="Requisitos: pipelines de CI/CD, Docker e automacao de infraestrutura.", localizacao=local)
    assert _local_declarado_incompativel(vaga)


@pytest.mark.parametrize(
    "frase",
    [
        "Must be authorized to work in EU",
        "Applicants must have the right to work in the UK",
        "Must be legally authorized to work in Canada",
        "This position requires US work authorization",
        "Only candidates residing in Germany will be considered",
        "Sponsorship is not provided for this role",
        "We cannot sponsor work visas",
        "Candidates must hold EU citizenship",
        "Must be located within the continental United States",
    ],
)
def test_restricao_de_visto_barra_ate_em_host_desconhecido(frase):
    """No domínio de carreiras da própria empresa não há portal conhecido para reprovar."""
    vaga = _vaga("DevOps Engineer Junior", link="https://careers.acme-global.io/jobs/1",
                 descricao=f"Remote role with Docker and Terraform. {frase}")
    assert not _localizacao_compativel(vaga)


@pytest.mark.parametrize(
    "frase",
    ["Fully remote role, authorized to work in Brazil required.",
     "Remote worldwide, hiring across LATAM including Brazil.",
     "Vaga remota para todo o Brasil, com Docker e Terraform."],
)
def test_restricao_de_visto_nao_reprova_vaga_aberta_ao_brasil(frase):
    vaga = _vaga("DevOps Engineer Junior", link="https://careers.acme-global.io/jobs/1",
                 descricao=frase)
    assert _localizacao_compativel(vaga)


def test_sem_cache_continua_respeitando_o_circuito_aberto(monkeypatch):
    """--sem-cache ignora RESULTADOS gravados, não o fato de a cota estar esgotada."""
    estado = cache.carregar()
    for _ in range(cache.FALHAS_PARA_ABRIR):
        cache.registrar_falha(estado, "Google Search")
    cache.salvar(estado)

    chamadas = {"n": 0}

    class ClienteContador:
        def __init__(self):
            self.models = self

        def generate_content(self, **kwargs):
            chamadas["n"] += 1
            raise RuntimeError("não deveria ter sido chamado")

    monkeypatch.setattr("triagem.buscador._buscar_jooble", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._buscar_adzuna", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._busca_metasearch", lambda pedido, limite: ("", []))

    linhas = []
    buscar_vagas(ClienteContador(), "cv", "pedido", limite=3, log=linhas.append, usar_cache=False)
    assert chamadas["n"] == 0
    assert any("CIRCUITO ABERTO" in linha for linha in linhas)


def test_falha_sob_sem_cache_conta_para_o_circuito(monkeypatch):
    """Sem persistir, o circuito nunca aprendia com execuções feitas com --sem-cache."""
    cache.salvar({"entradas": {}, "circuitos": {}})

    class ClienteQuebrado:
        def __init__(self):
            self.models = self

        def generate_content(self, **kwargs):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr("triagem.buscador._buscar_jooble", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._buscar_adzuna", lambda pedido, limite: [])
    monkeypatch.setattr("triagem.buscador._busca_metasearch", lambda pedido, limite: ("", []))

    buscar_vagas(ClienteQuebrado(), "cv", "pedido", limite=3, usar_cache=False)
    assert cache.carregar()["circuitos"]["Google Search"]["falhas"] == 1


def test_enriquecimento_recorta_a_partir_da_ancora_e_nao_do_menu():
    """O índice vinha do texto normalizado e era usado para fatiar o texto visível."""
    truncada = "Atuar com pipelines CI/CD e automacao de infraestrutura em nuvem para o time..."
    menu = "MENU >>> | Home | Vagas | Empresas | Login | ---- *** ---- " * 6
    pagina = (
        "<body>" + menu
        + "Atuar com pipelines CI/CD e automacao de infraestrutura em nuvem para o time "
          "de plataforma. Requisitos: Docker, Kubernetes, Terraform, Azure DevOps. " * 3
        + "</body>"
    )
    enriquecida = _enriquecer_descricao(_vaga("DevOps Júnior", descricao=truncada), pagina)
    assert enriquecida.descricao.startswith("Atuar com pipelines")
    assert "Home | Vagas" not in enriquecida.descricao
    assert "Kubernetes" in enriquecida.descricao


@pytest.mark.parametrize(
    "texto",
    ["MENU >>> | Home | Vagas — acentuação e pontuação!!", "Olá   MUNDO", "a.b+c#d",
     "café ½ litro", "  espaço inicial", "final   ", "ÁÉÍÓÚ àèìòù ç ñ", ""],
)
def test_normalizar_com_indices_bate_com_normalizar(texto):
    """Se as duas divergirem, o recorte do enriquecimento volta a sair deslocado."""
    normalizado, indices = _normalizar_com_indices(texto)
    assert normalizado == _normalizar(texto)
    assert len(indices) == len(normalizado)
    assert all(0 <= i < len(texto) for i in indices)


def test_normalizar_com_indices_resiste_a_entrada_aleatoria():
    """`_normalizar_com_indices` tem um atalho para ASCII que precisa ser equivalente.

    Semente fixa: a suíte não pode ficar intermitente, mas o alfabeto cobre
    combining marks soltos, ligaduras, CJK, zero-width e NBSP — onde o atalho
    erraria se estivesse errado.
    """
    alfabeto = (
        "abcXYZ019 .+#-_/|!?()[]{}<>@$%&*=~^\t\n\r"
        "áéíóúàèìòùâêîôûãõçñÁÉÍÓÚÂÊÔÃÕÇÑ"
        "½¼ﬁœæßøåÅ€£¥•…—–«»“”‘’"
        "̧́̀̃"
        "日本語한국어​ ﻿"
    )
    aleatorio = random.Random(20260725)
    for _ in range(2000):
        texto = "".join(aleatorio.choice(alfabeto)
                        for _ in range(aleatorio.randint(0, 40)))
        normalizado, indices = _normalizar_com_indices(texto)
        assert normalizado == _normalizar(texto), repr(texto)
        assert len(indices) == len(normalizado), repr(texto)
        assert all(0 <= i < len(texto) for i in indices), repr(texto)


@pytest.mark.parametrize(
    "conteudo",
    [
        "# CV\n<!-- PRIVADO -->Telefone: 41 99999-9999\nresto do cv",
        "# CV\n<!-- PRIVADO\nTelefone: 41 99999-9999\nresto do cv",
        "# CV\n<!-- PRIVADO -->Telefone<!-- FIM PRIVADO -->",
        "# CV\n<!-- PRIVADO -->Telefone<!-- \\PRIVADO -->",
        "# CV\nTelefone<!-- /PRIVADO -->",
    ],
)
def test_marcador_privado_malformado_impede_o_envio_do_cv(tmp_path, monkeypatch, conteudo):
    """Falha fechado: marcador com typo vazava o bloco inteiro para a API em silêncio."""
    cv = tmp_path / "cv_base.md"
    cv.write_text(conteudo, encoding="utf-8")
    monkeypatch.setattr("triagem.curriculo.CV_BASE", cv)
    with pytest.raises(ValueError, match="PRIVADO malformado"):
        carregar_cv_base()


def test_cv_com_bloco_bem_formado_continua_carregando(tmp_path, monkeypatch):
    cv = tmp_path / "cv_base.md"
    cv.write_text("# CV\n<!-- PRIVADO -->\nTel: 41 99999-9999\n<!-- /PRIVADO -->\n\n## Exp\nEstágio.\n",
                  encoding="utf-8")
    monkeypatch.setattr("triagem.curriculo.CV_BASE", cv)
    carregado = carregar_cv_base()
    assert "99999" not in carregado
    assert "Estágio." in carregado


def test_historico_corrompido_aponta_o_backup(tmp_path, monkeypatch):
    """O .bak é criado a cada gravação; de nada adianta se o erro não o mencionar."""
    arquivo = tmp_path / "historico.json"
    arquivo.write_text('{"a": {quebrado', encoding="utf-8")
    arquivo.with_suffix(".json.bak").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(historico, "ARQUIVO", arquivo)
    with pytest.raises(ValueError, match=r"\.bak"):
        historico.carregar()


@pytest.mark.parametrize("texto", ["d1=1.55,d2=-1.00,d3=0.20,d4=0.15,d5=0.10", "d2=-0.10", "d1=1.5"])
def test_peso_fora_da_faixa_e_recusado(texto):
    """Só a soma era validada: pesos negativos passavam desde que somassem 1.0."""
    with pytest.raises(ValueError, match="fora da faixa"):
        parse_pesos(texto)
