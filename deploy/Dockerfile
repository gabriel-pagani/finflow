# STAGE 1
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

COPY ../requirements.txt .

RUN apt-get update && apt-get install -y build-essential gcc python3-dev libpq-dev --no-install-recommends

RUN pip install --upgrade pip && pip install --prefix=/install -r requirements.txt

# STAGE 2
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y libpq5 --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN groupadd --gid 1000 django && useradd --uid 1000 --gid django --create-home django

COPY --chown=django:django ../ .

RUN mkdir -p /app/static_root /app/media_root && chown django:django /app/static_root /app/media_root

USER django

EXPOSE 8000