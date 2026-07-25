# Resumo de Validação — Triagem de Vagas v2.1

Data: 2026-07-25 · Suíte do projeto: **104/104 passando** · Sondas de validação escritas para este teste: **149 asserções**

## Ressalvas de escopo (leia antes dos resultados)

Três desvios entre o enunciado e o ambiente real:

1. **O projeto não é um repositório Git.** `automatizacao-curriculo-main` é um diretório
   comum dentro do workspace `ecosystem-predictor`, que o ignora via `*/` no `.gitignore`.
   Não há `.git` próprio, nenhum remote `leonardosovienski/automatizacao-curriculo`, e
   o commit `57e930e` / branch `main` **não são verificáveis aqui**. O worktree criado para
   esta sessão é do repo `ecosystem-predictor` e contém outro conteúdo — não foi usado.
   A1 foi validado pelo conteúdo do `.gitignore` do projeto, não por `git ls-files`.
2. **Não existe `.env`.** Sem `GEMINI_API_KEY`/`JOOBLE_API_KEY`/`ADZUNA_*`, nenhuma busca
   real, chamada ao Gemini ou resolução de redirect foi executada. Os itens que dependem
   de rede foram cobertos por **dublês determinísticos** das fontes e do modelo, exercitando
   o código real do projeto. Onde a evidência é simulada, está marcado como tal.
3. **`perfil/cv_base.md` não existe** (só o `.exemplo.md`). A regra de nunca ler o CV real
   foi respeitada por construção — A4 foi validado com um CV sintético apontado por
   monkeypatch em `curriculo.CV_BASE`.

Nada no diretório do projeto foi criado, alterado ou removido.

---

### Segurança e Privacidade
- [x] `.env` e `cv_base.md` gitignorados — os 4 padrões (`.env`, `perfil/cv_base.md`, `historico.json`, `.cache_busca.json`) estão no `.gitignore`; nenhum dos arquivos existe no disco
- [x] Credenciais não vazam em artefatos — nenhuma chave embutida no código; `.env.example` só tem placeholders; `_redigir_segredos` neutraliza as 4 variáveis
- [x] URLs sanitizadas — `utm_*`, `gclid`, `fbclid`, `trk`, `refid`, `position` removidos; caixa alta tratada; fragmento descartado; chave de dedup estável
- [x] Anonimização de CV funciona — validada ponta a ponta: o sentinel `teste-sensivel-12345` não chega ao payload da API (**ressalva: falha em silêncio com bloco malformado — BUG-07**)
- [x] CSV injection neutralizado — `=`, `+`, `-`, `@`, `\t`, `\r` prefixados com `'`; BOM utf-8-sig presente

### Robustez
- [x] Circuit breaker funciona — 3 falhas abrem por 24 h; sucesso fecha na hora; `buscar_vagas` pula a fonte de fato (0 chamadas)
- [x] Cache hit/miss funcionam — TTL 6 h (Jooble/Adzuna) e 1 h (DDGS) respeitados; idade informada no log
- [x] `--sem-cache` força fetch fresco — ignora entrada fresca e vencida (**ressalva: BUG-05**)
- [x] Cache vencido serve como fallback — 8 h + fonte fora do ar → serve o vencido com aviso explícito
- [x] Timeout e retry funcionam — 90 s análise / 180 s busca; 429/5xx → 3 tentativas com backoff (3,96 s medidos); 400/403 falham na primeira
- [ ] **Schema violation não derruba lote** — correto na busca; **quebrado no `analisar` (BUG-01)**

### Qualidade de Dados
- [x] Hosts não-canônicos rejeitados — X, Twitter, Telegram, Reddit, bit.ly, lnkd.in, subdomínios (**ressalva: BUG-02**)
- [x] LinkedIn `/feed/update/`, `/posts/`, `/pulse/` rejeitados; `/jobs/view/` aceito
- [ ] **Localização declarada pela fonte vence texto** — **não vence: o texto vence (BUG-03)**
- [x] Senioridade bilíngue filtrada — Pleno, Senior, Sr, Staff, Lead, Principal, Arquiteto, Especialista, Gerente, Coordenador, Mid-Level; `3+ anos`/`3+ years` penalizados; "DevOps Junior com 3 anos aceitáveis" corretamente aprovada
- [x] Área exigida no título — Data Engineer, Data Scientist, Talent Sourcer, Product Owner, Suporte N1 rejeitados mesmo citando cloud/devops na descrição
- [ ] **Restrição de visto rejeita "US only", etc.** — só em portais conhecidos; **passa em host desconhecido (BUG-04)**
- [x] Empresa alucinada → `Desconhecida` + confiança baixa — ancoragem determinística no material de origem
- [x] Dedup entre fontes funciona — Jooble + Adzuna → mesma URL final → 1 vaga, mesclagem logada *(redirect simulado)*
- [x] D2 fixo: remoto=10, híbrido=8, presencial=6 — testado com o modelo retornando 0/3/5/9/10; justificativa marcada; objeto de entrada não mutado
- [x] `--pesos` valida soma 1.0 — soma errada, dimensão desconhecida e valor não numérico rejeitados com mensagem clara (**ressalva: BUG-11**)
- [ ] **Enriquecimento de descrição funciona** — enriquece, mas **corta no offset errado (BUG-06)**

