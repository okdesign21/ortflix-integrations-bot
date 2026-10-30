FROM opensuse/tumbleweed@sha256:d95ddcc0ae62a9df3e6e424cb39a00ba74ef1744b02d7914bdbec4c7657baf01 AS base

ENV PATH=/usr/local/bin:$PATH

RUN zypper --non-interactive install --no-recommends \
        python314-base=3.14.8-2.1 ffmpeg-8=8.1.3-3.3 mkvtoolnix=103.0-1.1 \
        libxml2-16=2.15.4-1.1 libQt6Core6=6.12.0-1.1 util-linux=2.42.4-2.1 \
        libsystemd0=261.4-1.1 libncurses6=6.6.20261017-116.1 libacl1=2.4.0-1.2 \
        ca-certificates=2+git20260727.241e0ff-1.1 && \
    zypper clean --all && \
    mkdir -p /usr/local/bin && \
    ln -s /usr/bin/python3.14 /usr/local/bin/python && \
    rm -rf /usr/lib/python*/ensurepip /usr/lib64/python*/ensurepip

FROM base AS builder

WORKDIR /build

COPY pyproject.toml build-constraints.txt README.md ./
COPY *.py ./
COPY integrations ./integrations

ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

RUN zypper --non-interactive install --no-recommends python314-pip=26.2.2-1.1 && \
    zypper clean --all && \
    python -m venv --without-pip /build-env && \
    python -m pip --python /build-env install --no-cache-dir --upgrade \
        --constraint build-constraints.txt pip setuptools wheel && \
    /build-env/bin/python -m pip wheel --no-cache-dir \
        --build-constraint build-constraints.txt --wheel-dir /wheels . && \
    /build-env/bin/python -m pip install --no-cache-dir \
        --no-index --find-links=/wheels --prefix=/install /wheels/ortflix_integrations-*.whl

FROM base AS runtime

WORKDIR /app

COPY --from=builder /install/ /usr/

COPY *.py ./
COPY config ./config
COPY integrations ./integrations
COPY scripts ./scripts

EXPOSE 7777

ENTRYPOINT ["python", "-m", "bot"]
