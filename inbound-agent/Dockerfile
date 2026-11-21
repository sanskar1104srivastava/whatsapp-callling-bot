ARG PYTHON_VERSION=3.11
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim

ENV PYTHONUNBUFFERED=1

ARG UID=10001
RUN adduser --disabled-password --gecos "" --home "/app" --shell "/sbin/nologin" --uid "${UID}" appuser

RUN apt-get update && apt-get install -y gcc g++ python3-dev libsndfile1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY src ./src
RUN chown -R appuser:appuser /app
USER appuser

# pre-fetch plugin model files so the first call is not slow
RUN uv run src/inbound_agent.py download-files

CMD ["uv", "run", "src/inbound_agent.py", "start"]
