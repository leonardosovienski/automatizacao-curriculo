"""Configuração pública e validação explícita antes de abrir o serviço em produção."""

import os
from urllib.parse import urlsplit


def producao() -> bool:
    return os.environ.get("TRIAGEM_ENV", "development").lower() == "production"


def configuracao_publica() -> dict:
    return {
        "nome_servico": os.environ.get("TRIAGEM_SERVICE_NAME", "Triagem de Vagas"),
        "suporte_email": os.environ.get("TRIAGEM_SUPPORT_EMAIL") or None,
        "termos_url": os.environ.get("TRIAGEM_TERMS_URL") or None,
        "privacidade_url": os.environ.get("TRIAGEM_PRIVACY_URL") or None,
    }


def problemas_configuracao() -> list[str]:
    problemas = []
    modo = os.environ.get("TRIAGEM_ENV", "development")
    if modo not in {"development", "staging", "production"}:
        problemas.append("TRIAGEM_ENV deve ser development, staging ou production")
    for nome, minimo, maximo, padrao in (
        ("TRIAGEM_JOB_TIMEOUT_SECONDS", 60, 7200, "1800"),
        ("TRIAGEM_TOKEN_MINUTES", 5, 1440, "60"),
        ("TRIAGEM_MONTHLY_SEARCH_LIMIT", 1, 10000, "30"),
        ("TRIAGEM_MONTHLY_ANALYSIS_LIMIT", 1, 100000, "300"),
        ("TRIAGEM_SMTP_PORT", 1, 65535, "587"),
    ):
        try:
            if not minimo <= int(os.environ.get(nome, padrao)) <= maximo:
                raise ValueError
        except ValueError:
            problemas.append(f"{nome} deve estar entre {minimo} e {maximo}")
    if os.environ.get("TRIAGEM_WORKER_MODE", "embedded") not in {"embedded", "external", "disabled"}:
        problemas.append("TRIAGEM_WORKER_MODE inválido")
    if not producao():
        return problemas
    obrigatorias = (
        "DATABASE_URL", "TRIAGEM_JWT_SECRET", "TRIAGEM_PUBLIC_URL",
        "GEMINI_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID",
        "TRIAGEM_SUPPORT_EMAIL", "TRIAGEM_TERMS_URL", "TRIAGEM_PRIVACY_URL",
        "TRIAGEM_SMTP_HOST", "TRIAGEM_SMTP_FROM",
    )
    for nome in obrigatorias:
        valor = os.environ.get(nome, "").strip()
        if not valor or valor == "..." or "exemplo" in valor or "example" in valor:
            problemas.append(f"{nome} precisa de um valor de produção")
    if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        problemas.append("DATABASE_URL deve apontar para PostgreSQL em produção")
    segredo = os.environ.get("TRIAGEM_JWT_SECRET", "")
    if len(segredo) < 48 or len(set(segredo)) < 16:
        problemas.append("TRIAGEM_JWT_SECRET precisa ser aleatório, com pelo menos 48 caracteres")
    for nome in ("TRIAGEM_PUBLIC_URL", "TRIAGEM_TERMS_URL", "TRIAGEM_PRIVACY_URL"):
        url = urlsplit(os.environ.get(nome, ""))
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.hostname in {"localhost", "127.0.0.1"}):
            problemas.append(f"{nome} deve ser uma URL HTTPS pública")
        if nome == "TRIAGEM_PUBLIC_URL" and (url.path not in {"", "/"} or url.query or url.fragment):
            problemas.append("TRIAGEM_PUBLIC_URL deve conter somente a origem, sem caminho, query ou fragmento")
    if not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_live_"):
        problemas.append("STRIPE_SECRET_KEY deve usar o modo live em produção; use staging para testes")
    if os.environ.get("TRIAGEM_WORKER_MODE") == "disabled":
        problemas.append("O worker não pode estar desabilitado em produção")
    if os.environ.get("TRIAGEM_SMTP_SECURITY", "starttls") not in {"starttls", "ssl"}:
        problemas.append("SMTP exige TLS em produção")
    for origem in os.environ.get("TRIAGEM_CORS_ORIGINS", "").split(","):
        if origem.strip() and (origem.strip() == "*" or urlsplit(origem.strip()).scheme != "https"):
            problemas.append("TRIAGEM_CORS_ORIGINS deve conter somente origens HTTPS explícitas")
    return problemas


def validar_configuracao() -> None:
    problemas = problemas_configuracao()
    if problemas:
        raise RuntimeError("Configuração incompleta: " + "; ".join(problemas))


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    erros = problemas_configuracao()
    for erro in erros:
        print(f"ERRO: {erro}")
    if not erros:
        print("Configuração estrutural válida. Valide credenciais e fluxos reais antes da venda.")
    raise SystemExit(bool(erros))