### Integração
- [x] Fluxo end-to-end — `analisar → historico → status → cv` completo com export; *a perna `buscar` não pôde ser executada ao vivo (sem credenciais)*
- [x] Re-análise preserva status manual — `aplicado` e `entrevista` sobrevivem ao `--reanalisar`
- [x] Deduplicação dentro do input — 3 cópias → 1 chamada de API (**ressalva: silenciosa, BUG-08**)
- [x] Export MD/CSV consistentes — mesma contagem e mesmos scores; acentos abrem no Excel (BOM); extensão inválida recusada

### Estresse
- [x] Limite alto não quebra — `limite_coleta` sempre entre 20 e 50; `--limite 0`, negativo e não numérico rejeitados pelo argparse
- [x] Input vazio erro claro — `Input vazio. Envie as vagas em JSON ou texto livre.`
- [x] JSON malformado erro claro — aponta linha/coluna, sem traceback
- [x] ID ambíguo erro claro — `ID 'a' é ambíguo: a1b2c3d4e5, a4b5c6d7e8.`
- [x] Histórico corrompido erro claro — sem traceback (**ressalva: não menciona o `.bak`, BUG-09**)

---

## Bugs Encontrados

### BUG-01 · ALTO · Um item inválido no JSON derruba o lote inteiro (`analisar`)

`entrada.py:33` — `if not vagas or any(not vaga.strip() for vaga in vagas): raise ValueError(...)`

Um único item que não produza texto invalida **todas** as vagas do arquivo.

- **Reproduzir:** JSON com 3 vagas válidas + `{}` → `triar.py analisar lote.json`
- **Esperado (item B6 do plano):** 3 processadas, 1 descartada com alerta
- **Obtido:** `Erro no input: JSON não contém nenhuma vaga com dados.`, exit 1, **zero vagas processadas**
- **Nota:** o caminho da busca faz certo — `_normalizar_texto_livre` valida item a item e loga
  `metabusca: 1 item(ns) fora do schema descartado(s)`. A inconsistência é só no `analisar`.

### BUG-02 · ALTO · Host não-canônico alcançado por redirect não é reavaliado

`buscador.py:350` (`_host_de_anuncio`) roda só sobre `vaga.link` em `_selecionar_candidatas:577`.
`_validar_links` resolve `link_final` (`buscador.py:547`) mas **nunca recheca o host**.

- **Reproduzir:** vaga com `link = https://ow.ly/abc` que redireciona para `https://x.com/liftmycv/status/123456`
- **Esperado:** rejeitada — é exatamente a regressão que o comentário em `buscador.py:159-162` diz ter motivado o filtro ("uma execução real aprovou com 74/100 um tweet de bot")
- **Obtido:** sobrevive ao `_validar_links` com `link_final = https://x.com/liftmycv/status/123456`
- **Agravante:** a lista de encurtadores é incompleta. Passam no pré-filtro: `ow.ly`, `buff.ly`, `is.gd`, `rb.gy`, `shorturl.at`
- **Impacto:** falso positivo do tipo mais caro — o candidato vai parar num tweet em vez da página de candidatura

### BUG-03 · MÉDIO · Negação de "remoto" é lida como remoto; praça declarada não vence o texto

`buscador.py:389-404` (`_local_declarado_incompativel`) — retorna "compatível" se **qualquer**
marcador de remoto aparecer em título + descrição + localização, sem olhar para negação.

- **Reproduzir:** `localizacao = "Recife, Pernambuco"`, descrição contendo `"Vaga presencial. Não oferecemos trabalho remoto."`
- **Esperado (item C2 do plano):** rejeitada — a localização declarada pela fonte vence o texto
- **Obtido:** **aprovada**. Basta a palavra "remoto" existir no anúncio, ainda que negada
- **Impacto:** combina com a regra fixa do D2 — se o modelo classificar como `remoto`, a vaga
  presencial em Recife recebe 10/10 em Regime/Localização, que é o cenário que a função existe para impedir
