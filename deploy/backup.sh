#!/usr/bin/env bash
# Backup do banco (pg_dump) e dos PDFs enviados. O cron roda todo dia (ver
# preparar-servidor.sh); também dá para rodar à mão antes de uma atualização.
#
#   deploy/backup.sh                      # grava em ~/backups
#   DESTINO=/outro/lugar deploy/backup.sh
#
# Backup que fica só nesta VM não protege de perder a VM: puxe as cópias para outro
# lugar (ver docs/deploy.md, "Levar o backup para fora do servidor").
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

DESTINO="${DESTINO:-$HOME/backups}"
RETENCAO_DIAS="${RETENCAO_DIAS:-14}"
quando=$(date -u +%Y%m%dT%H%M%SZ)
dc() { docker compose -f docker-compose.prod.yml "$@"; }
# Atenção: "docker compose exec" lê o stdin. Todo exec sem entrada própria leva
# </dev/null, senão ele engole o resto de quem chamou o script (um heredoc, um pipe).

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"

# 1. Banco. --format=custom: comprimido, e o pg_restore escolhe o que restaurar e em
# que ordem. O pg_dump lê uma FOTO consistente (uma transação REPEATABLE READ): a API
# pode continuar escrevendo durante o backup.
dump="$DESTINO/banco_$quando.dump"
dc exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6 \
  < /dev/null > "$dump.parcial"
# Confere que o arquivo é um dump legível antes de considerá-lo válido
dc exec -T db pg_restore --list < "$dump.parcial" > /dev/null
mv "$dump.parcial" "$dump" # só ganha o nome final se chegou até aqui

# 2. PDFs (o banco guarda só o caminho). Os trechos e embeddings estão no banco; os
# arquivos servem para reprocessar.
docker run --rm --user "$(id -u):$(id -g)" \
  -v estuda-ai-prod_uploads:/dados:ro -v "$DESTINO":/destino \
  alpine:3.20 tar czf "/destino/uploads_$quando.tar.gz" -C /dados . < /dev/null

# 3. Retenção: apaga cópias mais velhas que RETENCAO_DIAS dias
find "$DESTINO" -maxdepth 1 \( -name 'banco_*.dump' -o -name 'uploads_*.tar.gz' \) \
  -mtime +"$RETENCAO_DIAS" -delete

echo "$(date -u +%FT%TZ) backup ok: $(du -h "$dump" | cut -f1) $(basename "$dump")"
