"""Testes dos comandos de CLI que operam só sobre arquivos locais.

`historico`, `status` e `limpar-cache` não chamam o Gemini nem a rede: são
leitura/escrita de JSON. Ficavam sem cobertura nenhuma (nenhum `_cmd_*` era
exercitado), justamente os comandos de uso diário. Aqui eles são acionados
pelo `main()` de verdade — com argv, lock e resolução de caminho por ambiente —
para que o teste cubra também o dispatch e a serialização, não só o handler.
"""

import argparse
import json

import pytest
from filelock import FileLock

from triagem import cli, historico
from triagem.schema import AnaliseVaga, Dimensao, Notas


def _entrada(*, empresa="ACME", titulo="Backend Engineer", status="novo", score=80.0):
    return {
        "analisado_em": "2026-08-07T10:00:00",
        "status": status,
        "score_final": score,
        "pipeline_version": 2,
        "texto": f"Vaga: {titulo}",
        "analise": {
            "titulo_normalizado": titulo,
            "empresa": empresa,
            "regime": "remoto",
            "localizacao": "Brasil",
            "nivel_real": "pleno_disfarcado",
            "stack_exigida": ["Python"],
            "stack_desejavel": [],
            "idioma_trabalho": "pt",
            "link": "https://example.com/vaga",
            "origem": "teste",
            "publicada_em": "",
            "descartada": score is None,
            "motivo_descarte": None,
            "notas": None,
            "alertas": [],
        },
    }


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Isola histórico e cache em tmp_path via as mesmas env vars do usuário."""
    hist = tmp_path / "historico.json"
    monkeypatch.setenv("TRIAGEM_HISTORICO", str(hist))
    monkeypatch.setenv("TRIAGEM_CACHE", str(tmp_path / ".cache_busca.json"))
    return tmp_path


def _rodar(monkeypatch, *argv) -> int:
    monkeypatch.setattr("sys.argv", ["triar", *argv])
    return cli.main()


def _escrever(ambiente, dados):
    (ambiente / "historico.json").write_text(json.dumps(dados), encoding="utf-8")


def _ler(ambiente):
    return json.loads((ambiente / "historico.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------- lock

def test_cli_e_api_disputam_o_mesmo_arquivo_de_lock(tmp_path, monkeypatch):
    """Regressão: `with_suffix` trocava a extensão e os locks divergiam.

    A CLI travava `historico.lock` e a API `historico.json.lock`, então os dois
    processos escreviam ao mesmo tempo e a última gravação apagava a anterior.
    """
    monkeypatch.setattr(historico, "ARQUIVO", tmp_path / "historico.json")
    caminho = historico.caminho_lock()

    assert caminho.name == "historico.json.lock"
    # o lock precisa ficar ao lado do histórico, não em outro diretório
    assert caminho.parent == historico.ARQUIVO.parent

    # exclusão mútua real entre as duas instâncias
    with FileLock(str(caminho)):
        with pytest.raises(TimeoutError):
            FileLock(str(historico.caminho_lock())).acquire(timeout=0.2)


def test_comando_espera_o_lock_e_falha_com_mensagem(ambiente, monkeypatch, capsys):
    with FileLock(str(ambiente / "historico.json.lock")):
        codigo = _rodar(monkeypatch, "historico")
    assert codigo == 1
    assert "Outra execução do triar" in capsys.readouterr().out


# ------------------------------------------------------------ historico

def test_historico_vazio(ambiente, monkeypatch, capsys):
    assert _rodar(monkeypatch, "historico") == 0
    assert "Histórico vazio." in capsys.readouterr().out


def test_historico_ordena_por_score_com_descartada_por_ultimo(
    ambiente, monkeypatch, capsys
):
    _escrever(ambiente, {
        "baixo00001": _entrada(empresa="Baixo", score=40.0),
        "alto000001": _entrada(empresa="Alto", score=95.0),
        "descart001": _entrada(empresa="Descartada", score=None),
    })
    assert _rodar(monkeypatch, "historico") == 0

    saida = capsys.readouterr().out
    assert saida.index("Alto") < saida.index("Baixo") < saida.index("Descartada")
    assert "Total: 3" in saida


def test_historico_filtra_por_status(ambiente, monkeypatch, capsys):
    _escrever(ambiente, {
        "aaaaaaaaa1": _entrada(empresa="Nova", status="novo"),
        "bbbbbbbbb2": _entrada(empresa="Enviada", status="aplicado"),
    })
    assert _rodar(monkeypatch, "historico", "--status", "aplicado") == 0

    saida = capsys.readouterr().out
    assert "Enviada" in saida
    assert "Nova" not in saida


def test_historico_sem_resultado_para_o_status(ambiente, monkeypatch, capsys):
    _escrever(ambiente, {"aaaaaaaaa1": _entrada(status="novo")})
    assert _rodar(monkeypatch, "historico", "--status", "entrevista") == 0
    assert "Nenhuma vaga com status 'entrevista'." in capsys.readouterr().out


def test_historico_corrompido_falha_com_dica_em_vez_de_traceback(
    ambiente, monkeypatch, capsys
):
    (ambiente / "historico.json").write_text("{ isto nao e json", encoding="utf-8")
    assert _rodar(monkeypatch, "historico") == 1

    saida = capsys.readouterr().out
    assert "Erro:" in saida
    assert "backup" in saida.lower()


# --------------------------------------------------------------- status

def test_status_atualiza_e_persiste(ambiente, monkeypatch, capsys):
    _escrever(ambiente, {"abcdef1234": _entrada(status="novo")})
    assert _rodar(monkeypatch, "status", "abcdef1234", "aplicado") == 0

    assert _ler(ambiente)["abcdef1234"]["status"] == "aplicado"
    assert "-> status: aplicado" in capsys.readouterr().out


def test_status_aceita_prefixo_de_id(ambiente, monkeypatch):
    _escrever(ambiente, {"abcdef1234": _entrada(status="novo")})
    assert _rodar(monkeypatch, "status", "abcdef", "entrevista") == 0
    assert _ler(ambiente)["abcdef1234"]["status"] == "entrevista"


def test_status_id_inexistente(ambiente, monkeypatch, capsys):
    _escrever(ambiente, {"abcdef1234": _entrada()})
    assert _rodar(monkeypatch, "status", "zzz", "aplicado") == 1
    assert "Nenhuma vaga no histórico" in capsys.readouterr().out


def test_status_prefixo_ambiguo_nao_altera_nada(ambiente, monkeypatch, capsys):
    _escrever(ambiente, {
        "abc1111111": _entrada(status="novo"),
        "abc2222222": _entrada(status="novo"),
    })
    assert _rodar(monkeypatch, "status", "abc", "aplicado") == 1

    assert "ambíguo" in capsys.readouterr().out
    assert all(e["status"] == "novo" for e in _ler(ambiente).values())


def test_status_descartada_nao_e_alcancavel_pelo_usuario(ambiente, monkeypatch):
    """`descartada` é decisão do hard filter, não do acompanhamento manual."""
    _escrever(ambiente, {"abcdef1234": _entrada(status="novo")})
    with pytest.raises(SystemExit):
        _rodar(monkeypatch, "status", "abcdef1234", "descartada")


# --------------------------------------------------------- limpar-cache

def _cache_com(ambiente, gravado_em):
    (ambiente / ".cache_busca.json").write_text(
        json.dumps({
            "entradas": {"jooble:python": {"gravado_em": gravado_em, "dados": []}},
            "circuitos": {"jooble": {"falhas": 3}},
        }),
        encoding="utf-8",
    )


def _cache_lido(ambiente):
    return json.loads((ambiente / ".cache_busca.json").read_text(encoding="utf-8"))


def test_limpar_cache_remove_entrada_vencida(ambiente, monkeypatch, capsys):
    """A contagem exibida precisa bater com o que saiu do arquivo.

    O teste anterior só checava o efeito (entrada sumiu) e por isso passava com
    a mensagem errada: `cache.carregar()` já podava, então o `podar()` seguinte
    não achava mais nada e o comando anunciava "0 entrada(s) removida(s)"
    enquanto removia uma.
    """
    _cache_com(ambiente, "2020-01-01T00:00:00")
    assert _rodar(monkeypatch, "limpar-cache") == 0

    assert _cache_lido(ambiente)["entradas"] == {}
    assert "1 entrada(s) removida(s)" in capsys.readouterr().out


def test_limpar_cache_preserva_entrada_recente(ambiente, monkeypatch):
    from triagem import cache

    _cache_com(ambiente, cache._agora().isoformat(timespec="seconds"))
    assert _rodar(monkeypatch, "limpar-cache") == 0
    assert "jooble:python" in _cache_lido(ambiente)["entradas"]


def test_limpar_cache_tudo_zera_entradas_e_circuitos(ambiente, monkeypatch, capsys):
    from triagem import cache

    _cache_com(ambiente, cache._agora().isoformat(timespec="seconds"))
    assert _rodar(monkeypatch, "limpar-cache", "--tudo") == 0

    estado = _cache_lido(ambiente)
    assert estado["entradas"] == {}
    assert estado["circuitos"] == {}
    assert "1 entrada(s) removida(s)" in capsys.readouterr().out


# ----------------------------------------------------------- dispatch

def test_sem_argumentos_mostra_ajuda_e_falha(ambiente, monkeypatch, capsys):
    assert _rodar(monkeypatch) == 1
    assert "usage:" in capsys.readouterr().out


def test_subcomando_omitido_assume_analisar(ambiente, monkeypatch, capsys):
    """Retrocompatibilidade: `triar arquivo.txt` == `triar analisar arquivo.txt`.

    O erro citar o arquivo (e não "comando inválido") é o que prova o dispatch:
    o argumento solto virou o parâmetro posicional de `analisar`.
    """
    assert _rodar(monkeypatch, "vagas.txt") == 1
    assert "vagas.txt" in capsys.readouterr().out


def test_analisar_exige_chave_de_api(ambiente, monkeypatch, capsys, tmp_path):
    lote = tmp_path / "vagas.txt"
    lote.write_text("Vaga: Backend\nEmpresa: ACME\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert _rodar(monkeypatch, "analisar", str(lote)) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


# =====================================================================
# Orquestração de `analisar`
#
# É o trecho que gasta dinheiro: chamadas ao Gemini já pagas podem ser
# perdidas se a persistência, o retry ou a interrupção forem tratados
# errado. Todo o bloco rodava sem teste nenhum. Aqui o cliente é falso —
# nenhuma chamada real —, mas o caminho exercitado é o de produção.
# =====================================================================

def _analise(empresa="TechCorp", titulo="Estágio DevOps"):
    dim = lambda n: Dimensao(nota=n, justificativa="x")  # noqa: E731
    return AnaliseVaga(
        titulo_normalizado=titulo,
        empresa=empresa,
        regime="remoto",
        localizacao="Remoto (Brasil)",
        nivel_real="estagio",
        stack_exigida=["Python"],
        stack_desejavel=[],
        idioma_trabalho="pt",
        link="https://example.com/1",
        origem="teste",
        descartada=False,
        motivo_descarte=None,
        notas=Notas(
            d1_crescimento=dim(9),
            d2_regime_localizacao=dim(10),
            d3_stack_fit=dim(9),
            d4_ingles=dim(8),
            d5_nivel_real=dim(10),
        ),
        alertas=[],
    )


def _args_analisar(**kw):
    base = dict(
        pesos=None, stdin=False, arquivo=None, modelo=cli.MODELO_PADRAO,
        paralelo=2, saida=None, reanalisar=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def gemini_falso(monkeypatch):
    """Substitui o cliente e a análise; registra as chamadas feitas."""
    monkeypatch.setenv("GEMINI_API_KEY", "chave-de-teste-1234567890")
    monkeypatch.setattr(cli, "criar_cliente", lambda: object())

    chamadas = []

    def instalar(comportamento):
        def falso(client, texto, modelo, tentativas=None):
            chamadas.append(texto)
            return comportamento(texto, chamadas.count(texto))
        monkeypatch.setattr(cli, "analisar_vaga", falso)

    return chamadas, instalar


BOA, RUIM = "Vaga: boa\nEmpresa: Alfa", "Vaga: ruim\nEmpresa: Beta"


def test_sucesso_e_persistido_mesmo_quando_outra_vaga_falha(
    ambiente, monkeypatch, gemini_falso, capsys
):
    """Falha de uma vaga não pode descartar a análise já paga da outra."""
    chamadas, instalar = gemini_falso

    def comportamento(texto, _n):
        if texto == RUIM:
            raise RuntimeError("cota estourada")
        return _analise(empresa="Alfa")

    instalar(comportamento)
    historico.aplicar_config_do_ambiente()

    codigo = cli._cmd_analisar(_args_analisar(), textos=[BOA, RUIM])

    assert codigo == 2  # 2 = concluiu, mas com falhas pendentes
    persistido = _ler(ambiente)
    assert len(persistido) == 1
    assert next(iter(persistido.values()))["analise"]["empresa"] == "Alfa"

    saida = capsys.readouterr().out
    assert "não analisadas mesmo após retry" in saida
    assert "cota estourada" in saida


def test_checkpoint_sobrevive_a_interrupcao_durante_o_retry(
    ambiente, monkeypatch, gemini_falso
):
    """A garantia central do checkpoint, hoje só descrita em comentário.

    "Persiste o que já terminou antes de esperar a segunda passagem. Se o
    processo for interrompido, chamadas pagas não são perdidas." Um Ctrl+C
    no meio do retry não pode levar junto o que já foi analisado e pago.
    """
    chamadas, instalar = gemini_falso

    def comportamento(texto, n):
        if texto == RUIM and n == 1:
            raise RuntimeError("falha transitória")
        if texto == RUIM:
            raise KeyboardInterrupt  # usuário aborta durante o retry
        return _analise(empresa="Alfa")

    instalar(comportamento)
    historico.aplicar_config_do_ambiente()

    with pytest.raises(KeyboardInterrupt):
        cli._cmd_analisar(_args_analisar(), textos=[BOA, RUIM])

    # o disco já tem a vaga boa, apesar da interrupção
    persistido = _ler(ambiente)
    assert len(persistido) == 1
    assert next(iter(persistido.values()))["analise"]["empresa"] == "Alfa"


def test_retry_sequencial_recupera_falha_transitoria(
    ambiente, monkeypatch, gemini_falso, capsys
):
    chamadas, instalar = gemini_falso

    def comportamento(texto, n):
        if texto == RUIM and n == 1:
            raise RuntimeError("timeout")
        return _analise(empresa="Alfa" if texto == BOA else "Beta")

    instalar(comportamento)
    historico.aplicar_config_do_ambiente()

    codigo = cli._cmd_analisar(_args_analisar(), textos=[BOA, RUIM])

    assert codigo == 0
    assert len(_ler(ambiente)) == 2
    assert "OK na segunda tentativa" in capsys.readouterr().out


def test_falha_ao_gravar_checkpoint_aborta_sem_relatorio(
    ambiente, monkeypatch, gemini_falso, capsys
):
    """Disco cheio no checkpoint não pode passar como execução bem-sucedida."""
    chamadas, instalar = gemini_falso
    instalar(lambda texto, n: _analise())
    historico.aplicar_config_do_ambiente()

    def explodir(_hist):
        raise OSError("disco cheio")

    monkeypatch.setattr(historico, "salvar", explodir)

    codigo = cli._cmd_analisar(_args_analisar(), textos=[BOA])

    assert codigo == 1
    assert "Erro ao salvar checkpoint" in capsys.readouterr().out


def test_vaga_ja_no_historico_nao_gasta_chamada(
    ambiente, monkeypatch, gemini_falso, capsys
):
    """Dedup por histórico é o que evita pagar duas vezes pela mesma vaga."""
    chamadas, instalar = gemini_falso
    instalar(lambda texto, n: _analise())
    historico.aplicar_config_do_ambiente()

    assert cli._cmd_analisar(_args_analisar(), textos=[BOA]) == 0
    assert len(chamadas) == 1

    capsys.readouterr()
    assert cli._cmd_analisar(_args_analisar(), textos=[BOA]) == 0
    assert len(chamadas) == 1  # nenhuma chamada nova
    assert "já no histórico" in capsys.readouterr().out


def test_reanalisar_forca_nova_chamada(ambiente, monkeypatch, gemini_falso):
    chamadas, instalar = gemini_falso
    instalar(lambda texto, n: _analise())
    historico.aplicar_config_do_ambiente()

    cli._cmd_analisar(_args_analisar(), textos=[BOA])
    cli._cmd_analisar(_args_analisar(reanalisar=True), textos=[BOA])

    assert len(chamadas) == 2


def test_duplicata_no_input_e_analisada_uma_vez_so(
    ambiente, monkeypatch, gemini_falso, capsys
):
    chamadas, instalar = gemini_falso
    instalar(lambda texto, n: _analise())
    historico.aplicar_config_do_ambiente()

    assert cli._cmd_analisar(_args_analisar(), textos=[BOA, BOA]) == 0

    assert len(chamadas) == 1
    assert len(_ler(ambiente)) == 1
    assert "duplicada no input" in capsys.readouterr().out


def test_chave_de_api_nao_vaza_na_mensagem_de_erro(
    ambiente, monkeypatch, gemini_falso, capsys
):
    """A mensagem de falha vai para o terminal: não pode carregar a chave."""
    chave = "chave-de-teste-1234567890"
    chamadas, instalar = gemini_falso

    def comportamento(texto, n):
        raise RuntimeError(f"401 Unauthorized para key={chave}")

    instalar(comportamento)
    historico.aplicar_config_do_ambiente()

    cli._cmd_analisar(_args_analisar(), textos=[BOA])

    saida = capsys.readouterr().out
    assert chave not in saida
    assert "<GEMINI_API_KEY>" in saida


def test_nenhum_resultado_ainda_explica_a_causa(
    ambiente, monkeypatch, gemini_falso, capsys
):
    """Falhar tudo é o caso mais comum (chave inválida) e era o menos explicado.

    O `return 1` acontecia antes do bloco de falhas, então o usuário via apenas
    "Nenhuma vaga analisada." — sem uma linha sobre o motivo.
    """
    chamadas, instalar = gemini_falso
    instalar(lambda texto, n: (_ for _ in ()).throw(RuntimeError("cota diária esgotada")))
    historico.aplicar_config_do_ambiente()

    assert cli._cmd_analisar(_args_analisar(), textos=[BOA]) == 1

    saida = capsys.readouterr().out
    assert "Nenhuma vaga analisada." in saida
    assert "cota diária esgotada" in saida


def test_analisar_paralelo_separa_sucessos_de_falhas(monkeypatch):
    """Unidade: nenhuma exceção individual pode derrubar o lote inteiro."""
    def falso(client, texto, modelo, tentativas=None):
        if texto == "b":
            raise RuntimeError("falha isolada")
        return _analise()

    monkeypatch.setattr(cli, "analisar_vaga", falso)

    analises, falhas = cli._analisar_paralelo(
        object(), [("id-a", "a"), ("id-b", "b"), ("id-c", "c")], cli.MODELO_PADRAO, 3
    )

    assert set(analises) == {"id-a", "id-c"}
    assert [vid for vid, _, _ in falhas] == ["id-b"]