- **Nota de spec:** o enunciado afirma "a localização declarada pela fonte vence o texto"; o docstring
  da função descreve o comportamento mais fraco que está implementado. Os dois documentos divergem

### BUG-04 · MÉDIO · Restrição de visto não é detectada em host desconhecido

`buscador.py:407-419` (`_localizacao_compativel`) — a lista `TERMOS_RESTRICAO_EXTERIOR` só cobre
frases literais; hosts fora de `PORTAIS_BRASILEIROS`/`PORTAIS_INTERNACIONAIS` caem no `return True` final.

- **Reproduzir:** vaga em `https://careers.acme-global.io/jobs/1234` com qualquer uma destas frases
- **Obtido:** **10 de 10 passam** — `Must be authorized to work in EU`, `right to work in the UK`,
  `legally authorized to work in Canada`, `requires US work authorization`, `residing in Germany`,
  `Sponsorship is not provided`, `We cannot sponsor work visas`, `must hold EU citizenship`,
  `within the continental United States`, `must reside in Australia`
- **Contraste:** as mesmas 10 frases são **corretamente bloqueadas** em `weworkremotely.com`,
  porque lá a regra exige menção explícita a Brasil/LATAM/global. O buraco é só no host desconhecido —
  justamente o domínio de carreiras da própria empresa, que é comum na saída do Google Search

### BUG-05 · MÉDIO · `--sem-cache` desliga o circuit breaker e descarta as falhas

`buscador.py:1075` — `estado_cache = cache.carregar() if usar_cache else {"entradas": {}, "circuitos": {}}`.
Com `--sem-cache` o estado de circuito **gravado em disco nunca é lido**, e `cache.salvar` não é chamado (`buscador.py:1163`).

- **Reproduzir:** circuito do Google Search aberto por 24 h no disco → `triar buscar --sem-cache`
- **Esperado:** a fonte esgotada continua sendo pulada; a nova falha conta
- **Obtido:** 1 chamada ao Google Search apesar do circuito aberto (com cache: 0 chamadas);
  contador de falhas permanece em 3; arquivo de cache inalterado
- **Impacto:** `--sem-cache` gasta cota e latência contra uma fonte sabidamente esgotada, e o
  circuito nunca aprende com essas falhas. Também contraria o item B3 do plano, que espera
  que "o cache real foi atualizado após a execução"

### BUG-06 · MÉDIO · Enriquecimento corta no offset errado (índice em espaço de coordenadas diferente)

`buscador.py:520-525` — `posicao` é calculado sobre o texto **normalizado**
(`normalizado.find(inicio)`) e usado para fatiar o texto **visível** (`visivel[posicao:...]`).
`_normalizar` remove acentos e pontuação e colapsa espaços, então o índice normalizado é sempre ≤ o real.

- **Reproduzir:** página cujo menu antes da descrição tenha muita pontuação
- **Obtido:** desalinhamento de **168 caracteres**; a descrição gravada começa com
  `'| Home | Vagas | Empresas | Login | ---- *** ---- MENU >>> |'`
- **Esperado:** começar na âncora, na primeira palavra da descrição original
- **Impacto:** cola exatamente o "menu do portal" que o docstring da função diz querer evitar,
  e desperdiça parte da janela de 6000 caracteres enviada ao modelo

### BUG-07 · MÉDIO · Anonimização do CV falha em silêncio (fail-open)

`curriculo.py:23-41` — o regex exige o par completo `<!-- PRIVADO --> … <!-- /PRIVADO -->`.
Bloco não fechado ou com marcador diferente **não é removido, e ninguém avisa**:
`carregar_cv_base` descarta a contagem de blocos (`limpo, _ = remover_blocos_privados(...)`).

- **Reproduzir:** `<!-- PRIVADO -->teste-sensivel-12345` sem tag de fechamento
- **Obtido:** 0 blocos removidos, sentinel presente no texto enviado à API, nenhum aviso
- **Também falha com:** `<!-- FIM PRIVADO -->`, `<!-- \PRIVADO -->`
- **Impacto:** é o único controle entre telefone/e-mail pessoais do CV e a API do Google.
  Um erro de digitação no marcador vaza dados pessoais silenciosamente
- **Correção barata:** usar a contagem já retornada — avisar quando `<!-- PRIVADO -->` aparece
  no texto mas `removidos == 0`

### BUG-08 · BAIXO · Duplicata dentro do input é pulada em silêncio

`cli.py:235-237` — `if vid in vistos: continue`, sem nenhuma mensagem.

