#!/usr/bin/env bash
# Restaura um backup feito por deploy/backup.sh. SUBSTITUI o banco atual.
#
#   deploy/restaurar.sh ~/backups/banco_20261008T040000Z.dump [~/backups/uploads_...tar.gz]
#
# Funciona num servidor novo (depois do primeiro "up") e no mesmo servidor.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

dump="${1:?uso: deploy/restaurar.sh banco_XXXX.dump [uploads_XXXX.tar.gz]}"
uploads="${2:-}"
dc() { docker compose -f docker-compose.prod.yml "$@"; }

echo "Isto SUBSTITUI o banco '$POSTGRES_DB' pelo conteúdo de $(basename "$dump")."
read -r -p "Digite RESTAURAR para continuar: " confirmacao
[[ "$confirmacao" == "RESTAURAR" ]] || { echo "Cancelado."; exit 1; }

# Ninguém escrevendo durante o restore
dc stop frontend backend < /dev/null
dc up -d db < /dev/null

# Papéis são do SERVIDOR, não do banco: o pg_dump não os leva. Num servidor novo o
# papel da aplicação ainda não existe e os GRANTs do dump falhariam.
dc exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
  "DO \$\$ BEGIN
     IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'estuda_ai_app') THEN
       CREATE ROLE estuda_ai_app NOLOGIN;
     END IF;
   END \$\$;" < /dev/null

# --clean --if-exists: apaga o que existe antes de recriar.
# --single-transaction: tudo ou nada (se falhar no meio, o banco antigo continua lá).
dc exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --clean --if-exists --single-transaction --exit-on-error < "$dump"

if [[ -n "$uploads" ]]; then
  docker run --rm --user 10001:10001 -v estuda-ai-prod_uploads:/dados \
    -v "$(cd "$(dirname "$uploads")" && pwd)":/origem:ro \
    alpine:3.20 sh -c "rm -rf /dados/* && tar xzf /origem/$(basename "$uploads") -C /dados" < /dev/null
fi

# O backend, ao subir, aplica migrations que faltarem e dá a senha do .env ao papel
dc up -d < /dev/null
echo "Restaurado. Confira: docker compose -f docker-compose.prod.yml logs backend --tail 20"
