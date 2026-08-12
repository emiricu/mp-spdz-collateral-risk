#!/usr/bin/env bash
# Builds the two images needed for a node:
#   1. mpspdz-mascot:local -- the repo's OWN top-level Dockerfile, target
#      "machine", building mascot-party.x. Not a new build method: this is
#      the exact multi-stage build already documented at the top of the
#      repo's Dockerfile, just invoked with machine=mascot-party.x (its
#      own default).
#   2. collateral-node:local -- node/Dockerfile, FROM mpspdz-mascot:local,
#      adds the compiled crypto_collateral_risk.mpc (field/N=2/mode=2) and
#      the API+worker Python app.
set -euo pipefail
cd "$(dirname "$0")/.."  # repo root

echo "=== 1/2: building mascot-party.x (upstream Dockerfile, target=machine) ==="
docker build --target machine --build-arg machine=mascot-party.x \
    -t mpspdz-mascot:local .

echo "=== 2/2: building collateral-node ==="
docker build -f node/Dockerfile -t collateral-node:local .

echo "done: collateral-node:local"
