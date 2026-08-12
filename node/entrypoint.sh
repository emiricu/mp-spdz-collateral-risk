#!/usr/bin/env bash
set -euo pipefail
cd "${MP_SPDZ_HOME:-/usr/src/MP-SPDZ}"

: "${PARTY_ID:?PARTY_ID must be set (0 or 1)}"

generate_own_cert() {
    # Player-Data/ is a mounted volume so the identity persists across
    # container restarts instead of being regenerated (and thus
    # changing) every time.
    "${MP_SPDZ_HOME:-/usr/src/MP-SPDZ}/node_scripts/generate_own_cert.sh"
    if command -v c_rehash >/dev/null 2>&1; then
        c_rehash Player-Data >/dev/null 2>&1 || true
    elif openssl rehash Player-Data >/dev/null 2>&1; then
        :
    else
        echo "[entrypoint] WARNING: neither c_rehash nor 'openssl rehash' available -- -e (encrypted channels) will fail until Player-Data is rehashed manually" >&2
    fi
    if [ ! -f "Player-Data/P$((1 - PARTY_ID)).pem" ]; then
        echo "[entrypoint] WARNING: peer certificate (P$((1 - PARTY_ID)).pem) missing from Player-Data/ -- MPC runs will fail until it is copied in (see README, certificate exchange section). /salute will report this." >&2
    fi
}

role="${1:-api}"
case "$role" in
    api)
        exec uvicorn node_app.main:app --host 0.0.0.0 --port "${HTTP_PORT:-8000}"
        ;;
    worker)
        generate_own_cert
        # A single `rq worker` process is inherently one-job-at-a-time --
        # concurrency=1 is the queue framework's own default behaviour
        # here, not something enforced by a hand-written lock.
        exec rq worker --url "${REDIS_URL:-redis://redis:6379/0}" default
        ;;
    *)
        echo "ruolo sconosciuto: $role (usa 'api' o 'worker')" >&2
        exit 1
        ;;
esac
