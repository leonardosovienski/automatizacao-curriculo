# Changelog

Todas as mudanças relevantes deste projeto. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não publicado] — revisão independente e primeira busca com dados reais

Revisão feita do zero em 2026-08-08, seguida da primeira execução do pipeline com dados
reais. Cinco defeitos encontrados. **Nenhum deles teria surgido de escrever mais teste** —
vieram de ler código, rodar o sistema e reparar em detalhes fora do lugar.

### Corrigido

- **Deduplicação ignorava duplicatas novas dentro da mesma busca.** Cada vaga era comparada
  apenas ao histórico carregado antes do lote. Duas URLs distintas da mesma vaga podiam,
  portanto, entrar juntas em `pendentes`, consumir duas chamadas e receber duas linhas no
  histórico. A cascata agora compara também os registros já aceitos no lote e preserva a
  segunda URL como alias. O teste reproduz o par da Solvd do run `31240277177` e foi
  validado por mutação.
- **Lock divergente entre CLI e API — perda silenciosa de atualização.** `cli.py` travava
  `historico.lock` e `api/app.py` travava `historico.json.lock`, porque `with_suffix()`
  substitui a extensão em vez de acrescentar. Os dois lados nunca se excluíam: como ambos
  fazem read-modify-write do dicionário inteiro, um PATCH da interface web concorrente com
  `triar analisar` tinha o status sobrescrito, sem erro nenhum. O arquivo nunca corrompia
  (`salvar()` é atômico) — a atualização é que sumia. Agora `historico.caminho_lock()` é
  fonte única, com teste de regressão que falha se os caminhos divergirem.
- **Histórico ilegível virava 500 cru em toda rota de leitura da API.** Agora **503** com a
  mensagem e a dica do `.bak`. No `PATCH`, o erro de arquivo era capturado como 400,
  sugerindo erro do cliente; hoje 400 fica reservado para status inválido.
- **Deduplicação cega a ponto em sufixo societário.** `_normalizar` preserva `.` de
  propósito, para `.net`/`node.js`/`c#`/`c++` sobreviverem em `nucleo_do_cargo` — mas o mesmo
  normalizador servia nome de empresa, e `"ltda."` não casava com `"ltda"`. A SKA entrou
  duas vezes no histórico, com **duas análises pagas**. Atingia quase toda forma abreviada
  (`LTDA.`, `S.A.`, `S/A`, `Inc.`, `Corp.`, `Cia.`); só grafias sem pontuação funcionavam.
  O ponto passa a sair em `empresa_canonica` e só ali. `"s a"` e `"do brasil"` eram entradas
  de duas palavras num filtro token a token — código morto; `"s a"` virou
  `SUFIXOS_COMPOSTOS` e `"do brasil"` ficou de fora de propósito (ativá-lo faria
  `Banco do Brasil` virar `banco`).
- **`triar limpar-cache` anunciava "0 entrada(s) removida(s)" enquanto removia.**
  `cache.carregar()` já podava antes do `podar()` do comando, então quem contava não era
  quem removia. `carregar()` ganhou `podar_automaticamente`.
- **Checagem de host por substring em `_localizacao_compativel`.** `portal in host` fazia
  `catho.com.br` casar dentro de `catho.com.br.attacker.net` — host hostil tratado como
  portal brasileiro, entrando na análise paga. Não era SSRF (`_host_e_seguro` segue barrando
  destino interno e `_obter` revalida cada salto), mas custava chamada. O padrão seguro
  estava reimplementado à mão em seis pontos e duas cópias erraram: agora existe
  `_host_em()`, usado nas seis.
- **`analisar` não explicava a falha quando *todas* as vagas falhavam.** O retorno acontecia
  antes do bloco que lista os motivos, deixando o modo de falha mais comum (chave inválida,
  cota estourada) como o único sem diagnóstico.
- **`useEffect` de `/api/stats` sem guarda de cancelamento** — resposta lenta podia
  sobrescrever o dashboard com números vencidos.
- **`actions/checkout@v4` no job `frontend`** enquanto os demais usavam `@v7`.

### Testes

- **Seis dos 42 testes E2E passavam com a funcionalidade quebrada**: um sem asserção
  nenhuma, dois com tautologia (`toHaveValue(await inputValue())`), um com regex que casava
  `"0 Aplicadas"`, um com `test.skip` sobre fixture determinístico, e — o mais grave —
  `getVisibleScores` usando `/^(\d{1,3})\b/` sobre `"95Platform Engineer…"`, onde não há
  fronteira de palavra entre `5` e `P`: a função devolvia sempre `[]` e as asserções de
  ordenação, guardadas por `if (length >= 2)`, nunca chegaram a executar.
