#!/usr/bin/env bash
# Tenta criar a VM Always Free da Oracle até haver capacidade ("Out of host capacity").
#
#   caffeinate -i deploy/oracle-criar-vm.sh
#
# Roda no Mac, com a OCI CLI configurada (~/.oci/config; ver docs/deploy.md, passo A3).
# caffeinate impede o Mac de dormir enquanto tenta (deixe na tomada, tampa aberta).
#
# O que ele faz, sozinho:
#   - descobre o compartimento raiz (tenancy), os availability domains, a VCN pelo nome,
#     a subnet PÚBLICA dela e a imagem Ubuntu 24.04 ARM mais recente;
#   - cria SEMPRE o shape grátis: VM.Standard.A1.Flex com 1 OCPU e 4 GB (fixo no código,
#     para nenhum erro de digitação criar algo pago);
#   - se não houver capacidade, espera INTERVALO segundos (+ um sorteio) e tenta de novo;
#   - antes de cada tentativa, confere se a VM já existe (nada de criar duas);
#   - quando consegue, mostra o IP público e avisa com uma notificação do macOS.
# Qualquer erro diferente de "sem capacidade" para o script e mostra a mensagem.
#
# Variáveis opcionais: NOME_VM (estuda-ai), NOME_VCN (estuda-ai), INTERVALO (90),
# CHAVE_SSH (~/.ssh/oracle_estuda_ai.pub), OCI_CLI_PROFILE (DEFAULT).
set -uo pipefail

NOME_VM="${NOME_VM:-estuda-ai}"
NOME_VCN="${NOME_VCN:-estuda-ai}"
INTERVALO="${INTERVALO:-90}"
CHAVE_SSH="${CHAVE_SSH:-$HOME/.ssh/oracle_estuda_ai.pub}"
PERFIL="${OCI_CLI_PROFILE:-DEFAULT}"
SHAPE="VM.Standard.A1.Flex"
SHAPE_CONFIG='{"ocpus": 1, "memoryInGBs": 4}' # Always Free: não aumente sem ler docs/deploy.md

# A OCI CLI empacotada com Python 3.14 imprime SyntaxWarnings inofensivos a cada comando
export PYTHONWARNINGS="ignore::SyntaxWarning"
oci() { command oci --profile "$PERFIL" "$@"; }
agora() { date "+%d/%m %H:%M:%S"; }
avisar() {
  echo "$(agora) $1"
  osascript -e "display notification \"$1\" with title \"estuda-ai: VM na Oracle\" sound name \"Glass\"" \
    > /dev/null 2>&1 || true
}

command -v oci > /dev/null || { echo "OCI CLI não encontrada: brew install oci-cli" >&2; exit 1; }
[[ -f "$CHAVE_SSH" ]] || { echo "Chave SSH pública não encontrada: $CHAVE_SSH" >&2; exit 1; }
[[ "$CHAVE_SSH" == *.pub ]] || { echo "Use a chave PÚBLICA (.pub), nunca a privada." >&2; exit 1; }

# Chave de API recém-criada demora alguns minutos para chegar a todos os servidores da
# Oracle: no meio disso, uns pedidos passam e outros dão 401. Espera 5 sucessos seguidos.
echo "== esperando a chave de API valer em todos os serviços"
seguidos=0
for _ in $(seq 120); do
  if oci network vcn list --compartment-id "$(python3 -c 'import configparser,os,sys; c=configparser.ConfigParser(); c.read(os.path.expanduser("~/.oci/config")); print(c[sys.argv[1]]["tenancy"])' "$PERFIL")" \
       --limit 1 > /dev/null 2>&1; then
    seguidos=$((seguidos + 1))
    [[ $seguidos -ge 5 ]] && break
  else
    seguidos=0
  fi
  sleep 10
done
[[ $seguidos -ge 5 ]] || { echo "A autenticação não estabilizou em 20 min: confira a chave em ~/.oci/config." >&2; exit 1; }

echo "== descobrindo a conta"
TENANCY=$(python3 - "$PERFIL" <<'PY'
import configparser, os, sys
c = configparser.ConfigParser()
c.read(os.path.expanduser("~/.oci/config"))
print(c[sys.argv[1]]["tenancy"])
PY
) || { echo "Não consegui ler o tenancy em ~/.oci/config (perfil $PERFIL)." >&2; exit 1; }

ADS=$(oci iam availability-domain list --compartment-id "$TENANCY" \
  --query 'data[].name' --raw-output | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))') \
  || { echo "A OCI CLI não autenticou. Teste com: oci iam region list" >&2; exit 1; }

VCN=$(oci network vcn list --compartment-id "$TENANCY" --display-name "$NOME_VCN" \
  --query 'data[0].id' --raw-output)
[[ -n "$VCN" && "$VCN" != "null" ]] || { echo "VCN '$NOME_VCN' não encontrada." >&2; exit 1; }

