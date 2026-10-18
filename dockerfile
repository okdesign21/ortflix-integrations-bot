FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg=7:7.1.5-0+deb13u1 \
    mkvtoolnix=92.0-1 && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY *.py ./
COPY config ./config
COPY integrations ./integrations
COPY scripts ./scripts

# The image has no .git, so setuptools-scm needs the version passed in
ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

# Install build dependencies and package
RUN pip install --no-cache-dir --upgrade pip==24.3.1 setuptools==75.6.0 wheel==0.45.1 && \
    pip install --no-cache-dir .

EXPOSE 7777

ENTRYPOINT ["python", "-m", "bot"]
