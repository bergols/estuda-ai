#!/usr/bin/env bash
# Prepara uma VM Ubuntu 24.04 (Oracle Cloud, ARM) para rodar o estuda-ai. Rode UMA vez:
#
#   sudo deploy/preparar-servidor.sh
#
# Instala o Docker, liga as atualizações automáticas de segurança, abre 80/443 no
# firewall do sistema, cria swap, deixa o SSH só com chave e agenda o backup diário.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Rode com sudo." >&2; exit 1; }
USUARIO="${SUDO_USER:-ubuntu}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "== pacotes"
apt-get update
apt-get -y upgrade
# Docker dos repositórios do próprio Ubuntu (assinados pela Canonical)
apt-get install -y docker.io docker-compose-v2 docker-buildx git openssl \
  unattended-upgrades iptables-persistent
usermod -aG docker "$USUARIO"
systemctl enable --now docker

echo "== atualizações automáticas de segurança"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== firewall do sistema"
# As imagens Ubuntu da Oracle vêm com iptables que REJEITA tudo menos a porta 22.
# Abre 80/443 antes da regra de REJECT. (Também é preciso liberar 80/443 na
# "Security List" da VCN, no console da Oracle: são duas camadas.)
for porta in 80 443; do
  iptables -C INPUT -p tcp --dport "$porta" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp --dport "$porta" -m state --state NEW -j ACCEPT
done
netfilter-persistent save

echo "== swap de 2 GB (folga para o build das imagens)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== SSH só com chave"
cat > /etc/ssh/sshd_config.d/10-estuda-ai.conf <<EOF
PasswordAuthentication no
PermitRootLogin no
EOF
systemctl reload ssh

echo "== backup diário às 04:00 UTC (01:00 em Brasília)"
linha="0 4 * * * $REPO/deploy/backup.sh >> /home/$USUARIO/backups/backup.log 2>&1"
mkdir -p "/home/$USUARIO/backups"
chown "$USUARIO:$USUARIO" "/home/$USUARIO/backups"
( crontab -u "$USUARIO" -l 2>/dev/null | grep -v 'deploy/backup.sh' || true; echo "$linha" ) \
  | crontab -u "$USUARIO" -

echo
echo "Pronto. Saia e entre de novo no SSH (para o grupo docker valer) e siga o docs/deploy.md."
