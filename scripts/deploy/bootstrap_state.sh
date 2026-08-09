#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP_DIR="${REPO_DIR}/terraform/bootstrap-state"
MAIN_BACKEND_FILE="${REPO_DIR}/terraform/backend.hcl"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI (az) is required for state bootstrap." >&2
  exit 1
fi

SUBSCRIPTION_ID="${TF_VAR_subscription_id:-$(az account show --query id -o tsv)}"
export TF_VAR_subscription_id="${SUBSCRIPTION_ID}"

COMMAND="${1:-plan}"
shift || true
terraform -chdir="${BOOTSTRAP_DIR}" init

if [[ "${COMMAND}" == "apply" ]]; then
  terraform -chdir="${BOOTSTRAP_DIR}" apply "$@"
  RESOURCE_GROUP_NAME="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw resource_group_name)"
  STORAGE_ACCOUNT_NAME="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw storage_account_name)"
  CONTAINER_NAME="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw container_name)"
  STATE_KEY="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw state_key)"
  {
    printf 'resource_group_name  = "%s"\n' "${RESOURCE_GROUP_NAME}"
    printf 'storage_account_name = "%s"\n' "${STORAGE_ACCOUNT_NAME}"
    printf 'container_name       = "%s"\n' "${CONTAINER_NAME}"
    printf 'key                  = "%s"\n' "${STATE_KEY}"
    printf 'use_azuread_auth     = true\n'
  } > "${MAIN_BACKEND_FILE}"
  printf 'terraform {\n  backend "azurerm" {}\n}\n' > "${REPO_DIR}/terraform/backend.tf"
  chmod 0600 "${MAIN_BACKEND_FILE}"
  echo "Wrote ${MAIN_BACKEND_FILE}; run ./scripts/deploy/terraform.sh init next." >&2
elif [[ "${COMMAND}" == "plan" ]]; then
  terraform -chdir="${BOOTSTRAP_DIR}" plan "$@"
elif [[ "${COMMAND}" == "init" ]]; then
  :
else
  echo "usage: $0 [plan|apply|init] [terraform options]" >&2
  exit 2
fi
