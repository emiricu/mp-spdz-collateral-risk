#!/bin/sh
# DEV-ONLY convenience: waits for both workers to generate their own
# self-signed cert (entrypoint.sh does this on first boot), then copies
# each one into the OTHER node's Player-Data volume and rehashes both --
# the exact manual step node/README.md documents for the real two-host
# deployment, automated here only because both volumes happen to be
# reachable from a single host in this dev compose file. Never do this
# automatic cross-copy in the real deployment: there, the two Player-Data
# directories live on different machines and the .pem exchange (public
# certs only, never the .key files) has to cross that boundary out of
# band, by a human, on purpose.
set -eu

TIMEOUT=120
elapsed=0
while [ ! -f /node0/P0.pem ] || [ ! -f /node1/P1.pem ]; do
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
        echo "cert-exchange: timeout waiting for both certs" >&2
        exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

cp /node1/P1.pem /node0/P1.pem
cp /node0/P0.pem /node1/P0.pem

apk add --no-cache openssl >/dev/null 2>&1 || true
openssl rehash /node0 >/dev/null 2>&1 || true
openssl rehash /node1 >/dev/null 2>&1 || true

echo "cert-exchange: done (P0.pem <-> P1.pem exchanged, both rehashed)"
