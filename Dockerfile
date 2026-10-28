# syntax=docker/dockerfile:1
#
# staan-mcp container image.
#
# The default command serves the stateless streamable-HTTP transport on 0.0.0.0:8000. The server
# refuses to start that way unless you also configure access control (see the README):
#   * an OAuth gate (STAAN_MCP_AUTH_* variables) or the explicit --insecure-no-auth flag, and
#   * at least one allowed Host header (STAAN_MCP_ALLOWED_HOSTS or --allowed-host).
# `docker run <image>` on its own therefore exits with status 2 and an explanation on stderr.
# That is deliberate: the image fails closed instead of exposing your API key's quota.
# For stdio, override the arguments: `docker run -i --rm -e STAAN_API_KEY <image> --transport stdio`.
#
# Base images: literal `name:tag@sha256:<index digest>` on every FROM line. Dependabot (docker
# ecosystem) only recognises that form; a tag built from an ARG (`uv:${UV_VERSION}`) makes it skip
# the line, so do not reintroduce ARGs here. The uv tag is bumped by Dependabot independently of
# the `uv-version` dependency group in pyproject.toml (tests/repo only checks the major version). Both images ship amd64 and arm64.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_DEV=1
WORKDIR /app
# Dependencies first, so source changes do not invalidate this layer. The image includes the
# optional `oauth` extra so the OAuth resource-server gate works out of the box.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable --extra oauth
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra oauth

FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
LABEL org.opencontainers.image.title="staan-mcp" \
      org.opencontainers.image.description="MCP server for the Staan API: web search and grounded answers." \
      org.opencontainers.image.source="https://github.com/MarcinWolnyEU/Staan-MCP" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.MarcinWolnyEU/staan-mcp"
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app
# Root-owned and read-only for the app user: a code-execution bug cannot rewrite the installed
# packages. The bytecode is precompiled in the builder and PYTHONDONTWRITEBYTECODE is set.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
# Only meaningful for the HTTP transports: succeeds when the port accepts TCP connections.
# Disable it with `docker run --no-healthcheck` when you run the stdio transport. It reads the port
# from STAAN_MCP_PORT, so set the port with that variable, not with the `--port` flag.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ.get('STAAN_MCP_PORT', '8000'))), 3).close()"]
ENTRYPOINT ["staan-mcp"]
CMD ["--transport", "stateless", "--host", "0.0.0.0"]
