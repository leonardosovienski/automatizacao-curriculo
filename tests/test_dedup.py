"""Cascata de deduplicação — casos reais da validação ao vivo de 2026-07-27.

Cada caso testa os dois lados: o par que precisa fundir e o par vizinho que não
pode fundir. A assimetria que guia os limiares é que falso merge apaga uma vaga
boa em silêncio, enquanto falso split só custa uma linha repetida.
"""

import json

from triagem import historico as historico_mod
from triagem.dedup import (
    LIMIAR_JACCARD,
    Registro,
    agrupar,
    chave_estrutural,
    corroborado,
    empresa_canonica,
    jaccard,
    nucleo_do_cargo,
    pode_fundir_semanticamente,
    resolver_id,
)

# ---------------------------------------------------------------- normalização

def test_empresa_canonica_ignora_sufixo_societario():
    assert empresa_canonica("NSTech Ltda") == empresa_canonica("nstech")
    # "de" não é expurgado de propósito: "Banco do Brasil" viraria "banco".
    assert empresa_canonica("SKA AUTOMACAO DE ENGENHARIAS LTDA") == "ska automacao de engenharias"


def test_empresa_canonica_nao_funde_empresas_diferentes():
    assert empresa_canonica("RedFox Digital Solutions") != empresa_canonica("BairesDev")


def test_nucleo_do_cargo_expurga_senioridade_e_marketing():
    # O caso BairesDev: mesma vaga, títulos diferentes em portais diferentes.
    adzuna = nucleo_do_cargo("Work From Home Junior DevOps / Rd")
    linkedin = nucleo_do_cargo("Junior DevOps Engineer")
    assert "devops" in adzuna and "devops" in linkedin
    assert jaccard(adzuna, linkedin) >= LIMIAR_JACCARD


def test_nucleo_do_cargo_separa_funcoes_diferentes():
    assert jaccard(nucleo_do_cargo("DevOps Júnior"), nucleo_do_cargo("Data Engineer Júnior")) < LIMIAR_JACCARD
    # O par mais perigoso: mesma família, funções diferentes.
    assert jaccard(nucleo_do_cargo("Data Engineer"), nucleo_do_cargo("Data Analyst")) < LIMIAR_JACCARD


def test_chave_estrutural_exige_empresa_e_nucleo():
    assert chave_estrutural("", "DevOps Júnior") is None
    assert chave_estrutural("RedFox", "Junior") is None  # só ruído, núcleo vazio
    assert chave_estrutural("RedFox", "DevOps Júnior") is not None


# ---------------------------------------------------------------- camada A

def test_camada_a_funde_url_identica():
    a = Registro("1", "https://x.com.br/vagas/9", "Alfa", "DevOps Júnior")
    b = Registro("2", "https://x.com.br/vagas/9", "Beta", "Outra Coisa")
    assert len(agrupar([a, b])) == 1


# ---------------------------------------------------------------- camada B

def test_camada_b_funde_mesma_vaga_em_portais_diferentes():
    # RedFox via Adzuna (JSON-LD, alta) e via um portal hipotético com outro título.
    a = Registro("1", "https://adzuna.com.br/details/1", "RedFox Digital Solutions",
                 "DevOps Júnior", confianca="alta", localidade="Curitiba")
    b = Registro("2", "https://gupy.io/v/2", "RedFox Digital Solutions Ltda",
                 "Júnior DevOps", confianca="media", localidade="curitiba")
    assert len(agrupar([a, b])) == 1


def test_camada_b_nao_funde_vagas_distintas_da_mesma_empresa():
    # O falso merge que precisamos evitar: duas vagas reais e diferentes na RedFox.
    a = Registro("1", "https://adzuna.com.br/details/1", "RedFox Digital Solutions",
                 "DevOps Júnior", confianca="alta")
    b = Registro("2", "https://adzuna.com.br/details/2", "RedFox Digital Solutions",
                 "Analista de Dados Júnior", confianca="alta")
    assert len(agrupar([a, b])) == 2


