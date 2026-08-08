#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}; copy the deployment secrets/settings into the ignored .env first." >&2
  exit 1
fi

# Terraform does not read dotenv files itself. This wrapper keeps the provider
# token and deployment inputs in the ignored .env while exporting only the
# TF_VAR names Terraform expects for this root module.
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
export ARM_USE_AZUREAD="${ARM_USE_AZUREAD:-true}"

: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN is required in .env}"
: "${CLOUDFLARE_ACCOUNT_ID:?CLOUDFLARE_ACCOUNT_ID is required in .env}"
: "${CLOUDFLARE_ZONE_ID:?CLOUDFLARE_ZONE_ID is required in .env}"
: "${CLOUDFLARE_TUNNEL_HOSTNAME:?CLOUDFLARE_TUNNEL_HOSTNAME is required in .env}"

export TF_VAR_cloudflare_account_id="${TF_VAR_cloudflare_account_id:-${CLOUDFLARE_ACCOUNT_ID}}"
export TF_VAR_cloudflare_zone_id="${TF_VAR_cloudflare_zone_id:-${CLOUDFLARE_ZONE_ID}}"
export TF_VAR_cloudflare_hostname="${TF_VAR_cloudflare_hostname:-${CLOUDFLARE_TUNNEL_HOSTNAME}}"
export TF_VAR_public_base_url="${TF_VAR_public_base_url:-${PUBLIC_BASE_URL:-https://${CLOUDFLARE_TUNNEL_HOSTNAME}}}"
export TF_VAR_app_port="${TF_VAR_app_port:-${APP_PORT:-8000}}"

# Who Cloudflare Access lets in. Terraform reads list variables from the
# environment as JSON, so build it from the comma-separated .env value.
if [[ -z "${TF_VAR_cloudflare_access_allowed_emails:-}" ]]; then
  : "${CLOUDFLARE_ACCESS_EMAILS:?CLOUDFLARE_ACCESS_EMAILS is required in .env (comma-separated addresses allowed through Cloudflare Access)}"
  ACCESS_EMAILS_JSON=""
  IFS=',' read -ra ACCESS_EMAILS <<<"${CLOUDFLARE_ACCESS_EMAILS}"
  for ACCESS_EMAIL in "${ACCESS_EMAILS[@]}"; do
    ACCESS_EMAIL="${ACCESS_EMAIL//[[:space:]]/}"
    [[ -z "${ACCESS_EMAIL}" ]] && continue
    ACCESS_EMAILS_JSON+="${ACCESS_EMAILS_JSON:+,}\"${ACCESS_EMAIL}\""
  done
  export TF_VAR_cloudflare_access_allowed_emails="[${ACCESS_EMAILS_JSON}]"
fi
export TF_VAR_sms_provider="${TF_VAR_sms_provider:-${SMS_PROVIDER:-mock}}"
export TF_VAR_twilio_account_sid="${TF_VAR_twilio_account_sid:-${TWILIO_ACCOUNT_SID:-}}"
export TF_VAR_twilio_auth_token="${TF_VAR_twilio_auth_token:-${TWILIO_AUTH_TOKEN:-}}"
export TF_VAR_twilio_from_number="${TF_VAR_twilio_from_number:-${TWILIO_FROM_NUMBER:-}}"
export TF_VAR_google_maps_api_key="${TF_VAR_google_maps_api_key:-${GOOGLE_MAPS_API_KEY:-}}"
export TF_VAR_clerk_enabled="${TF_VAR_clerk_enabled:-${CLERK_ENABLED:-false}}"
export TF_VAR_clerk_publishable_key="${TF_VAR_clerk_publishable_key:-${CLERK_PUBLISHABLE_KEY:-}}"
export TF_VAR_clerk_frontend_api_url="${TF_VAR_clerk_frontend_api_url:-${CLERK_FRONTEND_API_URL:-}}"
export TF_VAR_clerk_secret_key="${TF_VAR_clerk_secret_key:-${CLERK_SECRET_KEY:-}}"
export TF_VAR_clerk_jwt_key="${TF_VAR_clerk_jwt_key:-${CLERK_JWT_KEY:-}}"
export TF_VAR_clerk_authorized_parties="${TF_VAR_clerk_authorized_parties:-${CLERK_AUTHORIZED_PARTIES:-}}"
if [[ -z "${TF_VAR_subscription_id:-}" ]]; then
  export TF_VAR_subscription_id="$(az account show --query id -o tsv)"
fi

if [[ -z "${TF_VAR_ssh_public_key:-}" ]]; then
  USER_HOME="$(getent passwd "$(id -un)" | cut -d: -f6)"
  SSH_KEY_PATH="${HANGOUT_SSH_PUBLIC_KEY_PATH:-${USER_HOME}/.ssh/github-auth.pem.pub}"
  if [[ ! -f "${SSH_KEY_PATH}" ]]; then
    echo "Set TF_VAR_ssh_public_key or HANGOUT_SSH_PUBLIC_KEY_PATH to a public SSH key." >&2
    exit 1
  fi
  export TF_VAR_ssh_public_key="$(<"${SSH_KEY_PATH}")"
fi

BACKEND_FILE="${REPO_DIR}/terraform/backend.hcl"
BACKEND_DECLARATION="${REPO_DIR}/terraform/backend.tf"
COMMAND="${1:-}"
if [[ "${COMMAND}" == "init" ]]; then
  shift
  if [[ -f "${BACKEND_FILE}" && -f "${BACKEND_DECLARATION}" ]]; then
    exec terraform -chdir="${REPO_DIR}/terraform" init -backend-config="${BACKEND_FILE}" "$@"
  fi
  echo "No ${BACKEND_FILE}; initializing with local state for validation only. Run ./scripts/bootstrap_state.sh apply before production apply." >&2
  exec terraform -chdir="${REPO_DIR}/terraform" init -backend=false "$@"
fi

if [[ "${COMMAND}" == "apply" && ( ! -f "${BACKEND_FILE}" || ! -f "${BACKEND_DECLARATION}" ) ]]; then
  echo "Refusing apply without remote state. Run ./scripts/bootstrap_state.sh apply, then ./scripts/terraform.sh init." >&2
  exit 1
fi

exec terraform -chdir="${REPO_DIR}/terraform" "$@"
