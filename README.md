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

Variáveis de ambiente (todas ficam só no `.env`, que é gitignorado):

| Variável | Obrigatória | Para quê |
|---|---|---|
| `GEMINI_API_KEY` | sim | análise, ranking e comando `cv` |
| `JOOBLE_API_KEY` | não | fonte estruturada de vagas em `triar buscar` |
| `ADZUNA_APP_ID` + `ADZUNA_API_KEY` | não | fonte estruturada de vagas (as duas juntas) |
| `TRIAGEM_HISTORICO` | não | caminho alternativo do `historico.json` |

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
> Google para melhorar seus produtos. O comando `cv` envia o CV base e o texto da vaga, e
> o `buscar` envia o CV base junto com o pedido de pesquisa.
>
> Para dados que não devem sair da máquina (CPF, RG, endereço, telefone), envolva-os em
> `<!-- PRIVADO -->` … `<!-- /PRIVADO -->` no `perfil/cv_base.md`. Esses blocos são
> removidos antes de qualquer chamada de API — o aviso vira salvaguarda técnica. Sem
> marcadores, nada muda.

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
indisponível ou vier vazia, usa automaticamente uma metabusca gratuita (DDGS).

**Fonte estruturada não passa pelo modelo.** Jooble e Adzuna já devolvem título, empresa,
localização, link e data em JSON — esses campos são convertidos direto em código. O Gemini
só normaliza o que chega como texto livre (Google Search e metabusca). Isso existe porque a
normalização por LLM descartava silenciosamente resultados inteiros: em uma execução real,
os 29 anúncios da Jooble sumiram e sobraram só os 7 da Adzuna.

Cada execução imprime quantos anúncios vieram de cada fonte e quantos o pré-filtro cortou —
uma fonte fora do ar (cota do Google Search estourada, por exemplo) fica visível em vez de
sumir em silêncio.

**Cache e circuit breaker.** O resultado bruto de cada fonte é guardado em
`.cache_busca.json` (gitignorado) com TTL de 6 h para Jooble/Adzuna, 1 h para a metabusca e
24 h para o Google Search. Se a fonte não responder, o cache vencido é servido com a idade
declarada em tela. Quando o Google Search falha 3 vezes seguidas, o circuito abre por 24 h e
as execuções seguintes pulam a fonte em vez de gastar latência para receber o mesmo 429.

O estado do circuito é gravado em disco, então ele conta falhas **entre** execuções — que é
o que importa num programa que termina a cada uso. O cache se poda sozinho: entradas com
mais de 30 dias saem do arquivo no carregamento.

```powershell
triar buscar --sem-cache        # ignora o cache e consulta as fontes do zero
triar buscar --testar-fontes    # health check; se o Google Search responder, fecha o circuito
triar limpar-cache              # remove entradas com mais de 30 dias
triar limpar-cache --tudo       # apaga tudo e reseta os circuitos
```

**Uma requisição, três respostas.** A validação de link já visita a página do anúncio, então
a mesma resposta é usada para: confirmar que a vaga está no ar, resolver o redirect do
agregador (chave de dedup entre fontes) e recuperar a descrição completa que Jooble e Adzuna
truncam. O enriquecimento é conservador: só substitui a descrição quando a página é mais
rica **e** ainda contém o início do texto original — senão estaríamos colando o menu do
portal no lugar dos requisitos.

Antes da análise, o programa coleta mais resultados que o limite solicitado, deduplica e
descarta deterministicamente:

- **área**: o termo-alvo (DevOps, DevSecOps, Cloud, Platform, SRE, C#/.NET) precisa estar no
  **título** — exigir só na descrição deixava passar "Data Engineer" e "Talent Sourcer";
- **senioridade**: título com Pleno/Sênior/Staff/Lead/Principal/Arquiteto/Gerente, ou
  exigência de 3+ anos/`3+ years` sem abertura para Jr/estágio;
- **validade**: anúncio com mais de 60 dias, banco de talentos e cadastro de currículo;
- **localização**: a praça declarada pela fonte vence o texto do anúncio — vaga cuja API diz
  "Recife, Pernambuco" sem nenhuma menção a remoto é cortada mesmo que o modelo a chame de
  remota;
- **elegibilidade**: portais internacionais e frases como "US only" ou "we do not sponsor"
  exigem menção explícita a Brasil, LATAM ou contratação global;
- **link**: anúncio que não responde ou redireciona para uma página genérica.

No caminho de texto livre, o nome da empresa devolvido pelo modelo é conferido contra o
material de origem: se não aparecer lá, vira `Desconhecida` com confiança `baixa`. A mesma
URL já saiu como "Casado.dev", "Sylision" e "Desconhecida" em execuções diferentes — a
checagem é determinística porque o modelo também erra ao avaliar a própria confiança.

Parâmetros de rastreio (`utm_*`, `gclid`, …) são removidos de todos os links antes de
qualquer saída — o `redirect_url` da Adzuna carrega o `ADZUNA_APP_ID` no `utm_source` e ele
acabava gravado no relatório, no CSV e no histórico.

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
| `--pesos LISTA` | Pesos do score, ex.: `d1=0.15,d2=0.30,d3=0.25,d4=0.15,d5=0.15` (soma tem que dar 1.0) |

`--pesos` existe porque D1 (crescimento) é a dimensão mais subjetiva e pesa 30% por padrão.
Se quiser que o score dependa menos da impressão do modelo, reduza D1 e distribua em D3/D5.

Vagas já analisadas são **puladas automaticamente** — rodar o mesmo arquivo duas vezes não
paga API de novo. Em `analisar` a chave é o hash do texto; em `buscar` é a URL canônica do
anúncio, porque o texto vem do modelo e muda de execução para execução (com hash do texto,
a mesma vaga era reanalisada e cobrada todo dia).

Erros 429/5xx da API ganham backoff exponencial (3 tentativas) e, no fim do lote, ainda há
uma segunda passada sequencial para as vagas que falharam.

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
ranking do relatório, dedup/status do histórico, export md/csv, os filtros determinísticos
da busca (área pelo título, senioridade, validade, localização declarada, elegibilidade
internacional), a limpeza de parâmetros de rastreio nos links, a redação de credenciais em
mensagens de erro e o backoff de erros transitórios da API.

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
| `triagem/cache.py` | Cache com TTL por fonte + circuit breaker do Google Search |
| `triagem/schema.py` | Modelos Pydantic (contrato do JSON) |
| `prompts/system_prompt.md` | Perfil do candidato + regras de triagem |
| `prompts/cv_prompt.md` | Regras do gerador de material de candidatura |
| `perfil/cv_base.md` | CV base real (fonte de verdade do gerador; gitignorado — dados pessoais) |
| `perfil/cv_base.exemplo.md` | Template versionável do CV base |
| `tests/test_pipeline.py` | Testes do pipeline sem API |
