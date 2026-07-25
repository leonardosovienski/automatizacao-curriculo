# PROMPT — Colocar a triagem de vagas em uso real

## Contexto

CLI Python de triagem de vagas em `C:\Claude-projetos\Claude\automatizacao-curriculo-main`.
Busca vagas em 4 fontes (Jooble, Adzuna, Google Search Grounding, DDGS), filtra por critérios
determinísticos, pontua as aprovadas via Gemini em 5 dimensões (D1–D5), mantém histórico com
deduplicação e gera material de candidatura (CV sob medida + mensagem).

Perfil-alvo: estágio/Júnior em DevOps, DevSecOps, Cloud, Platform Engineering, SRE, C#/.NET.
Preferência: remoto no Brasil, ou presencial/híbrido em Curitiba/Araucária.

**Atenção — dois fatos que economizam tempo:**

1. **Não é um repositório Git**, apesar de o README citar
   `github.com/leonardosovienski/automatizacao-curriculo`. É um diretório comum dentro do
   workspace `ecosystem-predictor`, que o ignora via `*/`. Não há `.git` próprio: `git diff`,
   `git log` e `git stash` não funcionam ali, e **não há como reverter uma mudança ruim**.
   Antes de editar qualquer coisa, faça backup dos arquivos que for tocar.
2. **Dependências não estão no Python do sistema.** Existe um venv pronto em `C:\tvenv`.
   Use `C:/tvenv/Scripts/python.exe` para tudo. Se precisar recriar, use caminho curto —
   instalar em diretório profundo falha com
   `ImportError: DLL load failed while importing _cffi_backend` (limite de path do Windows).

## Estado atual

O projeto passou por uma validação completa offline em 2026-07-25. 11 bugs foram corrigidos
(lote derrubado por item inválido, host não-canônico via redirect, negação de "remoto",
restrição de visto em host desconhecido, `--sem-cache` furando o circuit breaker, offset do
enriquecimento, anonimização do CV falhando aberto, e 4 menores). A suíte tem **163 testes**
e cobre todos os filtros determinísticos.

**O que nunca foi testado: qualquer coisa que use rede.** A validação inteira rodou com
dublês porque não havia credenciais. Continuam sem validação ao vivo:

- busca real nas 4 fontes e resolução de redirect contra portais reais
- enriquecimento de descrição contra HTML real
- comportamento do Gemini (qualidade das notas D1–D5, alucinação de empresa)
- cota, throttling e o circuit breaker sob 429 de verdade
- fluxo completo `buscar → historico → status → cv`

**É exatamente esse o objetivo deste chat: sair do "validado offline" para "produz uma lista
de vagas em que eu me candidataria de verdade".**

## Regras invioláveis

- NUNCA leia, exiba, edite ou publique `.env` nem `perfil/cv_base.md`. Ambos contêm dados
  sensíveis (chaves de API e dados pessoais). Use `perfil/cv_base.exemplo.md` como referência.
- NUNCA imprima o valor de `GEMINI_API_KEY`, `JOOBLE_API_KEY`, `ADZUNA_APP_ID` ou
  `ADZUNA_API_KEY` em log, mensagem ou arquivo. Para checar vazamento, procure pelo padrão,
  nunca compare imprimindo o valor real.
- Sempre `encoding="utf-8"` em qualquer I/O.
- Não invente resultado de execução. Se algo não rodou, diga que não rodou.

## Fase 0 — Pré-requisitos (checar ANTES de qualquer outra coisa)

Duas coisas precisam existir e **só eu posso criar** — não tente criar por mim:

1. `.env` na raiz do projeto, a partir do `.env.example`. `GEMINI_API_KEY` é obrigatória
   (grátis em https://aistudio.google.com/apikey). Jooble e Adzuna são opcionais, mas sem
   elas sobram só Google Search Grounding e DDGS.
2. `perfil/cv_base.md`, copiado de `perfil/cv_base.exemplo.md` com os `[PREENCHA: ...]`
   substituídos. **Sem esse arquivo, `buscar` e `cv` falham na hora** — `carregar_cv_base()`
   levanta `FileNotFoundError`.

Verifique a existência dos dois **sem ler o conteúdo**. Se faltar algum, pare e me diga
exatamente o que fazer. Se existirem, rode a suíte como smoke test do ambiente:

```
cd C:\Claude-projetos\Claude\automatizacao-curriculo-main
C:/tvenv/Scripts/python.exe -m pytest -q
```

Esperado: 163 passando. Se não passar, o ambiente está quebrado — resolva isso primeiro.

## Fase 1 — Primeiro contato com as fontes reais

```
C:/tvenv/Scripts/python.exe triar.py buscar --testar-fontes
```

Me diga quais fontes responderam e quais falharam, e por quê. Se o Google Search estiver com
cota esgotada, o circuito abre por 24 h — confirme que a mensagem aparece e que esse comando
fecha o circuito quando a fonte volta.

## Fase 2 — Primeira busca real, pequena

Comece com limite baixo para não queimar cota enquanto ainda não sabemos se a saída presta:

```
C:/tvenv/Scripts/python.exe triar.py buscar --limite 5 --saida primeira.md
```

Observe o funil impresso (quantas vagas cada fonte trouxe, quantas cada filtro cortou e por
qual motivo) e me traga esses números. É a primeira vez que vemos o pipeline com dados reais.

## Fase 3 — Julgar a QUALIDADE, que é o que importa

Esta é a fase central. Para cada vaga aprovada, abra o link e responda:

- O link leva mesmo à página de candidatura, ou a um agregador/post/página morta?
- A empresa está correta, ou foi inventada? (deve aparecer `Desconhecida` quando não há
  respaldo no material de origem)
- O regime e a localização batem com o anúncio real?
- A senioridade bate, ou é Pleno/Sênior disfarçado?
- **Eu me candidataria a esta vaga?**

E o mais importante: **veja as descartadas** e me diga se alguma boa foi cortada por engano,
e por qual filtro. Falso positivo (vaga ruim aprovada) é pior que falso negativo, mas um
filtro que corta tudo também não serve.

Não conserte nada ainda — primeiro me traga o diagnóstico com evidência.

## Fase 4 — Validar ao vivo o que só rodou com dublê

Com dados reais na mão, confirme:

- **Sanitização de URL:** nenhum link em `historico.json`, `.cache_busca.json` ou nos
  `.md`/`.csv` gerados pode conter `utm_source=`, `gclid=`, `fbclid=` ou o `ADZUNA_APP_ID`.
- **Cache:** rode a mesma busca duas vezes e confirme o cache-hit com a idade no log; depois
  rode com `--sem-cache` e confirme que consulta do zero mas **continua respeitando o
  circuito aberto** (foi um bug corrigido, vale confirmar no real).
- **Dedup entre fontes:** ache uma vaga que apareça na Jooble e na Adzuna e confirme que
  vira uma entrada só depois do redirect.
- **Enriquecimento:** ache uma vaga com descrição truncada e compare a descrição no
  histórico com a do site. Deve estar mais completa e **começar no início do texto original**,
  não no menu do portal (também foi um bug corrigido).
- **Empresa:** rode a mesma busca em momentos diferentes e veja se a mesma URL já saiu com
  empresa diferente.
- **Fluxo completo:** `historico` → `status <id> aplicado` → `cv <id> --saida candidatura.md`.
  Avalie se o material gerado presta e se o bloco `<!-- PRIVADO -->` do CV não vazou.

## Fase 5 — Tornar útil

Com o diagnóstico pronto, ajuste o que estiver atrapalhando. Candidatos prováveis:

- termos em `TERMOS_ALVO`, `TERMOS_SENIOR`, `TERMOS_ENTRADA` (em `triagem/buscador.py`)
- limiar do pré-filtro (hoje `_pontuacao_preliminar < 8` descarta)
- `--pesos` das dimensões, se o ranking não refletir o que eu valorizo
- `DIAS_MAXIMOS_ANUNCIO` (hoje 60)
- os prompts em `prompts/system_prompt.md` e `prompts/cv_prompt.md`

**Toda mudança de filtro precisa de teste de regressão** em `tests/test_pipeline.py`, testando
os dois lados: o que deve cortar e o que não pode cortar. A suíte é a única rede de segurança
que existe aqui — não há Git.

## Duas decisões minhas que ficaram em aberto

Traga cada uma quando os dados reais disserem algo sobre ela; não decida sozinho:

1. **Chave do histórico no `analisar`.** Hoje é o hash do texto inteiro, então a mesma vaga
   com uma palavra diferente na descrição vira entrada nova (re-análise e chamada de API
   extra). O caminho `buscar` já usa a URL canônica. Migrar custa reprocessar cada vaga uma
   vez.
2. **Regra estrita de localização.** Hoje, se a fonte declara uma cidade específica fora de
   Curitiba/Araucária e o próprio campo de localização não diz "remoto", a vaga é descartada
   no pré-filtro — mesmo que a descrição diga "100% remota". Numa amostra sintética isso
   fechou 4 falsos positivos e custou 5 vagas boas. **Só o uso real diz se compensa.** Se
   estiver cortando vaga boa demais, a alternativa é deixar passar como candidata e o D2
   punir, em vez de descartar.

## Como quero o retorno

Trabalhe em fases e me mostre o resultado de cada uma antes de seguir. Prefiro diagnóstico com
evidência a conserto rápido: se uma vaga ruim passou, quero saber qual filtro deixou e por quê,
não só que foi corrigida. E se algo não deu para testar, diga qual e por quê.

Comece pela Fase 0.