def test_corroboracao_aceita_datas_proximas_e_recusa_distantes():
    # Medido: datePosted (JSON-LD) e publicada_em (API) divergem 2 dias na mesma página.
    assert corroborado(2, 4, "", "") is True
    assert corroborado(2, 40, "", "") is False
    assert corroborado(None, None, "Curitiba", "curitiba") is True


# ---------------------------------------------------------------- camada C

def test_camada_c_funde_o_caso_real_da_people_partners():
    # O par que motivou a cascata: LinkedIn (sem JSON-LD, confiança media) e Adzuna.
    # Sob a regra "só funde com alta", este par continuaria duplicado.
    linkedin = Registro(
        "a1712e844f",
        "https://br.linkedin.com/jobs/view/devsecops-junior-at-people-partners-4416146595",
        "People Partners Consult", "DevSecOps Júnior", confianca="media",
        localidade="Híbrido",
    )
    adzuna = Registro(
        "0d86cbc4bc", "https://www.adzuna.com.br/details/5754860617",
        "People Partners Consult", "DevSecOps Júnior", confianca="media",
        localidade="híbrido",
    )
    assert len(agrupar([linkedin, adzuna])) == 1


def test_camada_c_nao_funde_quando_a_empresa_foi_inventada():
    a = Registro("1", "https://a.com.br/vagas/1", "Desconhecida", "DevOps Júnior", confianca="baixa")
    b = Registro("2", "https://b.com.br/vagas/2", "Desconhecida", "DevOps Júnior", confianca="baixa")
    assert len(agrupar([a, b])) == 2


def test_portao_da_camada_c_recusa_empresa_desconhecida():
    assert pode_fundir_semanticamente("Desconhecida", "baixa", "Desconhecida", "baixa") is False
    assert pode_fundir_semanticamente("", "alta", "Alfa", "alta") is False


def test_portao_da_camada_c_aceita_corroboracao_cruzada():
    assert pode_fundir_semanticamente("Alfa Tecnologia", "media", "Alfa", "media") is True


def test_portao_da_camada_c_aceita_um_lado_estruturado():
    assert pode_fundir_semanticamente("Alfa", "alta", "Beta", "media") is True


# ---------------------------------------------------------------- agrupamento

def test_agrupar_preserva_todas_as_origens_sem_descartar():
    a = Registro(
        "1", "https://br.linkedin.com/jobs/view/x-4416146595",
        "Alfa", "DevOps Júnior", localidade="Curitiba",
    )
    b = Registro(
        "2", "https://www.adzuna.com.br/details/99",
        "Alfa", "DevOps Júnior", localidade="curitiba",
    )
    grupos = agrupar([a, b])
    assert len(grupos) == 1
    assert [r.id for r in grupos[0]] == ["1", "2"]
    assert [r.url for r in grupos[0]] == [a.url, b.url]


def test_corroboracao_esta_ligada_e_autoriza_fusao_sem_lado_estruturado():
    # Regressão: `corroborado` existia, era testada e nunca era chamada por
    # `_mesma_vaga`. Chave estrutural idêntica, nenhum lado com confiança alta —
    # sem corroboração este par não funde pela Camada B.
    a = Registro("1", "https://a.com.br/vagas/1", "Alfa", "DevOps Júnior",
                 confianca="media", idade_dias=4, localidade="Curitiba")
    b = Registro("2", "https://b.com.br/vagas/2", "Alfa Ltda", "DevOps Jr",
                 confianca="media", idade_dias=6, localidade="Curitiba")
    assert len(agrupar([a, b])) == 1


