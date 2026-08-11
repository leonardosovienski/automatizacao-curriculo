FROM python:3.13-slim AS base

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
COPY triagem ./triagem
COPY api ./api
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD alembic upgrade head && uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}
