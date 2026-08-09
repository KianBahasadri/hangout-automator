#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"
COMMAND="${1:-}"

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
export TF_VAR_clerk_dns_id="${TF_VAR_clerk_dns_id:-${CLERK_DNS_ID:-}}"

# Cloudflare Access used to sit in front of the Tunnel and gate every path, so
# CLERK_ENABLED=false still left an edge login. Access is gone, so that switch
# now decides between "Clerk session required" and "the public internet can
# reach every route". Refuse the deploy rather than publish the app open.
# Only on apply: plan and validate change nothing and must stay runnable.
if [[ "${COMMAND}" == "apply" && "${TF_VAR_clerk_enabled}" != "true" \
      && "${HANGOUT_ALLOW_UNAUTHENTICATED_DEPLOY:-}" != "1" ]]; then
  echo "Refusing apply: CLERK_ENABLED is '${TF_VAR_clerk_enabled}', but Clerk is the only authentication boundary since Cloudflare Access was removed." >&2
  echo "Set CLERK_ENABLED=true, or set HANGOUT_ALLOW_UNAUTHENTICATED_DEPLOY=1 to deploy a deliberately public instance." >&2
  exit 1
fi

# Refuse to ship a development Clerk instance as production auth. A pk_test_
# key is user-capped, accepts any signer, and is not an authentication
# boundary. The mismatch is silent at apply time and only surfaces as a
# signed-in stranger, so catch it here rather than in the logs.
if [[ "${TF_VAR_clerk_enabled}" == "true" && "${TF_VAR_clerk_publishable_key}" == pk_test_* ]]; then
  echo "Refusing apply: CLERK_ENABLED=true with a development publishable key (pk_test_)." >&2
  echo "Use a Clerk production instance (pk_live_/sk_live_), or set HANGOUT_ALLOW_CLERK_TEST_KEYS=1 to override." >&2
  [[ "${HANGOUT_ALLOW_CLERK_TEST_KEYS:-}" == "1" ]] || exit 1
fi

# The frontend API host and the publishable key encode the same instance; if
# they disagree the browser loads ClerkJS from one instance and presents it a
# key from another, which fails only in the browser. The key's payload is the
# host followed by "$".
if [[ -n "${TF_VAR_clerk_publishable_key}" && -n "${TF_VAR_clerk_frontend_api_url}" ]]; then
  KEY_BODY="${TF_VAR_clerk_publishable_key#pk_test_}"
  KEY_BODY="${KEY_BODY#pk_live_}"
  # Clerk strips the base64 padding; some base64(1) builds reject that.
  while (( ${#KEY_BODY} % 4 )); do KEY_BODY+="="; done
  KEY_HOST="$(printf '%s' "${KEY_BODY}" | base64 -d 2>/dev/null | tr -d '$' || true)"
  URL_HOST="${TF_VAR_clerk_frontend_api_url#https://}"
  URL_HOST="${URL_HOST%%/*}"
  if [[ -n "${KEY_HOST}" && "${KEY_HOST}" != "${URL_HOST}" ]]; then
    echo "Refusing apply: CLERK_PUBLISHABLE_KEY encodes '${KEY_HOST}' but CLERK_FRONTEND_API_URL is '${URL_HOST}'." >&2
    exit 1
  fi
fi
# Postgres admin password has no Terraform default by design; the operator's
# .env is the only source.
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required in .env}"
export TF_VAR_postgres_admin_password="${TF_VAR_postgres_admin_password:-${POSTGRES_ADMIN_PASSWORD}}"
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
if [[ "${COMMAND}" == "init" ]]; then
  shift
  if [[ -f "${BACKEND_FILE}" && -f "${BACKEND_DECLARATION}" ]]; then
    exec terraform -chdir="${REPO_DIR}/terraform" init -backend-config="${BACKEND_FILE}" "$@"
  fi
  echo "No ${BACKEND_FILE}; initializing with local state for validation only. Run ./scripts/deploy/bootstrap_state.sh apply before production apply." >&2
  exec terraform -chdir="${REPO_DIR}/terraform" init -backend=false "$@"
fi

if [[ "${COMMAND}" == "apply" && ( ! -f "${BACKEND_FILE}" || ! -f "${BACKEND_DECLARATION}" ) ]]; then
  echo "Refusing apply without remote state. Run ./scripts/deploy/bootstrap_state.sh apply, then ./scripts/deploy/terraform.sh init." >&2
  exit 1
fi

exec terraform -chdir="${REPO_DIR}/terraform" "$@"
