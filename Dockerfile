# catalog-api: REST API + `catalog` CLI (migrations, imports, undo, merges).
# Build through `make build`: it runs lint, type-check and tests before building images.

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes -r /tmp/requirements.lock

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin catalog
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY src ./src
USER catalog
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"]
ENTRYPOINT ["python", "-m", "catalog.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
