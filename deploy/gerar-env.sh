#!/usr/bin/env bash
# Gera o .env de PRODUÇÃO no próprio servidor, com segredos aleatórios.
#
#   deploy/gerar-env.sh seu-nome.duckdns.org
#
# Os segredos nascem AQUI, no servidor (openssl rand), e só existem neste arquivo:
# não passam por chat, e-mail nem git. O .env fica com permissão 600 (só o dono lê).
# Não sobrescreve um .env existente: os segredos dele já estão em uso (o banco foi
# criado com aquela senha; trocar o JWT_SECRET derruba todos os logins).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -e .env ]]; then
  echo "Já existe um .env aqui; não vou sobrescrever." >&2
  exit 1
fi
DOMINIO="${1:?uso: deploy/gerar-env.sh seu-nome.duckdns.org}"

umask 077 # arquivos criados daqui em diante: só o dono lê e escreve
segredo() { openssl rand -hex 32; }
SENHA_DONO=$(segredo)
SENHA_APP=$(segredo)

cat > .env <<EOF
# PRODUÇÃO. Gerado por deploy/gerar-env.sh em $(date -u +%Y-%m-%dT%H:%MZ). Fora do git.
DOMINIO=${DOMINIO}

# Postgres: o dono do schema (migrations, backup)
POSTGRES_USER=estuda_ai
POSTGRES_PASSWORD=${SENHA_DONO}
POSTGRES_DB=estuda_ai

# A API conecta com o papel de menor privilégio; as migrations, com o dono
DATABASE_URL=postgresql+psycopg://estuda_ai_app:${SENHA_APP}@db:5432/estuda_ai
MIGRATION_DATABASE_URL=postgresql+psycopg://estuda_ai:${SENHA_DONO}@db:5432/estuda_ai

JWT_SECRET=$(segredo)
JWT_DIAS=30
BFF_SEGREDO=$(segredo)

MAX_UPLOAD_MB=20
EMBEDDING_MODEL=intfloat/multilingual-e5-small
LIMITE_GERACOES_DIA=50
LIMITE_IA_POR_MINUTO=10
LIMITE_LOGIN_POR_IP=20
LIMITE_LOGIN_POR_EMAIL=5

ANTHROPIC_MODEL=claude-haiku-4-5-20251001
# Descomente e cole a chave para ligar a IA (depois: docker compose ... up -d backend)
# ANTHROPIC_API_KEY=
EOF
chmod 600 .env
echo ".env criado para ${DOMINIO} (permissão 600)."
