# Estado do projeto

CLI Python para triagem de vagas, histórico de candidaturas, exportação e geração de
material de CV. Também busca vagas atuais na web a partir de um pedido em linguagem
natural e do CV base. A integração de IA usa a API Google Gemini.

## Integração atual

- Provedor: Google Gemini pelo pacote `google-genai`.
- Modelo padrão da análise: `gemini-3.1-flash-lite` (`--modelo lite`).
- Alternativa: `gemini-3.5-flash` (`--modelo flash`).
- Modelo da descoberta com Google Search Grounding: `gemini-3.5-flash-lite`.
- Chave: variável `GEMINI_API_KEY`, criada em <https://aistudio.google.com/apikey>.
- A triagem usa saída JSON estruturada validada pelo schema Pydantic.
- Há paralelismo, backoff exponencial com jitter para 429/5xx/timeout e uma segunda
  tentativa sequencial no fim do lote.
- Toda chamada de API tem timeout explícito (90 s análise/CV, 180 s busca). Sem ele, uma
  conexão pendurada travava a thread e o `as_completed` nunca fechava o lote.
- **O estado do circuit breaker é persistente**, gravado em `.cache_busca.json` junto com o
  cache. Ele conta falhas *entre* execuções do CLI — que é o cenário que importa: rodar
  `triar buscar` três vezes seguidas com a cota estourada abre o circuito na terceira, e a
  quarta execução pula a fonte sem gastar a chamada. Um contador em memória não protegeria
  nada num programa que termina a cada uso.
- O cache se poda sozinho no carregamento: entradas com mais de 30 dias são removidas do
  disco. `triar limpar-cache [--tudo]` faz a limpeza manual.
- **A metabusca espaça as próprias consultas.** O DuckDuckGo degrada por cadência, não por
  volume: as 4 consultas de `_busca_metasearch` saíam em sequência imediata e a última
  voltava com 1 resultado em vez de 8. Agora há pausa aleatória de 1,5–3 s entre elas (não
  só na retentativa) e parada antecipada quando já há material suficiente para o limite
  pedido — menos consultas, menos exposição ao bloqueio.
- Prompts e schemas JSON são carregados uma vez (`lru_cache` / constante de módulo), não a
  cada vaga.
- `triar buscar` combina Jooble, Adzuna, Google Search Grounding e metabusca DDGS.
- A antiga integração Anthropic e a Batch API foram removidas.

## Divisão de responsabilidade entre código e modelo

Regra da arquitetura: **o modelo só toca no que é ambíguo.**

- Jooble e Adzuna devolvem JSON estruturado — viram `VagaEncontrada` em código puro.
  Antes passavam pelo LLM para "normalizar" e isso perdia dados: numa execução real de
  42 anúncios (29 Jooble + 13 Adzuna) sobraram 7, todos da Adzuna. A Jooble era zerada.
- O LLM normaliza só o texto livre do Google Search Grounding e da metabusca DDGS, e
  agora item a item: uma vaga fora do schema não invalida mais a lista inteira.
- Senioridade, área, validade, localização e elegibilidade geográfica são decididas por
  regra determinística **antes** da triagem paga.
- A regra fixa do D2 (remoto=10, híbrido CWB=8, presencial CWB=6) continua sobrescrevendo
  a nota do modelo. Ela amplificava alucinação: para uma vaga cuja Adzuna dizia "Recife,
  Pernambuco" o modelo respondeu `regime=remoto` e a regra premiava com 10/10. Por isso a
  praça declarada pela fonte passou a ser um filtro determinístico anterior ao scoring.

## Segurança de credenciais

- `.env` é gitignorado e nunca é lido pelo processo de análise.
- O `redirect_url` da Adzuna carrega `utm_source=<ADZUNA_APP_ID>`. Todo link passa por
  `_limpar_url`, que remove parâmetros de rastreio antes de o link chegar ao relatório,
  ao CSV ou ao `historico.json`.
- Exceções das fontes são capturadas de forma ampla porque a URL da Jooble contém a chave
  de API; qualquer mensagem que chegue ao terminal passa por `_redigir_segredos`.
- O CSV neutraliza células iniciadas por `= + - @` (CSV injection no Excel).

## Privacidade

Segundo a página de preços do Gemini, conteúdo enviado pela camada gratuita pode ser usado
para melhorar os produtos do Google. O comando `cv` envia o CV base e o texto da vaga.
Não use dados pessoais sensíveis que você não queira enviar ao provedor. Para tratamento
sem uso dos dados para melhoria do produto, consulte as condições do plano pago.

`perfil/cv_base.md` é gitignorado. Versione apenas `perfil/cv_base.exemplo.md`.

Blocos entre `<!-- PRIVADO -->` e `<!-- /PRIVADO -->` no CV base são removidos por
`carregar_cv_base()` antes de qualquer chamada de API — vale para `cv` e para `buscar`.

## Trade-offs decididos (e por que não fizemos o "óbvio")

- **Sem token bucket / rate limiter próprio.** O backoff exponencial já absorve os 429 e
  nenhuma execução real precisou dele nas chamadas de análise. Um limitador global num CLI
  de execução única é complexidade sem retorno; se aparecer 429 em série, reduza
  `--paralelo`.
