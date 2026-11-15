# syntax=docker/dockerfile:1

# ---- build: install avatartrace with face matching and the web UI into a virtual environment
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[face,web]"

# ---- runtime
FROM python:3.12-slim

# OpenCV (a dependency of InsightFace) needs these shared libraries at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 avatartrace
COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER avatartrace
WORKDIR /work

# The face model pack is downloaded to /home/avatartrace/.insightface on first use.
# Mount a volume there to keep it between runs:
#   docker run --rm -v avatartrace-models:/home/avatartrace/.insightface -v "$PWD:/work" avatartrace scan /work/photo.jpg
#
# API keys: pass --env-file .env. Never bake keys into an image.
# Web UI (binds to loopback only), Linux: docker run --rm --network host avatartrace web
ENTRYPOINT ["avatartrace"]
CMD ["--help"]