- Novo `tests/test_cli.py`: nenhum `_cmd_*` era exercitado antes. Cobre `status`,
  `historico` e `limpar-cache` pelo `main()` real, e a orquestração do `analisar` com
  cliente falso — retry, paralelismo, dedup por histórico, redação da chave em mensagens de
  erro e o *checkpoint* que impede um Ctrl+C no retry de descartar análises já pagas.
- **Toda correção validada por mutação**: quebrar o código de propósito e confirmar que o
  teste fica vermelho. Em dois casos, um teste recém-escrito passava com o bug.
- 306 → **348 testes**; cobertura 76.33% → **80.57%**; `api/app.py` a 100%;
  `triagem/cli.py` de 44% para 56%.

### Adicionado

- **Workflow "Pipeline real (manual)"** (`.github/workflows/verificacao-gemini.yml`). A
  suíte roda com dublês, o que valida a orquestração mas não o **contrato** com o
  `google-genai`, a Jooble ou a Adzuna. O workflow fecha essa lacuna no único lugar onde as
  credenciais já vivem sem sair de um cofre: os Repository secrets. Escopos `analisar`,
  `fontes`, `buscar` e `tudo`; sempre manual, porque cada execução gasta chamadas pagas.
  O job `buscar` guarda o `historico.json` em cache entre execuções, para a dedup funcionar
  e não re-pagar vagas já vistas.
- Cada credencial aceita **duas grafias** de secret, e o passo de diagnóstico informa qual
  chegou e por qual nome — o que separa "credencial ausente" de "API mudou". Foi ele que
  identificou um secret criado em *Environment* em vez de *Repository*.

## [Anterior] — validação com rede real

Primeira execução do pipeline contra as quatro fontes de verdade, em 2026-07-27. Toda a
validação anterior tinha rodado com dublês, por falta de credenciais. Das 4 vagas aprovadas
na primeira busca real, **2 prestavam** — e a #1 do ranking era presencial em Da Nang, no
Vietnã, anunciada como "100% remota" com D2 10/10.

Causa raiz: o caminho de texto livre entregava descrições de ~100 caracteres que eram
paráfrases do próprio modelo, não o texto do anúncio. Sem material de onde tirar a
localização, ele preenchia mesmo assim, e a regra fixa da D2 premiava a invenção.

**Precedência de dados agora:** API de ATS > `schema.org/JobPosting` > campo estruturado da
fonte > texto visível. Campo sem respaldo fica vazio — vazio o D2 pune, inventado o D2
premiava.

### Adicionado

- **Conector de ATS via API pública** (`triagem/ats.py`), padrão Adapter, começando pelo
  Greenhouse. Cobre o board direto e o iframe embutido no site da empresa, onde a URL não
  menciona Greenhouse e o token só existe no `<script>` do embed. Contra a vaga da AvePoint:
  `localizacao` de `"Remoto"` para `"Da Nang, Da Nang, Vietnam"`, `publicada_em` de vazio
  para `2025-02-09` (532 dias, cortada pelo teto de 60) e descrição de 85 para 2.679 chars.
- **Extrator de `schema.org/JobPosting`** com `hiringOrganization`, `jobLocationType`,
  `datePosted`, `validThrough` e `description`. `validThrough` no passado descarta a vaga
  sem consultar o LLM.
- **Cascata de deduplicação em três camadas** (`triagem/dedup.py`), rodando na persistência.
  Numa execução real reconheceu 3 vagas vindas do Google Search com URLs completamente
  diferentes das gravadas, economizando 3 análises num lote de 5.
- **Alfândega de URL**: domínio nu, blacklist de caminhos e página de listagem descartados
  antes de qualquer requisição ou token. `solides.com.br` (home) e
  `encontreumnerd.com.br/cadastro-prestador` (formulário) tinham recebido 59/100 e 58/100.
- **`triagem/replay.py`**: guarda o blob do grounding e o HTML cru **só em caminhos de
  falha**, com teto e TTL, para reproduzir falhas que a fonte não repete.
- **`migrar_historico.py`**: migração de execução única, preservando status manual.
- **Meta-testes** que leem o texto dos prompts e conferem contra as constantes do código.

