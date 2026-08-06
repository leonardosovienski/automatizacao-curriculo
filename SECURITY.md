# Security Policy

## Supported Versions

Este projeto é mantido na branch `main`.

| Versão | Suporte de segurança |
|---|---|
| `main` | ✅ |
| versões antigas | ❌ |

## Reporting a Vulnerability

Não abra uma issue pública para relatar:

- credenciais ou tokens expostos;
- falhas que permitam acesso indevido a dados;
- vazamento de informações pessoais;
- execução de código não autorizado;
- bypass dos filtros de privacidade;
- vulnerabilidades em integrações externas;
- problemas que possam afetar usuários do projeto.

Use o recurso **Private vulnerability reporting** disponível na aba **Security** deste repositório.

Inclua, quando possível:

- descrição da vulnerabilidade;
- arquivo ou componente afetado;
- passos para reproduzir;
- impacto esperado;
- evidências sanitizadas;
- sugestão de correção, caso exista.

Não envie chaves reais, tokens válidos, documentos pessoais ou currículos completos como evidência.

## Response Process

Após o recebimento do relato:

1. o problema será analisado;
2. sua severidade e impacto serão avaliados;
3. uma correção será preparada, quando necessária;
4. credenciais comprometidas deverão ser revogadas ou rotacionadas;
5. a divulgação pública ocorrerá somente depois da correção, quando aplicável.

Não há garantia de prazo fixo de resposta, pois este é um projeto pessoal mantido individualmente.

## Scope

São considerados dentro do escopo:

- código Python;
- processamento e armazenamento local de vagas;
- integração com APIs externas;
- geração de currículos e mensagens;
- proteção de dados pessoais;
- sanitização de conteúdo enviado a modelos externos;
- cache, histórico e arquivos gerados;
- workflows do GitHub Actions;
- dependências do projeto.

Não estão dentro do escopo:

- vulnerabilidades exclusivamente em serviços externos;
- indisponibilidade de APIs de terceiros;
- bloqueios ou limitações impostos por mecanismos anti-bot;
- engenharia social;
- problemas já corrigidos na branch `main`;
- uso do projeto fora das instruções documentadas.

## Secrets and Personal Data

Nunca faça commit de:

- `.env`;
- chaves de API;
- tokens;
- currículos reais;
- CPF, RG, telefone ou endereço;
- histórico local de candidaturas;
- caches contendo dados pessoais.

Caso um segredo seja exposto, removê-lo do código não é suficiente: ele deve ser revogado ou rotacionado no provedor correspondente.