- **Reproduzir:** JSON com a mesma vaga 3×
- **Obtido:** 1 chamada de API (dedup correto), mas nada no stdout; o relatório mostra
  "TOTAL ANALISADAS: 1" para um arquivo de 3 itens, sem explicar o sumiço
- **Esperado (item D3 do plano):** aviso "duplicada no input"

### BUG-09 · BAIXO · Erro de histórico corrompido não menciona o `.bak`

`historico.py:48` — a mensagem cita o arquivo e o erro de JSON, mas não o backup.
`salvar()` **cria** um `.bak` a cada gravação (`historico.py:66`), então a recuperação existe e não é oferecida.

- **Obtido:** `Erro: Não foi possível ler o histórico '…': Expecting property name…`
- **Esperado (item E5 do plano):** sugerir restaurar o `.bak`

### BUG-10 · BAIXO · Redação de segredos aplicada de forma inconsistente

`_redigir_segredos` é usado em `cli.py:166` e `cli.py:184`, mas **não** em `cli.py:259`
(erro ao criar cliente no `analisar`), `cli.py:311` (falhas por vaga), `cli.py:431` e `cli.py:440`
(comando `cv`). Risco baixo — o SDK do Gemini manda a chave em header, não na URL — mas
a proteção deveria ser uniforme.

### BUG-11 · BAIXO · `--pesos` não valida a faixa individual

`scoring.py:41-43` valida só a soma. `--pesos d1=1.55,d2=-1.00,d3=0.20,d4=0.15,d5=0.10`
soma 1.0 e é aceito, com peso negativo. Score resultante no exemplo: 40.0 (dentro da faixa,
mas a composição perdeu o sentido). Vale exigir `0 ≤ peso ≤ 1`.

### BUG-12 · INFORMATIVO · MD e CSV exibem o score com precisão diferente

`exportar.py:50` usa `:.0f` (86) e o CSV grava o valor cru (86.5). Mesmo score, exibição
divergente entre os dois artefatos do mesmo relatório.

---

## Verificações que tentei quebrar e resistiram

- **CSV injection com espaço à frente** (`" =1+1"`): não é neutralizado, **mas também não é
  explorável** — Excel trata célula iniciada por espaço como texto. Não é bug.
- **Bypass de host por subdomínio** (`mobile.twitter.com`): corretamente rejeitado.
- **`_url_canonica` como chave de dedup:** estável sob parâmetros de rastreio diferentes —
  a mesma vaga com `utm_source` e com `trk` gera a mesma chave.
- **Mutação do objeto de análise pela regra fixa do D2:** há cópia defensiva; o objeto do
  chamador não é alterado.
- **Retry em erro não transitório:** 400/403 falham na primeira tentativa, sem gastar cota.
- **Cache corrompido:** tratado como descartável, não quebra a busca (ao contrário do
  histórico, que falha alto — e está certo assim).
- **Empresa alucinada:** a ancoragem é determinística e não consulta o modelo sobre a própria
  confiança. `Sylision` e `Casado.dev` viram `Desconhecida`/`baixa`, como documentado.

---

## Veredito

**[x] PRONTO COM RESSALVAS**

O núcleo de segurança e privacidade está sólido: nenhuma credencial vaza, URLs são
sanitizadas, o CSV é seguro e a anonimização do CV funciona no caminho feliz. A suíte do
projeto passa integralmente e as decisões arquiteturais documentadas (fonte estruturada sem
LLM, D2 como regra fixa, ancoragem de empresa, dedup por URL final) estão de fato
implementadas e se comportam como anunciado.

Nenhum bug **crítico** foi encontrado e nenhum bug impede o uso do sistema. Mas os dois
**ALTO** devem ser corrigidos antes de confiar na saída sem revisão manual:

- **BUG-01** quebra um fluxo documentado (`analisar` em lote) com mensagem enganosa;
- **BUG-02** reabre exatamente o falso positivo — anúncio que é post de rede social — que
  o código diz ter sido escrito para fechar.

Os quatro **MÉDIO** (BUG-03 a BUG-06) são todos vetores de **falso positivo** ou de perda de
qualidade de dados, que é o critério que o próprio plano de teste coloca como mais importante.

**Ainda não validado ao vivo, por ausência de credenciais:** busca real nas 4 fontes, resolução
de redirect contra portais reais, enriquecimento contra HTML real, comportamento de cota do
Gemini e o item C6 (mesma URL com empresa diferente entre execuções). Recomendo repetir as
fases A3, B1-B5, C6, C7, C10 e D1 com `.env` configurado antes de considerar o sistema validado
de ponta a ponta.
