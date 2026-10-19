FROM python:3.14-slim-trixie AS builder

WORKDIR /build

COPY pyproject.toml build-constraints.txt README.md ./
COPY *.py ./
COPY integrations ./integrations

ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

RUN python -m pip install --no-cache-dir --upgrade --constraint build-constraints.txt pip setuptools wheel && \
    python -m pip wheel --no-cache-dir --build-constraint build-constraints.txt --wheel-dir /wheels .

FROM python:3.14-slim-trixie AS media-builder

WORKDIR /build/ffmpeg

ARG FFMPEG_VERSION=9.0.2

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential=* ca-certificates=* curl=* gnupg=* nasm=* pkg-config=* xz-utils=* zlib1g-dev=* && \
    rm -rf /var/lib/apt/lists/*

COPY docker/build_ffmpeg.sh /build/build_ffmpeg.sh
RUN sh /build/build_ffmpeg.sh "$FFMPEG_VERSION"

FROM python:3.14-slim-trixie AS runtime

WORKDIR /app

COPY --from=media-builder /ffmpeg.deb /tmp/ffmpeg.deb

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    mkvtoolnix=92.0-1 /tmp/ffmpeg.deb && \
    dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n' > /tmp/runtime-packages && \
    if grep -E '^(libxml2[^ ]*|libav(codec|device|filter|format|util)[^ ]*|libsw(resample|scale)[^ ]*) installed$' /tmp/runtime-packages; then \
        echo 'Unexpected Debian XML/media runtime dependency' >&2; exit 1; \
    fi && \
    rm /tmp/runtime-packages /tmp/ffmpeg.deb && \
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
