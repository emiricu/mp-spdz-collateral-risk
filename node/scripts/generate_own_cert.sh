#!/usr/bin/env bash
# Generates ONLY this party's own self-signed cert+key for -e (encrypted
# channels), same openssl invocation as Scripts/generate-cert.sh in the
# repo root. Never touches the peer's key. Shared by node/entrypoint.sh
# (real deploy: runs at worker startup) and local-test/docker-compose.yml
# (dev-only: runs as an explicit one-shot init step so the cert-exchange
# step has something to wait on without a startup-order deadlock).
set -euo pipefail
cd "${MP_SPDZ_HOME:-/usr/src/MP-SPDZ}"

: "${PARTY_ID:?PARTY_ID must be set (0 or 1)}"

mkdir -p Player-Data
pem="Player-Data/P${PARTY_ID}.pem"
key="Player-Data/P${PARTY_ID}.key"
if [ ! -f "$pem" ] || [ ! -f "$key" ]; then
    echo "[generate_own_cert] generating local certificate for party ${PARTY_ID}..."
    openssl req -newkey rsa -nodes -x509 -out "$pem" -keyout "$key" \
        -subj "/CN=P${PARTY_ID}" -days 3650
else
    echo "[generate_own_cert] party ${PARTY_ID} already has a certificate, keeping it"
fi
