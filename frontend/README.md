# Aplicação web

Interface React em português para cadastro, recuperação de acesso, perfil e currículo, busca de vagas, geração de material de candidatura, cobrança e gestão da conta.

## Desenvolvimento

Requisitos: Node.js 22.12+ (ou 24) e API Python disponível na porta 8000.

```sh
npm ci
npm run dev
```

O Vite encaminha `/api` e `/billing` à API local. Em produção, o Docker da raiz compila e serve a interface na mesma origem da API; deixe `VITE_API_URL` vazio. Uma URL externa só deve ser definida quando cookies e origens autorizadas estiverem corretamente configurados.

## Verificação

```sh
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

A suíte abre sua própria API com banco isolado e a interface, em desktop e celular. Informe `E2E_PYTHON` com o caminho do Python que contém as dependências do projeto. No Windows, por exemplo, use o caminho absoluto de `.venv\Scripts\python.exe` da raiz. Não use um banco de produção em `TRIAGEM_DATABASE` para testes: o seed recria esse banco.

Os testes reais verificam autenticação, isolamento inicial de contas, filtros, ordenação e persistência dos status. Os cenários de Stripe, e-mail, busca com IA e geração de material usam respostas controladas para verificar os estados da interface sem cobranças nem chamadas externas. Eles não substituem a homologação das integrações em staging.

## Contratos da interface

- `GET /api/config/publico`: nome do serviço, contato de suporte e URLs públicas dos documentos legais. A aplicação aguarda essa configuração antes de mostrar o cadastro. Os documentos oficiais devem ser publicados e configurados pelo operador.
- Recuperação: o link `/redefinir-senha#token=...` entrega o token pelo fragmento; a interface o remove do endereço e envia somente no corpo de `POST /api/auth/redefinir-senha`.
- Checkout: `/?sucesso=1` chama `/billing/sincronizar` e verifica o estado no servidor antes de anunciar ativação; `/?cancelado=1` explica que o checkout foi encerrado. Preços e limites vêm da API.
- Conta: exportação JSON autenticada e exclusão mediante senha e confirmação explícita; nenhuma senha ou sessão fica em localStorage.
- Material de candidatura: geração assíncrona, acompanhamento e download Markdown para revisão; a interface não envia candidaturas automaticamente.

Consulte a documentação da raiz para publicação, migrações, backup, configuração de e-mail e Stripe e critérios de lançamento.
