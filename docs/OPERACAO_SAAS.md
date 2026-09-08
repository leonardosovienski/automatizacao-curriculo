# Operação do SaaS

## O que esta versão entrega

Cadastro com aceite versionado, senha Argon2, sessão revogável em cookie HttpOnly,
recuperação por SMTP, perfil individual, currículo, consentimento de IA, busca e
ranking, acompanhamento da candidatura, geração de material com evidências,
assinatura Stripe, cotas no servidor, exportação e exclusão de conta.

O Docker entrega interface e API na mesma origem. As tarefas ficam no PostgreSQL;
cada execução roda em processo próprio, sem compartilhar perfil, cache ou replay.
Uma atualização não perde as tarefas pendentes. Se o processo morrer durante uma
tarefa, ela será marcada como falha ao vencer o prazo (30 minutos por padrão);
os resultados já persistidos permanecem disponíveis. Não há repetição automática
de chamadas pagas após uma interrupção.

**Código validado localmente não equivale a operação comercial validada.** Antes da
primeira venda, conclua a seção de ativação usando suas contas reais. Esta versão
não fornece domínio, credenciais, contrato com fornecedores ou assessoria jurídica.

## Desenvolvimento reproduzível

Requisitos: Python 3.10+ e Node 22. O ambiente de produção usa Python 3.13.

```sh
python -m pip install uv==0.12.11
uv sync --locked --extra dev
# Copie .env.example para .env e preencha TRIAGEM_JWT_SECRET.
uv run alembic upgrade head
uv run uvicorn api.app:app --reload
```

Em outro terminal, dentro de `frontend`: `npm ci` e `npm run dev`. O proxy Vite
encaminha `/api` e `/billing` para a API. Para testar a entrega em uma só origem,
execute `npm run build` e abra a API na porta 8000. Não coloque chaves de Stripe,
Gemini ou SMTP em variáveis `VITE_*`.

`uv.lock` congela dependências Python para todas as plataformas suportadas.
`frontend/package-lock.json` congela as dependências da interface.

## Ativação comercial

1. Defina nome comercial, responsável pelo serviço, e-mail de suporte, domínio,
   política de reembolso e preço. Os documentos `TERMOS_DE_USO.md` e
   `PRIVACIDADE.md` são minutas: preencha a identidade do operador, fornecedores,
   retenção e canais de atendimento e obtenha a revisão aplicável ao seu negócio.
   Publique as versões finais e configure suas URLs no ambiente.
2. Crie PostgreSQL gerenciado com backups automáticos e teste uma restauração em
   um banco separado. Use TLS no acesso ao banco remoto e acesso restrito à rede
   da aplicação. Registre quem terá acesso a currículos e backups.
