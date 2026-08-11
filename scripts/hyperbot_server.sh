#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/hyperbot_server.sh COMMAND

Commands: config, start, stop, restart, status, logs, health, ui-health,
          quality, catalog
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
command="${1:-}"
if [ "$command" = "-h" ] || [ "$command" = "--help" ]; then
    usage
    exit 0
fi
INSTALL_ROOT="${HYPERBOT_INSTALL_ROOT:-/opt/hyperbot}"
ENV_FILE="${HYPERBOT_ENV_FILE:-${INSTALL_ROOT}/shared/.env.hyperbot}"
if [ ! -f "$ENV_FILE" ] && [ -f "${PROJECT_ROOT}/.env.hyperbot" ]; then
    ENV_FILE="${PROJECT_ROOT}/.env.hyperbot"
fi
[ -f "$ENV_FILE" ] || { echo "Missing HyperBot environment: $ENV_FILE" >&2; exit 1; }

compose() {
    docker compose --env-file "$ENV_FILE" -p hyperbot \
        -f "${PROJECT_ROOT}/docker-compose.hyperbot.yml" --profile collector "$@"
}

assert_safe_activation() {
    grep -qx 'HYPERBOT_COLLECTOR_ENABLED=true' "$ENV_FILE" || {
        echo "Collector is disabled in $ENV_FILE" >&2
        exit 1
    }
    grep -qx 'HYPERBOT_LIVE_ENABLED=false' "$ENV_FILE"
    grep -qx 'HYPERBOT_SHADOW_ONLY=true' "$ENV_FILE"
    if grep -Eq '^[[:space:]]*(HYPERBOT_PRIVATE_KEY|HYPERBOT_SECRET_KEY|TRIDENT_SECRET_KEY)=.+' "$ENV_FILE"; then
        echo "A forbidden signing secret is present in the HyperBot environment" >&2
        exit 1
    fi
}

case "$command" in
    config) compose config ;;
    start) assert_safe_activation; compose up -d --build ;;
    stop) compose stop ;;
    restart) assert_safe_activation; compose up -d --build --force-recreate ;;
    status) compose ps ;;
    logs)
        compose logs --tail "${HYPERBOT_LOG_LINES:-300}" \
            collector maintenance watchdog observer
        ;;
    health) compose exec -T collector python scripts/hyperbot_healthcheck.py ;;
    ui-health)
        compose exec -T observer python scripts/hyperbot_ui_healthcheck.py
        ;;
    quality)
        assert_safe_activation
        compose run --rm --no-deps maintenance \
            python scripts/run_ops_maintenance.py --once "${@:2}"
        ;;
    catalog)
        compose run --rm --no-deps maintenance \
            python scripts/snapshot_market_catalog.py \
            --data-root /app/data/raw/catalog \
            --report-root /app/data/reviews/catalog
        ;;
    *) usage; exit 2 ;;
esac
