#!/usr/bin/env bash
# Atualiza o servidor para a última versão do main: backup, pull, build, sobe.
#
#   deploy/atualizar.sh
set -euo pipefail
cd "$(dirname "$0")/.."

deploy/backup.sh # uma migration nova mexe no schema: backup ANTES
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
docker image prune -f > /dev/null # imagens antigas ocupam GBs (torch)
docker compose -f docker-compose.prod.yml ps
