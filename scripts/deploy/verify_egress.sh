#!/usr/bin/env bash
# Verify the VM still reaches the internet after an infrastructure change.
#
# Azure programs a VM's outbound SNAT when the VM is placed on a host, so
# changing the subnet's egress method underneath a running VM never reaches
# that VM. Terraform reports success, the subnet reports the new setting, and
# the VM has no egress at all: cloudflared cannot dial the Cloudflare edge and
# the public hostname goes dark behind a completely green apply. A guest reboot
# does not help. Only a deallocate/start makes the platform re-provision the
# VM's networking.
#
# That is not hypothetical — it took the site down on 2026-08-20 when the NAT
# Gateway was removed (see docs/deployment-history.md). This check exists so the
# next egress change fails loudly at deploy time instead of silently.
#
# Runs automatically after `scripts/deploy/terraform.sh apply`; also safe to run
# by hand. Set HANGOUT_SKIP_EGRESS_CHECK=1 to skip it.
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ATTEMPTS="${HANGOUT_EGRESS_CHECK_ATTEMPTS:-6}"
INTERVAL="${HANGOUT_EGRESS_CHECK_INTERVAL:-15}"

if ! command -v az >/dev/null 2>&1; then
  echo "verify_egress: az CLI not found; skipping post-apply egress check." >&2
  exit 0
fi

RESOURCE_GROUP="${HANGOUT_RESOURCE_GROUP:-}"
if [[ -z "${RESOURCE_GROUP}" ]]; then
  RESOURCE_GROUP="$(terraform -chdir="${REPO_DIR}/terraform" output -raw resource_group 2>/dev/null || true)"
fi
if [[ -z "${RESOURCE_GROUP}" ]]; then
  echo "verify_egress: could not determine the resource group; skipping." >&2
  exit 0
fi

# Name the VM from the group rather than assuming "${prefix}-vm", so a renamed
# prefix does not silently turn this check into a no-op.
VM_NAME="${HANGOUT_VM_NAME:-}"
if [[ -z "${VM_NAME}" ]]; then
  VM_NAME="$(az vm list -g "${RESOURCE_GROUP}" --query "[0].name" -o tsv 2>/dev/null || true)"
fi
if [[ -z "${VM_NAME}" ]]; then
  echo "verify_egress: no VM in ${RESOURCE_GROUP} yet; nothing to check." >&2
  exit 0
fi

# The Run Command agent executes with sh (dash), not bash, so this payload
# stays POSIX. Reaching the Cloudflare edge on 7844 is the check that actually
# matters -- that is the connection cloudflared depends on -- but a plain HTTPS
# fetch is what distinguishes "no egress at all" from "edge unreachable".
PROBE='curl -sS -m 12 https://api.ipify.org 2>/dev/null && echo "" || echo "EGRESS_FAIL"
systemctl is-active cloudflared 2>/dev/null || echo "cloudflared-inactive"'

echo "verify_egress: checking outbound connectivity on ${VM_NAME} (${RESOURCE_GROUP})..." >&2
attempt=1
while [[ "${attempt}" -le "${ATTEMPTS}" ]]; do
  OUTPUT="$(az vm run-command invoke -g "${RESOURCE_GROUP}" -n "${VM_NAME}" \
    --command-id RunShellScript --scripts "${PROBE}" \
    --query "value[0].message" -o tsv 2>/dev/null || true)"

  if grep -qE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' <<<"${OUTPUT}" \
     && ! grep -q 'EGRESS_FAIL' <<<"${OUTPUT}"; then
    EGRESS_IP="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' <<<"${OUTPUT}" | head -1)"
    if grep -qx 'active' <<<"${OUTPUT}"; then
      echo "verify_egress: OK — egress via ${EGRESS_IP}, cloudflared active." >&2
      exit 0
    fi
    echo "verify_egress: egress works (${EGRESS_IP}) but cloudflared is not active; retrying (${attempt}/${ATTEMPTS})." >&2
  else
    echo "verify_egress: no egress yet; retrying (${attempt}/${ATTEMPTS})." >&2
  fi

  attempt=$((attempt + 1))
  [[ "${attempt}" -le "${ATTEMPTS}" ]] && sleep "${INTERVAL}"
done

cat >&2 <<REMEDY

verify_egress: FAILED — ${VM_NAME} cannot reach the internet.

The apply itself succeeded. If this run changed the subnet's egress method
(default_outbound_access_enabled, a NAT Gateway, or a public IP on the NIC),
the running VM did not pick it up: Azure programs outbound SNAT at VM
placement. A guest reboot will NOT fix it. Deallocate and start so the platform
re-provisions the VM's networking:

  az vm deallocate -g ${RESOURCE_GROUP} -n ${VM_NAME}
  az vm start -g ${RESOURCE_GROUP} -n ${VM_NAME}
  ./scripts/deploy/verify_egress.sh

That is a full stop and start of the app, not a reboot. Until it completes,
cloudflared cannot dial the Cloudflare edge and the public hostname is down.
REMEDY
exit 1
