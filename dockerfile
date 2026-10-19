FROM python:3.14-slim-trixie AS builder

WORKDIR /build

COPY pyproject.toml build-constraints.txt README.md ./
COPY *.py ./
COPY integrations ./integrations

ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

RUN python -m pip install --no-cache-dir --upgrade --constraint build-constraints.txt pip setuptools wheel && \
    python -m pip wheel --no-cache-dir --build-constraint build-constraints.txt --wheel-dir /wheels .

FROM python:3.14-slim-trixie AS runtime

WORKDIR /app

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    ffmpeg=7:7.1.5-0+deb13u1 \
    mkvtoolnix=92.0-1 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels

RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels /wheels/ortflix_integrations-*.whl && \
    python -m pip uninstall --yes pip setuptools wheel && \
    rm -rf /wheels /usr/local/lib/python*/ensurepip

COPY *.py ./
COPY config ./config
COPY integrations ./integrations
COPY scripts ./scripts

EXPOSE 7777

ENTRYPOINT ["python", "-m", "bot"]
