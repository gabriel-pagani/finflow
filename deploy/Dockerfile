# STAGE 1
FROM python:3.15-slim-trixie AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential gcc python3-dev libpq-dev libsasl2-dev libldap-dev --no-install-recommends

COPY ../requirements.txt .

RUN pip install --upgrade pip && pip install --prefix=/install -r requirements.txt

# STAGE 2
FROM python:3.15-slim-trixie AS production

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y libpq5 libsasl2-2 libldap2 --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Agendador do serviço scheduler (deploy/crontab).
# ponytail: versão e checksum fixos à mão e só amd64; o Dependabot não vê o ADD.
ADD --chmod=755 --checksum=sha256:cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123 \
    https://github.com/aptible/supercronic/releases/download/v0.2.51/supercronic-linux-amd64 /usr/local/bin/supercronic

RUN groupadd --gid 1000 django && useradd --uid 1000 --gid django --create-home django

# O código fica do root: o processo lê, mas não reescreve o próprio código.
COPY ../ .

RUN mkdir -p /app/static_root /app/media_root && chown django:django /app/static_root /app/media_root

USER django

EXPOSE 8000

# STAGE 3
FROM production AS test

USER root

RUN pip install --no-cache-dir -r test-requirements.txt && chown django:django /app

USER django
