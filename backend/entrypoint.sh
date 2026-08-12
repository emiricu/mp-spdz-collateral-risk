#!/bin/sh
# Two listeners on the same FastAPI app: 9090 (HTTPS, self-signed cert,
# published to the host for the browser) and 9091 (plain HTTP, internal
# only -- never published to the host, reachable only from the two node
# Docker networks this container is attached to). Node callbacks target
# 9091: the nodes' own httpx/requests client does plain HTTP with no
# certificate-trust setup, and pointing it at 9090 fails outright (TLS
# handshake against a plaintext client looks like an immediate connection
# reset, not a clean error) -- verified directly, not assumed.
set -e
uvicorn app:app --host 0.0.0.0 --port 9091 &
exec uvicorn app:app --host 0.0.0.0 --port 9090 \
    --ssl-keyfile /certs/key.pem --ssl-certfile /certs/cert.pem
