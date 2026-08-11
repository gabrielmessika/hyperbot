#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/fetch_hyperbot_data.sh [--days N] [--date YYYY-MM-DD]
       [--all] [--host HOST] [--user USER] [--identity FILE]
       [--remote-dir /opt/hyperbot] [--local-dir data/server-fetches] [--dry-run]

Fetches only immutable public collector segments and checksummed reports.
Open segments, .env files, signing material, and TRIDENT data are excluded.
EOF
}

HOST="${HYPERBOT_DEPLOY_HOST:-trident-hetzner}"
SSH_USER="${HYPERBOT_DEPLOY_USER:-trident-deploy}"
IDENTITY_FILE="${HYPERBOT_DEPLOY_IDENTITY:-${HOME}/.ssh/trident_hetzner_ed25519}"
REMOTE_DIR="${HYPERBOT_DEPLOY_DIR:-/opt/hyperbot}"
LOCAL_DIR="data/server-fetches"
DAYS=3
DATES=()
FETCH_ALL=""
DRY_RUN=""

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --user) SSH_USER="$2"; shift 2 ;;
        --identity) IDENTITY_FILE="$2"; shift 2 ;;
        --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
        --local-dir) LOCAL_DIR="$2"; shift 2 ;;
        --days) DAYS="$2"; shift 2 ;;
        --date) DATES+=("$2"); shift 2 ;;
        --all) FETCH_ALL="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

case "$REMOTE_DIR" in /opt/*) ;; *) echo "Unsafe remote directory" >&2; exit 2 ;; esac
[[ "$REMOTE_DIR" != *".."* ]] || { echo "Unsafe remote directory" >&2; exit 2; }
[[ "$REMOTE_DIR" =~ ^/opt/[A-Za-z0-9._/-]+$ ]] || {
    echo "Remote directory contains unsupported characters" >&2
    exit 2
}
[[ "$DAYS" =~ ^[1-9][0-9]*$ ]] || { echo "--days must be positive" >&2; exit 2; }
if [ -z "$LOCAL_DIR" ] || [ "$LOCAL_DIR" = "/" ] || [ "$LOCAL_DIR" = "." ] \
    || [ "$LOCAL_DIR" = ".." ] || [[ "/$LOCAL_DIR/" == *"/../"* ]]; then
    echo "Unsafe local directory" >&2
    exit 2
fi

SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15)
if [ -f "$IDENTITY_FILE" ]; then
    SSH_OPTIONS=(-i "$IDENTITY_FILE" "${SSH_OPTIONS[@]}")
fi
SSH_TARGET="${SSH_USER}@${HOST}"
manifest_args=(--days "$DAYS")
for current_date in "${DATES[@]}"; do manifest_args+=(--date "$current_date"); done
[ -n "$FETCH_ALL" ] && manifest_args+=(--all)

if [ -n "$DRY_RUN" ]; then
    printf '[dry-run] fetch %s:%s/shared -> %s (%s)\n' \
        "$SSH_TARGET" "$REMOTE_DIR" "$LOCAL_DIR" "${manifest_args[*]}"
    exit 0
fi

remote_quote() { printf '%q' "$1"; }
quoted_args=()
for argument in "${manifest_args[@]}"; do quoted_args+=("$(remote_quote "$argument")"); done
remote_command="cd $(remote_quote "$REMOTE_DIR") && docker compose --env-file shared/.env.hyperbot -p hyperbot -f current/docker-compose.hyperbot.yml --profile collector run --rm --no-deps maintenance python scripts/build_fetch_manifest.py --root /app ${quoted_args[*]}"
container_bundle="$(
    ssh "${SSH_OPTIONS[@]}" "$SSH_TARGET" "$remote_command" \
        | sed -n 's/^HYPERBOT_EXPORT_DIR=//p' \
        | tail -n 1
)"
bundle_name="$(basename "$container_bundle")"
[[ "$bundle_name" =~ ^fetch-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] || {
    echo "Unexpected remote export identifier: $bundle_name" >&2
    exit 1
}
remote_bundle="${REMOTE_DIR}/shared/runtime/fetch_exports/${bundle_name}"
destination="${LOCAL_DIR%/}/${bundle_name}"
mkdir -p "$destination/export" "$destination/payload"

rsync -az -e "ssh ${SSH_OPTIONS[*]}" \
    "${SSH_TARGET}:${remote_bundle}/" "$destination/export/"
if [ -s "$destination/export/files.txt" ]; then
    rsync -az -e "ssh ${SSH_OPTIONS[*]}" \
        --files-from="$destination/export/files.txt" \
        "${SSH_TARGET}:${REMOTE_DIR}/shared/" "$destination/payload/"
fi
uv run python scripts/verify_fetch_manifest.py \
    "$destination/export/manifest.json" "$destination/payload"
(
    cd "$destination"
    sha256sum export/manifest.json export/SHA256SUMS export/files.txt \
        > FETCH_METADATA_SHA256SUMS
)
printf '%s\n' "$destination"
