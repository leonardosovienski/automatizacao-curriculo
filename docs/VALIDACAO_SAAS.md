# Consolidação e validação do SaaS

Esta revisão integra o conteúdo SaaS já incorporado à `main`, as cinco atualizações
compatíveis da PR #58 e as correções dos sete problemas reproduzidos na auditoria.
As atualizações são lucide-react 1.41.0, postcss 8.5.28, @types/node 26.4.1,
@types/react-dom 19.2.7 e oxlint 1.81.0; o lockfile registra as versões exatas.

## Correções e regressões

| Problema reproduzido | Comportamento corrigido | Cobertura |
| --- | --- | --- |
| DNS mudava entre validação de URL e conexão | Cada conexão usa o IP público validado e confere o destino conectado; Host, SNI e verificação TLS preservam o hostname original | `tests/test_rede_segura.py`, `tests/test_pipeline.py` |
| Exclusão em andamento aceitava senha anterior a uma redefinição | A exclusão trava e recarrega a conta antes de conferir a senha, sem cancelar cobrança se a senha estiver desatualizada | `tests/test_saas_readiness.py`, `tests/test_postgres.py` |
| Assinatura incompleta mais recente substituía uma assinatura válida | Reconciliação prioriza o direito de acesso por status, preço e período | `tests/test_billing.py` |
| Checkout aberto conservava preço antigo após mudança de plano | Itens, preço e quantidade são consultados; sessões incompatíveis expiram antes da abertura de nova cobrança | `tests/test_billing.py` |
| Currículo curto ou perfil incompleto impediam retirar consentimento | Desmarcar a permissão já concedida a revoga imediatamente usando o perfil salvo, sem exigir salvar o rascunho | `frontend/tests/e2e/regressoes.spec.ts` |
| Resposta inicial atrasada ocultava uma busca recém-iniciada | A resposta antiga não sobrescreve o estado mais recente da busca | `frontend/tests/e2e/regressoes.spec.ts` |
| Resposta de alteração de status aplicava um filtro já abandonado | Após confirmar a alteração, a lista é atualizada usando o filtro atual | `frontend/tests/e2e/regressoes.spec.ts` |

A proteção de rede abrange páginas, robots.txt e os redirecionamentos do coletor.
Proxies definidos no ambiente não são usados nesses pedidos. Os testes de rede
usam destinos simulados e um servidor local descartável para provar o bloqueio,
sem consultar serviços internos reais.

## Verificação reproduzível

```sh
python -m pip install uv==0.12.11
uv sync --locked --extra dev
uv run ruff check .
uv run python -m compileall -q triagem api triar.py
uv pip check
uv run pytest -q
cd frontend
npm ci
npm audit --audit-level=high
npm run lint
npm run build
npx playwright install --with-deps chromium
npm run test:e2e
```

O workflow [CI](../.github/workflows/ci.yml) executa Python 3.10, 3.12 e 3.13,
frontend e testes de navegador. A integração PostgreSQL usa exclusivamente
`TRIAGEM_TEST_POSTGRES_URL` e cria schemas descartáveis; os testes são pulados
quando esse ambiente não é fornecido. O workflow de gates do SaaS também valida
PostgreSQL, migrações, imagem Docker, readiness e execução do worker.
Confira os resultados do commit escolhido na [página de Actions](https://github.com/leonardosovienski/automatizacao-curriculo/actions).

## Ativação comercial

Os testes automatizados de cobrança e envio de mensagens não realizam pagamentos
nem enviam emails a clientes. A operação com domínio, PostgreSQL de produção,
Stripe, Gemini e SMTP depende das contas e credenciais do operador. O teste de
pagamento, entrega de email, recuperação de backup e os demais passos de ativação
continuam documentados em [Operação do SaaS](OPERACAO_SAAS.md).
