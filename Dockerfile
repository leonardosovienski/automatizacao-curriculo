FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN groupadd --system triagem && useradd --system --gid triagem --home /app triagem
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY triagem ./triagem
COPY api ./api
RUN pip install --no-cache-dir --upgrade pip==26.2.1 uv==0.12.11 \
    && uv sync --locked --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=frontend /build/dist ./frontend/dist
COPY deploy/start.sh ./deploy/start.sh
RUN chmod +x ./deploy/start.sh && mkdir /data && chown triagem:triagem /data
ENV TRIAGEM_DATABASE=/data/triagem.db
USER triagem
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/ready',timeout=4)"
CMD ["./deploy/start.sh"]