### Corrigido

- **Coerção por schema forçava o LLM a chutar o regime.** O `Literal` só aceitava
  `remoto | hibrido | presencial`; sem estado de "não sei", o modelo era obrigado a escolher
  para não estourar `ValidationError`, e escolhia "remoto" — 10/10 na D2. Ele documentava a
  coerção nos próprios alertas ("assumido como remoto por padrão de mercado"). Com
  `indefinido` (D2 = 4) e alerta determinístico, a mesma vaga caiu de 71,5 para 54,0.
- **Google Search estava permanentemente morto.** `MODELO_BUSCA` era `gemini-3.5-flash-lite`,
  e o grounding não tem cota no tier gratuito em nenhum modelo 2.0/3.x — 429 na primeira
  chamada, com chave nova. Medido contra sete modelos; só o `gemini-2.5-flash` passa.
- **`url_context` derrubava a fonte** com `400 INVALID_ARGUMENT (21 > 20)`. Removido: a
  leitura de página agora é feita pelo extrator de JSON-LD. Google Search voltou de 0 para
  25 fontes citadas, e o pré-filtro de 8 para 14 candidatas.
- **Erros da API apareciam só como `ClientError`.** Um 429 de cota, um 400 de limite de URLs
  e um 504 de timeout eram indistinguíveis — três modos de falha reais, encontrados nesta
  validação. `_resumo_erro()` leva a mensagem achatada, truncada e com credenciais redigidas.
- **Localização inventada no caminho de texto livre.** `_ancorar_localizacao()` apaga o campo
  quando não há respaldo, com janela de 600 caracteres em volta do anúncio — senão o "remoto"
  de qualquer vaga vizinha no blob aprovaria esta.
- **Nome de portal gravado como empregador.** "Nerdin Vagas de TI" era o site; o JSON-LD da
  página mostra anunciante anônimo e `validThrough` de sete meses atrás.
- **`_empresa_do_jsonld_confiavel` reprovava o nome no próprio domínio de carreiras.**
  `avepoint.com` publicando "AvePoint" é a confirmação mais forte que existe, não a mais
  fraca. A coincidência só é suspeita quando o host é um agregador conhecido.
- **Redirect para página de listagem virava chave de dedup.** O GeekHunter aponta a vaga para
  `/pt/vagas`; sem o guarda, todas as vagas do portal ganhariam `link_final` idêntico e a
  Camada A as fundiria em silêncio.
- **Anúncio desativado passava como ativo** quando a página responde 200 e a fonte omite
  `publicada_em`: 9 marcadores novos de expiração.
- **Regra estrita de localização cortava vaga remota.** "Work From Home Junior DevOps" da
  BairesDev era reprovada pela praça declarada, com o regime escrito no título. O título
  passou a valer como declaração — só ele, não a descrição.
- **A suíte escrevia no diretório `.replay` real do usuário**, contaminando com dados
  sintéticos o material que existe para reproduzir falhas reais.

### Alterado

- D2 passa a punir distância, não só regime: praça fora do raio derruba presencial para 1 e
  híbrido para 2. Localização vazia ou genérica **não** é punida.
- D2 de híbrido em Curitiba/Araucária: 8 → 7.
- Cabeçalho de navegador em `_obter()`, com `robots.txt` respeitado e freio de 1,5 s por
  host. O LinkedIn proíbe e fica sem enriquecimento — degradação assumida.
- Prompt de análise: `empresa`, `regime` e `localizacao` viram campos imutáveis, com
  exigência de D2 e D5 citarem o bloco autoritativo na justificativa.
- Testes: 163 → 256.

---

## [Publicado em commits anteriores]

Duas iterações de auditoria sobre o commit inicial.

### Segurança

- **`ADZUNA_APP_ID` vazava em todo artefato.** O `redirect_url` da Adzuna traz
  `utm_source=<APP_ID>`; a URL ia inteira para o relatório, o CSV e o `historico.json`.
  Agora `_limpar_url` remove parâmetros de rastreio de todo link antes de qualquer saída.
- **Credenciais em mensagens de erro.** A URL da Jooble contém a chave de API. Exceções das
  fontes são capturadas de forma ampla e qualquer texto que chegue ao terminal passa por
  `_redigir_segredos`.
- **CSV injection.** Campos iniciados por `=`, `+`, `-`, `@` ou tab são prefixados com
  apóstrofo — um título malicioso viraria fórmula ativa no Excel.
