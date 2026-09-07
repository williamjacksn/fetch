FROM ghcr.io/astral-sh/uv:0.12.7-trixie-slim

RUN /usr/sbin/useradd --create-home --shell /bin/bash --user-group python
USER python

WORKDIR /app
COPY --chown=python:python .python-version pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1" \
    TZ="Etc/UTC"

LABEL org.opencontainers.image.authors="William Jackson <william@subtlecoolness.com>" \
      org.opencontainers.image.description="Fetch and tag music" \
      org.opencontainers.image.source="https://github.com/williamjacksn/fetch" \
      org.opencontainers.image.title="Fetch"

COPY --chown=python:python README.md ./
COPY --chown=python:python src/fetch ./src/fetch
RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "--no-sync", "fetch"]
