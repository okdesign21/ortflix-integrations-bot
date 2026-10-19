FROM python:3.14-slim-trixie

WORKDIR /app

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    ffmpeg=7:7.1.5-0+deb13u1 \
    mkvtoolnix=92.0-1 && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml build-constraints.txt README.md ./
COPY *.py ./
COPY config ./config
COPY integrations ./integrations
COPY scripts ./scripts

# The image has no .git, so setuptools-scm needs the version passed in
ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

# Install build dependencies and package
RUN python -m pip install --no-cache-dir --upgrade --constraint build-constraints.txt pip setuptools wheel && \
    python -m pip install --no-cache-dir --build-constraint build-constraints.txt .

EXPOSE 7777

ENTRYPOINT ["python", "-m", "bot"]
