FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS dependencies

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app/backend
# Build both XML bindings against the same system libxml2 for SAML imports.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config zlib1g-dev libxml2-dev libxslt1-dev libxmlsec1-dev libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project \
    --no-binary-package lxml --no-binary-package xmlsec
RUN uv run --no-sync python -c \
    "import lxml.etree, xmlsec; from onelogin.saml2.auth import OneLogin_Saml2_Auth; assert lxml.etree.LIBXML_VERSION == xmlsec.get_libxml_version()"

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG REVISION=uncommitted
ARG SOURCE_ARCHIVE_SHA256=uncommitted
LABEL org.opencontainers.image.source="https://github.com/Perfecto23/sample-agentcore-launchpad" \
    org.opencontainers.image.revision=$REVISION \
    io.launchpad.source-archive-sha256=$SOURCE_ARCHIVE_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git libxml2 libxslt1.1 libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 launchpad \
    && useradd --uid 10001 --gid 10001 --create-home launchpad \
    && install -d -o 10001 -g 10001 -m 0700 /app/data /app/config
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=dependencies /opt/venv /opt/venv
COPY backend /app/backend
COPY vendor /app/vendor
COPY samples /app/samples
COPY scripts /app/scripts
ENV PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/app/data/cache/uv XDG_CACHE_HOME=/app/data/cache \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LAUNCHPAD_RUN_MODE=prod LAUNCHPAD_ALLOW_OPEN_CONSOLE=false \
    LAUNCHPAD_AUTH_REGISTRATION_ENABLED=false LAUNCHPAD_AUTH_COOKIE_SECURE=true \
    LAUNCHPAD_STUDIO_LOCAL_EXEC_ENABLED=false LAUNCHPAD_STUDIO_EXEC_BACKEND=subprocess \
    LAUNCHPAD_UPDATE_ENABLED=false AWS_EC2_METADATA_DISABLED=true
WORKDIR /app/backend
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD uv run --no-sync python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