def test_corroboracao_com_datas_distantes_e_cidades_diferentes_nao_decide_sozinha():
    # Mesma chave estrutural, mas nada corrobora: cai para a Camada C, que ainda
    # funde por corroboração cruzada de empresa. O que se testa aqui é que a
    # corroboração de data/local não é o que está decidindo.
    from triagem.dedup import corroborado

    assert corroborado(2, 90, "Curitiba", "Sao Paulo") is False


def test_corroboracao_nao_e_porta_lateral_para_falso_merge():
    # Mesma empresa, mesma data, mesma cidade — e funções genuinamente distintas.
    # Corroboração perfeita não pode fundir núcleos de cargo diferentes.
    a = Registro("1", "https://a.com.br/vagas/1", "Alfa", "Data Engineer",
                 confianca="alta", idade_dias=3, localidade="Curitiba")
    b = Registro("2", "https://a.com.br/vagas/2", "Alfa", "Data Analyst",
                 confianca="alta", idade_dias=3, localidade="Curitiba")
    assert len(agrupar([a, b])) == 2


def test_resolver_id_encontra_a_mesma_vaga_gravada_por_outra_fonte():
    # Reprodução fiel do histórico real: a entrada do LinkedIn já gravada, e a
    # mesma vaga chegando pela Adzuna com URL, id e material diferentes.
    historico = {
        "a1712e844f": {
            "texto": json.dumps(
                {
                    "titulo": "DevSecOps Júnior",
                    "empresa": "People Partners Consult",
                    "link": "https://br.linkedin.com/jobs/view/devsecops-junior-4416146595",
                    "link_final": "https://br.linkedin.com/jobs/view/devsecops-junior-4416146595",
                    "confianca_empresa": "media",
                    "localizacao": "Híbrido",
                },
                ensure_ascii=False,
            )
        }
    }
    nova = Registro(
        "0d86cbc4bc", "https://www.adzuna.com.br/details/5754860617",
        "People Partners Consult", "DevSecOps Júnior", confianca="media",
        localidade="Híbrido",
    )
    assert resolver_id(historico, nova) == "a1712e844f"


def test_resolver_id_devolve_none_para_vaga_realmente_nova():
    historico = {
        "a1712e844f": {
            "texto": json.dumps(
                {"titulo": "DevSecOps Júnior", "empresa": "People Partners Consult",
                 "link": "https://br.linkedin.com/jobs/view/x-4416146595",
                 "confianca_empresa": "media"},
                ensure_ascii=False,
            )
        }
    }
    nova = Registro("novo", "https://www.adzuna.com.br/details/1", "BairesDev",
                    "Junior DevOps Engineer", confianca="alta")
    assert resolver_id(historico, nova) is None


def test_resolver_id_ignora_entrada_com_texto_corrompido():
    historico = {"quebrada": {"texto": "{isto não é json"}}
    nova = Registro("novo", "https://a.com.br/vagas/1", "Alfa", "DevOps Júnior")
    assert resolver_id(historico, nova) is None


def test_cli_importa_dedup():
    # Guarda contra o NameError latente: a cascata só roda quando `registros` existe,
    # então um import faltando passava despercebido por toda a suíte.
    from triagem import cli

    assert hasattr(cli, "dedup")
    assert hasattr(cli.historico, "registrar_alias")


def test_registrar_alias_preserva_as_duas_origens():
    hist = {"abc": {"texto": "{}", "status": "novo"}}
    historico_mod.registrar_alias(hist, "abc", "https://outra.com.br/vagas/1")
    historico_mod.registrar_alias(hist, "abc", "https://outra.com.br/vagas/1")
    historico_mod.registrar_alias(hist, "abc", "https://terceira.com.br/vagas/2")
    assert hist["abc"]["aliases"] == [
        "https://outra.com.br/vagas/1",
        "https://terceira.com.br/vagas/2",
    ]


