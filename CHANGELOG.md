# Changelog

Todas as mudanças relevantes deste projeto. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não publicado]

Duas iterações de auditoria sobre o commit inicial. Tudo abaixo está no working tree e
ainda **não foi commitado nem publicado**.

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
- Testes: 37 → 92.