- **Sem throttle entre execuções.** O backoff protege dentro de uma execução, mas nada
  impede rodar `triar buscar` quatro vezes em dez minutos. Fica em backlog: se começar a
  aparecer 429 em série, gravar um timestamp da última chamada junto ao cache e recusar
  nova busca antes de 60 s. Não foi implementado porque hoje o cache já absorve o caso
  comum (execuções repetidas com o mesmo pedido nem chegam às APIs).
- **Sem SQLite.** O histórico é JSON carregado inteiro. Adequado para dezenas/centenas de
  vagas. Gatilho para migrar: **~500 entradas**, quando paginação e índice por empresa/data
  passam a valer o custo.
- **Sem regras em YAML.** O único usuário do projeto é técnico; trocar constantes Python
  tipadas por três arquivos de configuração adicionaria parsing e tiraria a checagem
  estática sem ganho real.
- **Sem scraping do LinkedIn como fonte.** Os termos de uso proíbem, o bloqueio é agressivo
  e o HTML muda com frequência — seria a parte mais frágil do sistema. Fontes estruturadas
  adicionais (Gupy, InfoJobs) ficam em backlog, dependendo de endpoint público.
- **D1 continua com peso 30%.** É a dimensão mais subjetiva e a mais pesada, o que é uma
  fragilidade real; mas os pesos são especificação de produto, não bug. Quem quiser reduzir
  usa `--pesos d1=0.15,d2=0.30,d3=0.25,d4=0.15,d5=0.15`. Decompor D1 em subcritérios
  objetivos (mentoria, budget de treinamento, porte da empresa) é a evolução natural — não
  foi feito porque a maioria desses dados não está no texto do anúncio, e pedi-los ao modelo
  apenas moveria a alucinação de lugar.

## Limitações conhecidas

- **Google Search Grounding está estourando cota** (`429 RESOURCE_EXHAUSTED`) na chave
  gratuita. O circuito abre por 24 h após 3 falhas e a busca cai na metabusca DDGS.
  `triar buscar --testar-fontes` reabre assim que a cota voltar.
- **A metabusca DDGS não tem chave, contrato nem SLA** — é fallback, não fonte. O
  estrangulamento por rajada está resolvido (ver "espaça as próprias consultas" acima):
  medido no mesmo conjunto de consultas, rajada rendia 25 resultados com a última consulta
  estrangulada (8, 8, 8, 1) e com pausa rendeu 32 (8, 8, 8, 8); numa condição pior — logo
  após `--testar-fontes` consumir a franquia — a rajada zerava a fonte inteira. Depois da
  correção, quatro execuções consecutivas com `--sem-cache` devolveram 21, 21, 22 e 22
  resultados. **Isso não garante estabilidade futura**: o DuckDuckGo pode mudar a política
  de bloqueio sem aviso. Por isso o cache de texto livre (TTL 1 h) continua sendo a rede —
  quando a fonte cair, a coleta anterior é servida com a idade declarada em tela.
- **Descrições truncadas na origem** (Adzuna ~500, Jooble ~290 caracteres). O enriquecimento
  pela página do anúncio cobre parte disso, mas falha em portais que exigem JavaScript ou
  bloqueiam bots — nesses casos a triagem ainda vê o texto curto.
- **`empresa` em vagas de texto livre (DDGS/Google Search) é extraída deterministicamente do
  material de origem.** Se o nome devolvido pelo modelo não constar explicitamente lá, o
  campo vira `Desconhecida` com `confianca_empresa="baixa"`. **Não há heurística de
  recuperação**: o erro é evitado, o dado certo não é reconstruído. Recuperá-lo exigiria ler
  a página do anúncio e inferir o empregador do HTML — o que reintroduziria adivinhação no
  ponto exato que essa checagem existe para proteger.
- **Links de agregador** (`adzuna.com.br/details/...`, `jooble.org/jdp/...`) respondem 200
  mesmo para anúncios encerrados, então a validação de link não os pega.
- **Sem rate limiting próprio.** `--paralelo` (padrão 4) dispara chamadas simultâneas; a
  proteção contra 429 é o backoff, não um token bucket. Se a chave começar a levar 429 em
  série, reduza `--paralelo` antes de mexer no código.
- **Dedup entre fontes depende do redirect resolver.** Jooble e Adzuna normalmente
  redirecionam para o mesmo anúncio e a URL final funde as duas entradas. Quando o portal
  bloqueia o acesso (403/429) a URL final não é obtida e a vaga pode entrar duas vezes.
- **O `analisar` continua chaveado por hash do texto** — correto ali, porque o arquivo de
  entrada é estável. Só o `buscar` usa URL.
- **D1 (crescimento, peso 30%) é a dimensão mais subjetiva** e a de maior peso; é onde o
  modelo mais opina sem evidência no texto.
- O histórico do comando `buscar` passou a usar a URL canônica como chave. Entradas
  gravadas por versões anteriores continuam válidas, mas com IDs antigos.

## Validação

```powershell
python -m pytest -q
python -m ruff check .
python -m compileall -q triagem triar.py
python -m pip check
```

O teste real da API depende de uma `GEMINI_API_KEY` válida.
