# Build-Image: enthält Build-from-source (gehört NICHT in den Portainer-Stack).
# Architekturen: python:3.12-slim liefert amd64 und arm64; getestet auf amd64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

# Reihenfolge für besseres Layer-Caching: Metadaten, dann Quellcode.
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data \
    && chown -R app:app /data /app

USER app

EXPOSE 8080

# Healthcheck auch im Image (Compose setzt ihn gleichwohl explizit).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=5).read()"

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8080"]
