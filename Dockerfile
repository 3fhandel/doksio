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

RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -e ".[dev]"; else pip install -e "."; fi

EXPOSE 8000
