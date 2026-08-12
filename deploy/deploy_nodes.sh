#!/usr/bin/env bash
# Persistent deployment of the two MPC nodes on THIS machine, using the
# same topology already validated in local-test/run_repeated_strangers.py
# (5/5 clean runs, CV 6.4-6.5%): two Docker networks created independently
# and never connected to each other, party 0's MP-SPDZ port published 1:1
# to the host, PARTY0_HOST set to the host's real routable interface IP --
# never a Docker service name, never localhost. This is NOT a real
# two-machine deployment (no second machine available), but it is the
# same code path and the same network mechanism a real second machine
# would use, run here because it's what's available.
#
# Unlike local-test/ (ephemeral, torn down after each check),
# containers here run with --restart unless-stopped and named volumes,
# meant to stay up.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST_IP="${HOST_IP:?set HOST_IP to this machine real routable IP, e.g. HOST_IP=169.58.44.182}"
IMAGE=collateral-node:local
PORT_BASE=5000
HTTP0_PORT=18100
HTTP1_PORT=18101

echo "=== deploying nodes, PARTY0_HOST=$HOST_IP ==="

for c in node0-redis node0-api node0-worker node1-redis node1-api node1-worker; do
    docker rm -f "$c" >/dev/null 2>&1 || true
done
docker network create node0-net >/dev/null 2>&1 || true
docker network create node1-net >/dev/null 2>&1 || true

docker run -d --name node0-redis --network node0-net --restart unless-stopped \
    -v node0-redis-data:/data \
    redis:7-alpine redis-server --save 60 1

docker run -d --name node0-api --network node0-net --restart unless-stopped \
    -p "${HTTP0_PORT}:8000" \
    -e PARTY_ID=0 -e PARTY0_HOST="$HOST_IP" -e PORT_BASE=$PORT_BASE -e HTTP_PORT=8000 \
    -e REDIS_URL=redis://node0-redis:6379/0 \
    "$IMAGE" api

docker run -d --name node0-worker --network node0-net --restart unless-stopped \
    -p "${PORT_BASE}:${PORT_BASE}" \
    -e PARTY_ID=0 -e PARTY0_HOST="$HOST_IP" -e PORT_BASE=$PORT_BASE \
    -e REDIS_URL=redis://node0-redis:6379/0 \
    -v node0-player-data:/usr/src/MP-SPDZ/Player-Data \
    -v node0-logs:/usr/src/MP-SPDZ/node_logs \
    "$IMAGE" worker

docker run -d --name node1-redis --network node1-net --restart unless-stopped \
    -v node1-redis-data:/data \
    redis:7-alpine redis-server --save 60 1

docker run -d --name node1-api --network node1-net --restart unless-stopped \
    -p "${HTTP1_PORT}:8000" \
    -e PARTY_ID=1 -e PARTY0_HOST="$HOST_IP" -e PORT_BASE=$PORT_BASE -e HTTP_PORT=8000 \
    -e REDIS_URL=redis://node1-redis:6379/0 \
    "$IMAGE" api

docker run -d --name node1-worker --network node1-net --restart unless-stopped \
    -e PARTY_ID=1 -e PARTY0_HOST="$HOST_IP" -e PORT_BASE=$PORT_BASE \
    -e REDIS_URL=redis://node1-redis:6379/0 \
    -v node1-player-data:/usr/src/MP-SPDZ/Player-Data \
    -v node1-logs:/usr/src/MP-SPDZ/node_logs \
    "$IMAGE" worker

echo "=== waiting for certs to be generated ==="
sleep 3

echo "=== exchanging certificates (public .pem only, same manual step node/README.md documents -- automated here only because both are on one host we control) ==="
P0PEM=$(docker exec node0-worker cat /usr/src/MP-SPDZ/Player-Data/P0.pem)
P1PEM=$(docker exec node1-worker cat /usr/src/MP-SPDZ/Player-Data/P1.pem)
echo "$P1PEM" | docker exec -i node0-worker tee /usr/src/MP-SPDZ/Player-Data/P1.pem >/dev/null
echo "$P0PEM" | docker exec -i node1-worker tee /usr/src/MP-SPDZ/Player-Data/P0.pem >/dev/null
docker exec node0-worker bash -c "c_rehash Player-Data || openssl rehash Player-Data"
docker exec node1-worker bash -c "c_rehash Player-Data || openssl rehash Player-Data"

echo "=== done. node0 API: http://localhost:${HTTP0_PORT}  node1 API: http://localhost:${HTTP1_PORT} ==="
