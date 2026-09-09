# Dockerfile
#
# Shared image for both the mcp-server and scheduler services -- they run
# the same codebase/dependencies, just with a different CMD (see
# docker-compose.yml). Build once, run twice.

FROM python:3.12-slim

WORKDIR /app

# curl: used by the mcp-server container healthcheck.
# postgresql-client: provides pg_dump, used by scheduler.py's nightly
# backup job (see BACKUP_KEEP_DAYS in docker-compose.yml).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Default: run the MCP server. docker-compose overrides this command for
# the scheduler service.
CMD ["uv", "run", "server.py"]
