# Triagem de Vagas — agente personalizado

CLI que recebe vagas de emprego (JSON ou texto livre), aplica hard filters e pontua cada
vaga em 5 dimensões contra o perfil do candidato (Leo — estágio/Jr em C#/.NET ou
DevOps/DevSecOps, Curitiba/Araucária ou remoto), usando a camada gratuita da API Gemini.
Mantém histórico com deduplicação, acompanha o status das candidaturas e gera material
de CV sob medida para cada vaga.

> **Retomando o projeto?** O estado atual, as decisões de arquitetura e as pendências
> estão em [HANDOFF.md](HANDOFF.md).

## Como funciona

```
input (JSON/texto) ──▶ dedup pelo histórico ──▶ Gemini (parse + hard filters + notas D1-D5)
                                                      │  structured output (Pydantic)
                                                      ▼
                                    Python (D2 pela regra fixa, score composto, ranking)
                                                      │
                                ┌─────────────────────┼──────────────────────┐
                                ▼                     ▼                      ▼
                       relatório no terminal   export .md/.csv      historico.json
                                                                          │
                                              python triar.py cv <id> ◀──┘
                                              (bullets de CV + mensagem)
```

- O **modelo** faz o que exige julgamento: extrair os campos da vaga, aplicar os hard
  filters (localização, área, nível) e dar notas 0–10 com justificativa. A resposta é
  validada contra um schema Pydantic (structured outputs) — sempre JSON válido.
- O **código** garante o determinismo: D2 segue a regra fixa (remoto=10, híbrido CWB=8,
  presencial CWB=6), o score composto é `D1*0.30 + D2*0.25 + D3*0.20 + D4*0.15 + D5*0.10`
  em 0–100, e o ranking/relatório seguem o formato do spec.
- As regras de triagem estão em [prompts/system_prompt.md](prompts/system_prompt.md) e as
  do gerador de CV em [prompts/cv_prompt.md](prompts/cv_prompt.md) — edite sem tocar no código.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # gere a chave em https://aistudio.google.com/apikey
```

Como alternativa, instale o comando `triar` diretamente:

```powershell
pip install -e .
triar --help
```

O gerador de CV usa `perfil/cv_base.md` como fonte de verdade — o arquivo contém dados
pessoais e fica fora do versionamento (`.gitignore`); o formato esperado está em
[perfil/cv_base.exemplo.md](perfil/cv_base.exemplo.md). O gerador nunca inventa nada que
não esteja no CV base.

> **Privacidade:** na camada gratuita do Gemini, o conteúdo enviado pode ser usado pelo
> Google para melhorar seus produtos. O comando `cv` envia o CV base e o texto da vaga.
> Remova dados sensíveis que você não queira enviar ao provedor.

## Uso

### Buscar vagas automaticamente

```powershell
# Usa o CV base, pesquisa a web e já executa a triagem/ranking
triar buscar

# Pedido em linguagem natural
triar buscar "preciso de vagas remotas de DevOps ou C# Júnior" --limite 10

# Salva o ranking
triar buscar "estágio DevSecOps em Curitiba ou remoto" --saida vagas.md
```

A busca consulta as APIs da Jooble e da Adzuna quando suas credenciais estão configuradas.
Também tenta o Google Search Grounding do Gemini; se a cota de pesquisa estiver
indisponível, usa automaticamente uma metabusca gratuita. O Gemini normaliza e avalia os
resultados de todas as fontes. Antes da análise, o programa coleta mais resultados que o
limite solicitado, remove duplicatas e vagas de nível sênior, prioriza estágio/júnior e
valida:

- se o anúncio ainda responde e não redirecionou para uma página genérica;
- se portais internacionais mencionam explicitamente Brasil, LATAM ou contratação global;
- se a descrição realmente pertence às áreas DevOps, DevSecOps, Platform, SRE ou C#/.NET.

O limite é um máximo, não uma meta: se só houver uma vaga verificável, o programa retorna
uma em vez de completar a lista com resultados incompatíveis ou vencidos.

### Analisar vagas

```powershell
# JSON (lista de objetos com titulo/empresa/descricao/link/origem)
python triar.py exemplos\vagas_exemplo.json

