# Estado do projeto

CLI Python para triagem de vagas, histórico de candidaturas, exportação e geração de
material de CV. Também busca vagas atuais na web a partir de um pedido em linguagem
natural e do CV base. A integração de IA usa a API Google Gemini.

## Integração atual

- Provedor: Google Gemini pelo pacote `google-genai`.
- Modelo padrão: `gemini-3.1-flash-lite` (`--modelo lite`).
- Alternativa: `gemini-3.5-flash` (`--modelo flash`).
- Chave: variável `GEMINI_API_KEY`, criada em <https://aistudio.google.com/apikey>.
- A triagem usa saída JSON estruturada validada pelo schema Pydantic.
- Há paralelismo e uma segunda tentativa sequencial para falhas individuais.
- `triar buscar` combina Jooble, Adzuna, Google Search Grounding e metabusca DDGS como fallback.
- A busca faz sobrecoleta, pré-ranking, deduplicação semântica e validação de URL e
  disponibilidade geográfica antes de enviar as vagas para a triagem.
- Portais internacionais só são aceitos quando o anúncio explicita Brasil, LATAM ou
  contratação global; links encerrados ou redirecionados para buscas genéricas são removidos.
- A antiga integração Anthropic e a Batch API foram removidas.

## Privacidade

Segundo a página de preços do Gemini, conteúdo enviado pela camada gratuita pode ser usado
para melhorar os produtos do Google. O comando `cv` envia o CV base e o texto da vaga.
Não use dados pessoais sensíveis que você não queira enviar ao provedor. Para tratamento
sem uso dos dados para melhoria do produto, consulte as condições do plano pago.

## Validação

Execute:

```powershell
python -m pytest
python -m compileall -q triagem triar.py
python -m pip check
```

O teste real da API depende de uma `GEMINI_API_KEY` válida.
