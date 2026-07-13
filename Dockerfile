FROM python:3.14-slim AS build-metadata

WORKDIR /build
ARG DOKSIO_BUILD_VERSION=

RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

COPY . ./

RUN if [ -n "$DOKSIO_BUILD_VERSION" ]; then \
        printf "%s" "$DOKSIO_BUILD_VERSION" > .doksio-build-version; \
    elif [ -d .git ]; then \
        git log -1 --format=%cd --date=format:%Y%m%d-%H%M > .doksio-build-version; \
    else \
        : > .doksio-build-version; \
    fi

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
ARG INSTALL_DEV=false

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        libpq-dev \
        ocrmypdf \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY manage.py ./
COPY --from=build-metadata /build/.doksio-build-version ./.doksio-build-version

RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -e ".[dev]"; else pip install -e "."; fi

EXPOSE 8000