def test_agrupar_mantem_vagas_distintas_separadas():
    registros = [
        Registro("1", "https://a.com.br/vagas/1", "Alfa", "DevOps Júnior", confianca="alta"),
        Registro("2", "https://b.com.br/vagas/2", "Beta", "DevOps Júnior", confianca="alta"),
        Registro("3", "https://c.com.br/vagas/3", "Alfa", "Desenvolvedor C# Júnior", confianca="alta"),
    ]
    assert len(agrupar(registros)) == 3


def test_ids_ats_distintos_nunca_sao_fundidos():
    a = Registro(
        "1", "https://empresa.test/jobs/1", "Alfa", "DevOps Júnior",
        localidade="Curitiba", ats_provedor="greenhouse", ats_token="alfa", ats_job_id="1",
    )
    b = Registro(
        "2", "https://empresa.test/jobs/2", "Alfa", "DevOps Júnior",
        localidade="Curitiba", ats_provedor="greenhouse", ats_token="alfa", ats_job_id="2",
    )
    assert len(agrupar([a, b])) == 2


def test_empresa_e_titulo_sem_corroboracao_nao_apagam_requisicao_distinta():
    a = Registro("1", "https://a.test/jobs/1", "Alfa", "DevOps Júnior", confianca="alta")
    b = Registro("2", "https://a.test/jobs/2", "Alfa", "DevOps Júnior", confianca="alta")
    assert len(agrupar([a, b])) == 2


# ------------------------------------------- pontuação em sufixo societário

def test_empresa_canonica_ignora_ponto_no_sufixo_societario():
    """Regressão do duplicado real da SKA (busca de 2026-08-08).

    `_normalizar` preserva `.` de propósito, para `.net`/`node.js` sobreviverem
    em `nucleo_do_cargo`. Isso fazia `"ltda."` não casar com SUFIXOS_SOCIETARIOS,
    que guarda `"ltda"` — e a mesma vaga entrou duas vezes no histórico, com duas
    análises pagas. Atingia quase toda forma abreviada, não só uma.
    """
    esperado = "acme"
    for grafia in (
        "ACME LTDA", "ACME LTDA.", "ACME Ltda.",
        "ACME S.A.", "ACME S/A", "ACME S. A.",
        "ACME ME.", "ACME Inc.", "ACME Corp.", "ACME Cia.",
    ):
        assert empresa_canonica(grafia) == esperado, grafia


def test_empresa_canonica_funde_o_par_exato_do_historico():
    com_ponto = "SKA AUTOMACAO DE ENGENHARIAS LTDA."
    sem_ponto = "SKA AUTOMACAO DE ENGENHARIAS LTDA"
    assert empresa_canonica(com_ponto) == empresa_canonica(sem_ponto)


def test_remover_ponto_nao_funde_empresas_distintas():
    assert empresa_canonica("ACME Inc.") != empresa_canonica("ACME Digital")
    assert empresa_canonica("Sabre") != empresa_canonica("Saber")
    # nome com ponto interno continua casando consigo mesmo, e só consigo
    assert empresa_canonica("Booking.com") == empresa_canonica("booking.com")
    assert empresa_canonica("Booking.com") != empresa_canonica("Booking Holdings")


def test_nucleo_do_cargo_preserva_ponto_da_stack():
    """O ponto só sai do nome da empresa — no cargo ele carrega significado."""
    assert ".net" in nucleo_do_cargo("Desenvolvedor .NET")
    assert "node.js" in nucleo_do_cargo("Node.js Engineer")
    assert "c#" in nucleo_do_cargo("C# Developer")
    assert "c++" in nucleo_do_cargo("C++ Engineer")


def test_do_brasil_nao_e_expurgado():
    """Deliberado: `Banco do Brasil` não pode colapsar para `banco`.

    O custo é `Volvo do Brasil` não casar com `Volvo` — um falso split aceito de
    propósito, porque nenhuma regra estrutural separa "nome + país" de "nome
    próprio que contém o país", e falso merge é o erro mais caro aqui.
    """
    assert empresa_canonica("Banco do Brasil") != empresa_canonica("Banco")
