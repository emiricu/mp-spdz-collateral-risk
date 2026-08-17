#!/usr/bin/env bash
# Persistent deployment of the N MPC nodes on THIS machine, using the
# same topology already validated in local-test/run_repeated_strangers.py
# (5/5 clean runs, CV 6.4-6.5%) and extended from N=2 to N=3: N Docker
# networks created independently and never connected to each other,
# PARTY0_HOST set to the host's real routable interface IP -- never a
# Docker service name, never localhost. This is NOT a real N-machine
# deployment (no second machine available), but it is the same code path
# and the same network mechanism real separate machines would use, run
# here because it's what's available.
#
# Every node's OWN MP-SPDZ port range is published 1:1 to the host, not
# just party 0's: verified directly against Networking/Player.cpp's
# PlainPlayer::setup_sockets that at N>=3 party 1 also accepts an
# inbound data-channel connection (from party 2, the next-higher party),
# not just party 0 -- publishing only party 0's port (which was
# sufficient and correct at N=2, where party 1 only ever dials out) would
# silently hang at N=3 the same way an unreachable PARTY0_HOST did
# before that bug was found and fixed. Each party's actual MP-SPDZ port
# is <request's port_base> + its own party id (Networking/Player.h's
# default_port).
#
# A whole RANGE, not one port: since node/README.md sec:9.5/9.6, the
# port_base is assigned per-request by the orchestrator (a ring buffer
# over PORT_POOL_SIZE slots, never a single fixed PORT_BASE) precisely
# so a fresh request can never share a port with a still-possibly-alive
# process from a different, unrelated earlier request. PORT_POOL_BASE/
# SIZE/STRIDE here must match backend/app.py's own constants exactly --
# every slot the orchestrator might ever hand out needs to already be
# published, since which slot a given request gets isn't known ahead of
# deploy time.
#
# Unlike local-test/ (ephemeral, torn down after each check),
# containers here run with --restart unless-stopped and named volumes,
# meant to stay up.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST_IP="${HOST_IP:?set HOST_IP to this machine real routable IP, e.g. HOST_IP=169.58.44.182}"
IMAGE=collateral-node:local
N_PLATFORMS=3
PORT_BASE=5000
PORT_POOL_BASE=20000
PORT_POOL_SIZE=50
PORT_POOL_STRIDE=10
HTTP_PORTS=(18100 18101 18102)

# All three nodes are squeezed onto this ONE physical host (see the
# "estranei" topology note above), so they can't all publish the same
# continuous host port range -- only one container can ever bind a
# given host port. But they don't need to: within any pool slot, party
# i's actual port is always port_base+i, and PORT_POOL_STRIDE (10) is
# bigger than N_PLATFORMS (3), so node0's possible ports (slot+0),
# node1's (slot+1), node2's (slot+2) are three DISJOINT sets across the
# whole pool -- each node only ever needs to publish its own N_PLATFORMS-th
# of the range, not the continuous whole. build_port_flags fills the
# global PORT_FLAGS array with one "-p" "PORT:PORT" pair per slot for
# party i's own fixed offset (an array, not a plain string, so port
# numbers are never at risk of word-splitting/quoting mistakes when
# passed to `docker run`).
build_port_flags() {
    local party_id=$1
    local slot port
    PORT_FLAGS=()
    for slot in $(seq 0 $((PORT_POOL_SIZE - 1))); do
        port=$((PORT_POOL_BASE + slot * PORT_POOL_STRIDE + party_id))
        PORT_FLAGS+=(-p "${port}:${port}")
    done
}

echo "=== deploying $N_PLATFORMS nodes, PARTY0_HOST=$HOST_IP ==="

last=$((N_PLATFORMS - 1))

for i in $(seq 0 $last); do
    for c in "node${i}-redis" "node${i}-api" "node${i}-worker"; do
        docker rm -f "$c" >/dev/null 2>&1 || true
    done
    docker network create "node${i}-net" >/dev/null 2>&1 || true
done

for i in $(seq 0 $last); do
    http_port="${HTTP_PORTS[$i]}"

    docker run -d --name "node${i}-redis" --network "node${i}-net" --restart unless-stopped \
        -v "node${i}-redis-data:/data" \
        redis:7-alpine redis-server --save 60 1

    docker run -d --name "node${i}-api" --network "node${i}-net" --restart unless-stopped \
        -p "${http_port}:8000" \
        -e PARTY_ID="$i" -e N_PLATFORMS="$N_PLATFORMS" -e PARTY0_HOST="$HOST_IP" \
        -e PORT_BASE=$PORT_BASE -e HTTP_PORT=8000 \
        -e REDIS_URL="redis://node${i}-redis:6379/0" \
        "$IMAGE" api

    build_port_flags "$i"
    docker run -d --name "node${i}-worker" --network "node${i}-net" --restart unless-stopped \
        "${PORT_FLAGS[@]}" \
        -e PARTY_ID="$i" -e N_PLATFORMS="$N_PLATFORMS" -e PARTY0_HOST="$HOST_IP" \
        -e PORT_BASE=$PORT_BASE \
        -e REDIS_URL="redis://node${i}-redis:6379/0" \
        -v "node${i}-player-data:/usr/src/MP-SPDZ/Player-Data" \
        -v "node${i}-logs:/usr/src/MP-SPDZ/node_logs" \
        "$IMAGE" worker
done

echo "=== waiting for certs to be generated ==="
sleep 3

echo "=== exchanging certificates (public .pem only, same manual step node/README.md documents -- automated here only because all nodes are on one host we control). N=$N_PLATFORMS means a full mesh: every node needs every OTHER node's cert, not just one peer's ==="
for i in $(seq 0 $last); do
    pem_var="P${i}PEM"
    declare "$pem_var=$(docker exec "node${i}-worker" cat "/usr/src/MP-SPDZ/Player-Data/P${i}.pem")"
done
for i in $(seq 0 $last); do
    for j in $(seq 0 $last); do
        [ "$i" = "$j" ] && continue
        pem_var="P${j}PEM"
        echo "${!pem_var}" | docker exec -i "node${i}-worker" tee "/usr/src/MP-SPDZ/Player-Data/P${j}.pem" >/dev/null
    done
    docker exec "node${i}-worker" bash -c "c_rehash Player-Data || openssl rehash Player-Data"
done

echo "=== done ==="
for i in $(seq 0 $last); do
    echo "node${i} API: http://localhost:${HTTP_PORTS[$i]}"
done