- **Anonimização do CV.** Blocos entre `<!-- PRIVADO -->` e `<!-- /PRIVADO -->` no
  `perfil/cv_base.md` são removidos antes de qualquer chamada de API, tanto no `cv` quanto
  no `buscar`. Sem marcadores, nada muda.

### Corrigido

- **A Jooble era descartada por inteiro, em silêncio.** Todas as fontes passavam por uma
  "normalização" via LLM: 42 anúncios brutos (29 Jooble + 13 Adzuna) entravam e saíam 7,
  todos da Adzuna. Fontes estruturadas agora viram `VagaEncontrada` em código puro; o
  modelo só normaliza texto livre.
- **Vaga presencial aprovada como remota com 69/100.** O campo estruturado da Adzuna dizia
  "Recife, Pernambuco" e o modelo respondeu `regime=remoto`; a regra fixa do D2 premiava o
  erro com 10/10. A praça declarada pela fonte agora é filtro determinístico anterior ao
  scoring.
- **Um item fora do schema invalidava o lote inteiro.** Validação item a item.
- **Dedup do histórico não funcionava entre execuções** no `buscar`: o ID era hash do texto
  gerado pelo modelo, que muda a cada dia. A chave passou a ser a URL canônica.
- **Vagas distintas do Indeed/LinkedIn eram fundidas** porque `_url_canonica` descartava a
  query string, onde fica o id do anúncio.
- **`httpx.InvalidURL` derrubava a busca inteira** — não herda de `HTTPError`.
- **`TRIAGEM_HISTORICO` definido no `.env` era ignorado** (módulo importado antes do
  `load_dotenv`).
- **Área decidida pela descrição** deixava passar "Data Engineer" e "Talent Sourcer"; o
  termo-alvo passou a ser exigido no título.
- **Corte de experiência era só em português**: `5+ years` não era detectado.
- **Sem timeout nas chamadas de API** — uma conexão pendurada travava a thread e o
  `as_completed` nunca fechava o lote. Agora 90 s (análise/CV) e 180 s (busca).
- **`--sem-cache` ainda servia cache vencido** no caminho de fallback.
- **A metabusca disparava 4 consultas em rajada** e o DuckDuckGo estrangulava a última
  (8, 8, 8, 1 = 25 resultados; com pausa de 1,5–3 s, 8, 8, 8, 8 = 32). Em condição pior a
  rajada zerava a fonte. Numa busca real: 0 → 21 resultados, 3 → 5 candidatas, 0 → 1
  aprovada.
- **Post de rede social aprovado como vaga**: um tweet de bot passou com 74/100 porque o
  host era desconhecido e respondeu 200. Hosts não-canônicos (X, Telegram, Reddit,
  encurtadores) são cortados e no LinkedIn o caminho precisa conter `/jobs/`.

### Adicionado

- Cache com TTL por fonte (`triagem/cache.py`) e uso de entrada vencida como rede quando a
  fonte não responde. Flags `--sem-cache` e `--testar-fontes`.
- Circuit breaker do Google Search: 3 falhas seguidas abrem o circuito por 24 h.
- Dedup entre fontes pela URL final: Jooble e Adzuna redirecionam para o mesmo anúncio.
- Enriquecimento da descrição a partir da página do anúncio, reaproveitando a requisição
  que a validação de link já fazia (Adzuna trunca em ~500 e Jooble em ~290 caracteres).
- Ancoragem determinística do campo `empresa` no caminho de texto livre.
- Backoff exponencial com jitter para 429/5xx/timeout, com política por fonte
  (Jooble/Adzuna 3 tentativas, DDGS 2 com espera longa, Google Search 1).
- Filtros de senioridade em inglês (`mid level`, `team lead`) e de patrocínio de visto
  (`we do not sponsor`, `unable to sponsor`).
- Filtro de validade do anúncio (60 dias) e de banco de talentos.
- `--pesos` para ajustar as dimensões do score pela linha de comando.
- Diagnóstico por fonte impresso a cada execução.
- Poda automática do cache (30 dias) no carregamento e subcomando `triar limpar-cache
  [--tudo]` — o TTL decidia o que era servido, mas nada removia entrada do disco.

### Alterado

- Prompts e schemas JSON carregados uma vez (`lru_cache` / constante de módulo) em vez de a
  cada vaga.
- CI passou a rodar em matriz 3.10/3.13 e a incluir `compileall` e `pip check`.
- Testes: 37 → 104.
