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
# cxx=g++ overrides the Dockerfile's own default (clang++-11, from the
# bullseye base image): that clang is too old for this codebase's C++20
# <barrier> header (verified: build fails with "fatal error: 'barrier'
# file not found" using the untouched default). g++ is what every other
# build in this repo already uses (the host's own g++ 13 compiles
# mascot-party.x fine outside Docker) -- this is the existing
# --build-arg cxx=... flexibility the Dockerfile already documents, not
# a new build method.
docker build --target machine --build-arg machine=mascot-party.x \
    --build-arg cxx=g++ \
    -t mpspdz-mascot:local .

echo "=== 2/2: building collateral-node ==="
docker build -f node/Dockerfile -t collateral-node:local .

echo "done: collateral-node:local"