3. Configure o projeto Gemini com faturamento e limites de orçamento/uso. Os
   termos de dados dos serviços pagos diferem dos gratuitos; não use a camada
   gratuita para currículos de clientes sem resolver essa condição. Consulte os
   [termos do Gemini](https://ai.google.dev/gemini-api/terms#data-use-paid).
   Confirme também que as fontes de vagas permitem seu uso comercial.
4. No Stripe, crie um produto e um preço recorrente. Configure o Customer Portal
   para atualizar pagamento e cancelar renovação. Os valores exibidos no produto
   vêm desse preço; a aplicação não inventa nem fixa um valor em reais.
5. Cadastre um webhook em `https://SEU_DOMINIO/billing/webhook` com os eventos:
   `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `customer.subscription.paused`,
   `customer.subscription.resumed`, `customer.deleted`,
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `invoice.paid` e
   `invoice.payment_failed`. Copie o segredo deste endpoint para
   `STRIPE_WEBHOOK_SECRET`. A chave, preço e webhook devem pertencer ao mesmo modo
   e conta Stripe. Veja a [documentação de webhooks](https://docs.stripe.com/webhooks).
6. Configure SMTP com TLS, domínio remetente autenticado (SPF/DKIM conforme seu
   provedor) e teste a entrega do e-mail de recuperação em uma caixa real.
7. Configure todas as variáveis de `.env.example`. Em produção:
   `TRIAGEM_ENV=production`, `TRIAGEM_PUBLIC_URL=https://SEU_DOMINIO` e chaves live.
   Gere o segredo da sessão com `secrets.token_urlsafe(48)`. A validação de
   inicialização rejeita configuração estrutural incompleta. Ela não comprova
   validade de credenciais nem entrega de e-mails.
8. Faça o roteiro abaixo primeiro em **staging com Stripe de teste**. Depois,
   confirme uma compra real de valor definido por você, fatura, cancelamento e
   eventual reembolso pelo painel Stripe antes de anunciar o serviço.

## Implantação recomendada

O repositório inclui `Dockerfile` e `railway.json`. Em Railway, conecte o repositório,
adicione PostgreSQL, associe `DATABASE_URL`, preencha as variáveis e o domínio com
HTTPS. O container roda migrações, valida o ambiente e inicia a aplicação com
worker embutido. Use **uma réplica da aplicação inicialmente**. A migração ocorre
no startup; antes de usar várias réplicas, execute `alembic upgrade head` em uma
etapa única de pré-deploy e inicie as réplicas diretamente com Uvicorn.

Os workflows de deploy são manuais, na branch `main`, usando o environment
`production`. O workflow Railway precisa de `RAILWAY_TOKEN` (secret) e
`RAILWAY_PROJECT_ID` / `RAILWAY_SERVICE_ID` (variables). Configure a política de
aprovação desse environment e só promova commits com os gates de CI aprovados.
O CLI usa o [fluxo não interativo oficial](https://docs.railway.com/cli/deploying).

O `docker-compose.yml` serve para ambiente controlado local/VPS. Ele exige
`TRIAGEM_POSTGRES_PASSWORD` URL-safe, mantém o banco privado e publica a aplicação
somente em `127.0.0.1:8000`. Coloque um proxy HTTPS à frente antes de expor a um
domínio. Não publique o PostgreSQL na internet.

Para workers separados, configure `TRIAGEM_WORKER_MODE=external` na API e rode
`python -m api.worker` em outro serviço da mesma imagem, com o mesmo banco e
variáveis. Escale por réplicas de worker: a reserva no PostgreSQL impede dois
consumidores de executarem a mesma tarefa. Cada réplica processa uma tarefa por
vez. Limite réplicas e orçamento conforme custo e latência medidos.

Se optar pelo workflow Vercel, use domínio como `app.seudominio.com` e API como
`api.seudominio.com`, com HTTPS em ambos, `VITE_API_URL` e CORS explícitos.
Domínios padrão `vercel.app` e `railway.app` são sites distintos e podem bloquear
o cookie SameSite. A imagem integrada dispensa essa configuração.

`FORWARDED_ALLOW_IPS` deve conter apenas os proxies confiáveis da hospedagem.
Valide que o IP registrado no ASGI corresponde ao cliente antes de vender: um
proxy não reconhecido pode agrupar todos os clientes na mesma cota de login.
Jamais confie em cabeçalhos encaminhados de conexões públicas diretas.

## Roteiro de aceite em staging

- Abrir `/ready`, página inicial e assets pela URL final com HTTPS.
- Criar duas contas com e-mails distintos, ler documentos e concluir onboarding.
- Confirmar que cada conta só lista, altera, exporta e exclui seus próprios dados.
- Recuperar a senha por e-mail; reutilizar o link deve falhar; as sessões antigas
  devem perder acesso. Sair deve invalidar também uma cópia do token da sessão.
- Com a conta sem assinatura, confirmar que a busca/material não são liberados.
- Pagar no Stripe de teste e voltar para a aplicação: a confirmação depende do
  servidor, e alterar `?sucesso=1` manualmente não libera acesso.
- Reenviar um webhook e enviá-lo fora da ordem: a assinatura continua coerente
  com o estado atual no Stripe. Conferir entrega 2xx no painel Stripe.
- Fazer uma busca pequena real com o CV de teste. Confirmar qualidade das vagas,
  links, desempenho, consumo no provedor e geração de material com evidências.
- Usar um bloco `<!-- PRIVADO -->segredo de teste<!-- /PRIVADO -->` e confirmar
  pelos testes de transporte que ele não é enviado; marcador incompleto é recusado.
- Renovar, simular pagamento falho, cancelar e confirmar que o acesso respeita o
  status e fim do período. Conferir cotas no servidor e tentativa concorrente.
- Iniciar busca, reiniciar o serviço, verificar tarefa pendente recuperada ou
  tarefa interrompida reportada após o prazo; nenhuma fica travada para sempre.
- Excluir conta com assinatura: o cliente Stripe e assinaturas são encerrados
  antes da remoção dos dados locais. Se Stripe falhar, a conta fica preservada
  para uma nova tentativa. Obrigações fiscais/reembolsos devem ser tratados pelo
  operador no provedor; exclusão não gera reembolso automático.
- Restaurar backup em banco isolado e medir o tempo de recuperação.

## Cotas e custo

Padrão: 30 buscas e 300 unidades de análise por mês civil UTC. Uma busca reserva
o limite de vagas pedido, entre 1 e 20. Cada geração de material reserva 2 unidades
de análise e não consome uma busca. A reserva acontece na mesma transação que
cria a tarefa. Processamentos parciais/falhos não devolvem a cota automaticamente,
pois podem já ter consumido chamadas ao provedor; isso é informado na interface.

Os limites são globais para o único plano (`TRIAGEM_MONTHLY_SEARCH_LIMIT` e
`TRIAGEM_MONTHLY_ANALYSIS_LIMIT`). Não são uma garantia de custo máximo em reais:
busca, tokens, retentativas e número de réplicas também afetam o custo. Configure
limites no provedor e escolha preço somente depois de medir uso real.

## Saúde, backups e incidentes

`/health` indica processo vivo; `/ready` verifica acesso ao banco e colunas da fila.
Um monitor externo deve alertar por indisponibilidade HTTP, falhas de webhook,
quedas da taxa de conclusão, tarefas vencidas e orçamento dos provedores. Logs de
erro usam classe da exceção e ID da tarefa, sem currículo ou resposta bruta de IA.
Não habilite logging de corpos HTTP, prompts, query de recuperação ou cookies no proxy.

```sql
SELECT estado, count(*) FROM buscas GROUP BY estado;
SELECT id, criada_em, iniciada_em, expira_em
FROM buscas WHERE estado IN ('pendente', 'processando') ORDER BY criada_em;
```

Use backups gerenciados criptografados e retenção definida na sua política.
Antes de cada migração, tire snapshot. Para rollback, prefira restaurar o snapshot
em banco separado e apontar a versão compatível após validação. Downgrade de esquema
remove os dados dos recursos novos: não execute em produção como rotina. Para
restauração de usuários excluídos, reaplique o registro de exclusões antes de reabrir
acesso; backups antigos não devem ressuscitar dados que já foram removidos.

As migrações novas invalidam sessões antigas, marcam trabalhos legados incompletos
como falha e rejeitam clientes/assinaturas Stripe duplicados. Resolva duplicidades
reais com reconciliação antes de migrar; não apague registros de cobrança às cegas.

## Verificação automatizada

```sh
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run alembic upgrade head
uv run alembic check
cd frontend
npm ci
npm audit --audit-level=high
npm run lint
npm run build
npm run test:e2e
```

O workflow `saas-release-gates.yml` executa migrations/locks em PostgreSQL 16 e
build/smoke da imagem Docker. Os testes locais de Stripe, Gemini e SMTP usam
simulações controladas: não substituem o roteiro com as contas reais.
