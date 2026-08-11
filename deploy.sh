#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [--host HOST] [--user USER] [--identity FILE]
                   [--remote-dir /opt/hyperbot] [--start-collector]
                   [--rollback] [--dry-run]

Installs one committed HyperBot release in an isolated /opt/hyperbot tree.
The default action builds and selects the release but does not start services.
EOF
}

HOST="${HYPERBOT_DEPLOY_HOST:-trident-hetzner}"
SSH_USER="${HYPERBOT_DEPLOY_USER:-trident-deploy}"
IDENTITY_FILE="${HYPERBOT_DEPLOY_IDENTITY:-${HOME}/.ssh/trident_hetzner_ed25519}"
REMOTE_DIR="${HYPERBOT_DEPLOY_DIR:-/opt/hyperbot}"
START_COLLECTOR="false"
ROLLBACK="false"
DRY_RUN="false"

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --user) SSH_USER="$2"; shift 2 ;;
        --identity) IDENTITY_FILE="$2"; shift 2 ;;
        --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
        --start-collector) START_COLLECTOR="true"; shift ;;
        --rollback) ROLLBACK="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done

case "$REMOTE_DIR" in
    /opt/*) ;;
    *) printf 'Remote directory must be a narrow path below /opt\n' >&2; exit 2 ;;
esac
if [[ "$REMOTE_DIR" == *".."* ]] || [ "$REMOTE_DIR" = "/opt" ]; then
    printf 'Unsafe remote directory: %s\n' "$REMOTE_DIR" >&2
    exit 2
fi
if [[ ! "$REMOTE_DIR" =~ ^/opt/[A-Za-z0-9._/-]+$ ]]; then
    printf 'Remote directory contains unsupported characters: %s\n' "$REMOTE_DIR" >&2
    exit 2
fi

SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15)
if [ -f "$IDENTITY_FILE" ]; then
    SSH_OPTIONS=(-i "$IDENTITY_FILE" "${SSH_OPTIONS[@]}")
fi
SSH_TARGET="${SSH_USER}@${HOST}"

remote() {
    ssh "${SSH_OPTIONS[@]}" "$SSH_TARGET" "$@"
}

if [ "$DRY_RUN" = "true" ]; then
    printf '[dry-run] target=%s remote_dir=%s rollback=%s start=%s\n' \
        "$SSH_TARGET" "$REMOTE_DIR" "${ROLLBACK:-false}" "${START_COLLECTOR:-false}"
    exit 0
fi

if [ "$ROLLBACK" = "true" ]; then
    remote bash -s -- "$REMOTE_DIR" "$START_COLLECTOR" <<'REMOTE_ROLLBACK'
set -euo pipefail
install_root="$1"
start_collector="$2"
previous_file="${install_root}/shared/runtime/previous_release"
[ -f "$previous_file" ] || { echo "No previous HyperBot release" >&2; exit 1; }
previous="$(cat "$previous_file")"
case "$previous" in
    "${install_root}"/releases/*) ;;
    *) echo "Unsafe previous release path" >&2; exit 1 ;;
esac
[ -d "$previous" ] || { echo "Previous release is missing" >&2; exit 1; }
current_target="$(readlink -f "${install_root}/current" 2>/dev/null || true)"
ln -sfn "$previous" "${install_root}/current"
[ -n "$current_target" ] && printf '%s\n' "$current_target" > "$previous_file"
if [ "$start_collector" = "true" ]; then
    env_file="${install_root}/shared/.env.hyperbot"
    grep -qx 'HYPERBOT_COLLECTOR_ENABLED=true' "$env_file"
    grep -qx 'HYPERBOT_LIVE_ENABLED=false' "$env_file"
    grep -qx 'HYPERBOT_SHADOW_ONLY=true' "$env_file"
    if grep -Eq '^[[:space:]]*(HYPERBOT_PRIVATE_KEY|HYPERBOT_SECRET_KEY|TRIDENT_SECRET_KEY)=.+' "$env_file"; then
        echo "Forbidden signing secret in HyperBot environment" >&2
        exit 1
    fi
    docker compose --env-file "$env_file" -p hyperbot \
        -f "${install_root}/current/docker-compose.hyperbot.yml" \
        --profile collector up -d --build
fi
echo "HyperBot rollback selected: $previous"
REMOTE_ROLLBACK
    exit 0
fi

if [ -n "$(git status --porcelain=v1)" ]; then
    printf 'Refusing to deploy a dirty worktree; commit M7-Ops first.\n' >&2
    exit 1
fi
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD)"
REMOTE_RELEASE="${REMOTE_DIR}/releases/${RELEASE_ID}"

remote mkdir -p "$REMOTE_DIR/releases" "$REMOTE_DIR/shared/data" \
    "$REMOTE_DIR/shared/runtime" "$REMOTE_DIR/shared/reports" \
    "$REMOTE_DIR/shared/logs"
remote test ! -e "$REMOTE_RELEASE"
remote mkdir "$REMOTE_RELEASE"
remote touch "$REMOTE_DIR/shared/alert_webhook_url"
remote chmod 600 "$REMOTE_DIR/shared/alert_webhook_url"
remote bash -s -- "$REMOTE_DIR/shared/ui_password" <<'REMOTE_SECRET'
set -euo pipefail
password_file="$1"
if [ ! -s "$password_file" ]; then
    command -v openssl >/dev/null 2>&1 || {
        echo "openssl is required to generate the UI password" >&2
        exit 1
    }
    openssl rand -base64 -out "$password_file" 32
fi
chmod 600 "$password_file"
REMOTE_SECRET
git archive --format=tar HEAD | remote tar -xf - -C "$REMOTE_RELEASE"

remote bash -s -- "$REMOTE_DIR" "$REMOTE_RELEASE" "$START_COLLECTOR" <<'REMOTE_INSTALL'
set -euo pipefail
install_root="$1"
release="$2"
start_collector="$3"
env_file="${install_root}/shared/.env.hyperbot"
ensure_env_setting() {
    key="$1"
    value="$2"
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}
if [ ! -f "$env_file" ]; then
    cp "${release}/.env.hyperbot.example" "$env_file"
    echo "Created disabled environment: $env_file"
fi
ensure_env_setting HYPERBOT_HOST_SHARED_DIR "${install_root}/shared"
ensure_env_setting HYPERBOT_ALERT_WEBHOOK_FILE \
    "${install_root}/shared/alert_webhook_url"
ensure_env_setting HYPERBOT_UI_AUTH_PASSWORD_FILE \
    "${install_root}/shared/ui_password"
ensure_env_setting HYPERBOT_UI_HOST "0.0.0.0"
ensure_env_setting HYPERBOT_UI_PORT "3002"
ensure_env_setting HYPERBOT_UI_PUBLISH_HOST "0.0.0.0"
ensure_env_setting HYPERBOT_UI_AUTH_REQUIRED "true"
ensure_env_setting HYPERBOT_UI_AUTH_USERNAME "hyperbot"
ensure_env_setting HYPERBOT_UI_REFRESH_SECONDS "10"
ensure_env_setting HYPERBOT_UID "$(id -u)"
ensure_env_setting HYPERBOT_GID "$(id -g)"
chmod 600 "$env_file"
docker compose --env-file "$env_file" -p hyperbot \
    -f "${release}/docker-compose.hyperbot.yml" --profile collector config --quiet
docker compose --env-file "$env_file" -p hyperbot \
    -f "${release}/docker-compose.hyperbot.yml" --profile collector build
current_target="$(readlink -f "${install_root}/current" 2>/dev/null || true)"
if [ -n "$current_target" ] && [ "$current_target" != "$release" ]; then
    printf '%s\n' "$current_target" > "${install_root}/shared/runtime/previous_release"
fi
ln -sfn "$release" "${install_root}/current"
if [ "$start_collector" = "true" ]; then
    grep -qx 'HYPERBOT_COLLECTOR_ENABLED=true' "$env_file" || {
        echo "Collector remains disabled in $env_file" >&2
        exit 1
    }
    grep -qx 'HYPERBOT_LIVE_ENABLED=false' "$env_file"
    grep -qx 'HYPERBOT_SHADOW_ONLY=true' "$env_file"
    if grep -Eq '^[[:space:]]*(HYPERBOT_PRIVATE_KEY|HYPERBOT_SECRET_KEY|TRIDENT_SECRET_KEY)=.+' "$env_file"; then
        echo "Forbidden signing secret in HyperBot environment" >&2
        exit 1
    fi
    docker compose --env-file "$env_file" -p hyperbot \
        -f "${install_root}/current/docker-compose.hyperbot.yml" \
        --profile collector up -d
else
    echo "Release selected but collector left disabled/stopped."
fi
echo "HyperBot release ready: $release"
echo "Dashboard/API configured on public TCP port 3002 (authentication required)."
echo "UI password file: ${install_root}/shared/ui_password"
REMOTE_INSTALL