# Texto livre (várias vagas separadas por uma linha "---")
python triar.py exemplos\vagas_exemplo.txt

# Colando direto do clipboard
Get-Clipboard | python triar.py analisar --stdin

# Exportando o relatório (markdown ou CSV para Excel)
python triar.py exemplos\vagas_exemplo.json --saida relatorio.md
python triar.py exemplos\vagas_exemplo.json --saida relatorio.csv
```

Opções de `analisar`:

| Flag | Efeito |
|---|---|
| `--modelo {lite,flash}` | `lite` (padrão) = Gemini 3.1 Flash-Lite; `flash` = Gemini 3.5 Flash |
| `--paralelo N` | Análises simultâneas (padrão 4) — lote de 20 vagas termina ~4x mais rápido |
| `--saida ARQ` | Exporta para `.md` ou `.csv` além do terminal |
| `--reanalisar` | Força re-análise de vagas que já estão no histórico |

Vagas já analisadas são **puladas automaticamente** (dedup por hash do texto) — rodar o
mesmo arquivo duas vezes não paga API de novo. Falhas de API ganham uma segunda tentativa
automática no fim do lote.

Para usar outro arquivo de histórico, defina `TRIAGEM_HISTORICO` com o caminho desejado.

### Acompanhar candidaturas

```powershell
python triar.py historico                  # lista tudo, ordenado por score
python triar.py historico --status novo    # só as que você ainda não aplicou
python triar.py status a1b2c3d4e5 aplicado # novo | aplicado | entrevista | recusado
```

### Gerar material de candidatura

```powershell
python triar.py cv a1b2c3d4e5 --saida candidatura.md
```

Gera, a partir do seu [perfil/cv_base.md](perfil/cv_base.md) e do texto da vaga: fit em 3
bullets, bullets de CV reescritos com o vocabulário da vaga (ATS-friendly), gaps e como
mitigar, mensagem de candidatura pronta (em inglês quando a vaga é em inglês) e
palavras-chave ATS cobertas/faltantes.

## Testes

```powershell
python -m pytest
pip install -e ".[dev]"
python -m ruff check .
```

Cobrem o pipeline sem chamar a API: parse do input, regra fixa do D2, score composto,
ranking do relatório, dedup/status do histórico e export md/csv.

## Estrutura

| Arquivo | Responsabilidade |
|---|---|
| `triar.py` | Ponto de entrada |
| `triagem/cli.py` | Subcomandos (analisar, historico, status, cv), paralelismo, retry |
| `triagem/buscador.py` | Busca com Jooble, Adzuna, Google Search e fallback gratuito |
| `triagem/entrada.py` | Parse do input (JSON ou texto com `---`) |
| `triagem/analisador.py` | Chamada à API Gemini com saída estruturada |
| `triagem/scoring.py` | Regra fixa do D2 e score composto |
| `triagem/relatorio.py` | Relatório formatado no terminal |
| `triagem/exportar.py` | Export para Markdown e CSV |
| `triagem/historico.py` | Dedup por hash + status de candidatura (`historico.json`) |
| `triagem/curriculo.py` | Gerador de CV sob medida + mensagem de candidatura |
| `triagem/schema.py` | Modelos Pydantic (contrato do JSON) |
| `prompts/system_prompt.md` | Perfil do candidato + regras de triagem |
| `prompts/cv_prompt.md` | Regras do gerador de material de candidatura |
| `perfil/cv_base.md` | CV base real (fonte de verdade do gerador; gitignorado — dados pessoais) |
| `perfil/cv_base.exemplo.md` | Template versionável do CV base |
| `tests/test_pipeline.py` | Testes do pipeline sem API |