SUBNET=$(oci network subnet list --compartment-id "$TENANCY" --vcn-id "$VCN" \
  --query 'data[?"prohibit-public-ip-on-vnic"==`false`].id | [0]' --raw-output)
[[ -n "$SUBNET" && "$SUBNET" != "null" ]] \
  || { echo "A VCN '$NOME_VCN' não tem subnet PÚBLICA (ver docs/deploy.md, passo A3)." >&2; exit 1; }

IMAGEM=$(oci compute image list --compartment-id "$TENANCY" \
  --operating-system "Canonical Ubuntu" --operating-system-version "24.04" --shape "$SHAPE" \
  --sort-by TIMECREATED --sort-order DESC \
  --query 'data[?!contains("display-name", `Minimal`)] | [0]."display-name"' --raw-output)
IMAGEM_ID=$(oci compute image list --compartment-id "$TENANCY" \
  --operating-system "Canonical Ubuntu" --operating-system-version "24.04" --shape "$SHAPE" \
  --sort-by TIMECREATED --sort-order DESC \
  --query 'data[?!contains("display-name", `Minimal`)] | [0].id' --raw-output)
[[ -n "$IMAGEM_ID" && "$IMAGEM_ID" != "null" ]] || { echo "Imagem Ubuntu 24.04 ARM não encontrada." >&2; exit 1; }

echo "   availability domains: $(echo "$ADS" | tr '\n' ' ')"
echo "   rede: $NOME_VCN (subnet pública)"
echo "   imagem: $IMAGEM"
echo "   shape: $SHAPE $SHAPE_CONFIG"
echo "   tentando a cada ~${INTERVALO}s. Ctrl+C para parar."
echo

vm_existente() {
  oci compute instance list --compartment-id "$TENANCY" --display-name "$NOME_VM" \
    --query 'data[?"lifecycle-state"!=`TERMINATED` && "lifecycle-state"!=`TERMINATING`] | [0].id' \
    --raw-output 2> /dev/null
}

concluir() {
  local id="$1" ip=""
  for _ in $(seq 30); do
    ip=$(oci compute instance list-vnics --instance-id "$id" --query 'data[0]."public-ip"' \
      --raw-output 2> /dev/null)
    [[ -n "$ip" && "$ip" != "null" ]] && break
    sleep 10
  done
  avisar "VM criada! IP público: ${ip:-ainda sem IP (veja no console)}"
  echo
  echo "Próximo passo (docs/deploy.md, A4 e A5): liberar 80/443, DuckDNS com este IP, e"
  echo "  ssh -i ${CHAVE_SSH%.pub} ubuntu@${ip:-IP_DA_VM}"
  exit 0
}

tentativa=0
nao_autenticado=0
while true; do
  existente=$(vm_existente)
  if [[ -n "$existente" && "$existente" != "null" ]]; then
    echo "$(agora) a VM '$NOME_VM' já existe."
    concluir "$existente"
  fi

  for AD in $ADS; do
    tentativa=$((tentativa + 1))
    saida=$(oci compute instance launch \
      --compartment-id "$TENANCY" --availability-domain "$AD" \
      --display-name "$NOME_VM" --shape "$SHAPE" --shape-config "$SHAPE_CONFIG" \
      --image-id "$IMAGEM_ID" --subnet-id "$SUBNET" --assign-public-ip true \
      --ssh-authorized-keys-file "$CHAVE_SSH" \
      --query 'data.id' --raw-output 2>&1)
    if [[ $? -eq 0 && "$saida" == ocid1.instance.* ]]; then
      echo "$(agora) tentativa $tentativa ($AD): ACEITA, criando..."
      concluir "$saida"
    elif grep -qiE "out of (host )?capacity" <<< "$saida"; then
      nao_autenticado=0
      echo "$(agora) tentativa $tentativa ($AD): sem capacidade"
    elif grep -q "NotAuthenticated" <<< "$saida"; then
      # 401 isolado = propagação da chave; persistente (~30 min) = chave errada
      nao_autenticado=$((nao_autenticado + 1))
      echo "$(agora) tentativa $tentativa ($AD): 401 da Oracle, tentando de novo"
      if [[ $nao_autenticado -ge 15 ]]; then
        echo "$saida" >&2
        avisar "A Oracle recusa a chave de API há muito tempo: o script parou."
        exit 1
      fi
      continue
    elif grep -qiE "TooManyRequests|\"status\": 429" <<< "$saida"; then
      echo "$(agora) tentativa $tentativa ($AD): muitas requisições, esperando mais"
      sleep 120
    else
      echo "$saida" >&2
      avisar "Erro diferente de 'sem capacidade': o script parou (veja o terminal)."
      exit 1
    fi
  done
  sleep $((INTERVALO + RANDOM % 30))
done
