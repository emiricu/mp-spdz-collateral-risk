# Node image for the crypto-collateral-risk MPC network. Dual protocol:
# mascot (field, SPDZ family, production) AND tinier (binary domain,
# garbled circuits, experimental production -- see node/README.md's
# tinier-integration section for the verification this dual support is
# based on). Built FROM the repo's own top-level Dockerfile's "machine"
# target (see node/build.sh) -- no new MP-SPDZ build method introduced
# for mascot-party.x, this is the same multi-stage build already
# documented there, just invoked with machine=mascot-party.x.
# tinier-party.x is NOT built by that base-image stage (it only builds
# the one --build-arg machine given to it) -- built explicitly below,
# reusing the OT/libOTe objects already compiled as mascot-party.x's own
# transitive dependency ($(SPDZ) -> $(TINIER) -> $(OT) in the top-level
# Makefile), verified directly to take ~1 minute, not a from-scratch
# libOTe rebuild.
#
# buildenv/machine already did `COPY . .` from the repo root, so
# Scripts/risk_data/, Scripts/crypto_risk_run.py, Scripts/mpc_bench.py,
# Programs/Source/crypto_collateral_risk*.mpc and compile.py are already
# present in this image -- reused as-is, not duplicated here.
FROM mpspdz-mascot:local

WORKDIR /usr/src/MP-SPDZ

# Compile crypto_collateral_risk.mpc for every N this image is asked to
# run: domain=field, requester=party 0, mode=2 (decide_production -- see
# the .mpc's own module docstring). Same source file, same family of
# compile-time args already validated across every run in
# results_produzione/n2/ and n3/ -- not touched, not recompiled per
# request. Both N=2 and N=3 are baked into this one image (selected at
# request time by node/app/config.py's PROTOCOLS mapping) rather than
# building a separate image per N -- there is nothing image-specific
# about which N a given container ends up running as. Naming matches
# Scripts/mpc_bench.py's compile_domain() convention (-o field prefixes
# the output so it can never collide with a different domain's compile
# of the same args).
RUN ./compile.py -o field crypto_collateral_risk 2 0 2
RUN ./compile.py -o field crypto_collateral_risk 3 0 2

# tinier-party.x: builds fast on top of mascot-party.x's already-built
# OT/libOTe objects (verified: ~1 minute, see comment above) -- not a
# second `make clean`, which would force a full libOTe rebuild.
RUN make tinier-party.x

# crypto_collateral_risk_binary_produzione_sperimentale.mpc, domain
# binary (-B 64), same N=2/N=3 pair as the field program above. Compiled
# with the EXACT same compile-time args already validated in
# results_binario_produzione_sperimentale/ (34/34 scenarios matching
# field) and results_tinier_vs_mascot/ (correctness confirmed again at
# N=2..5, timed): `<n> 0 0` -- the trailing 0 is appended by
# mpc_bench.compile_domain()'s own convention (mode=0, the only mode
# this program's compile-time arg parser is ever invoked with) and kept
# here for name-compatibility with node/app/config.py's PROTOCOLS
# mapping, not because the .mpc branches on it -- see the .mpc's own
# module docstring, it always runs the one production-composition path.
RUN ./compile.py -B 64 -o binary crypto_collateral_risk_binary_produzione_sperimentale 2 0 0
RUN ./compile.py -B 64 -o binary crypto_collateral_risk_binary_produzione_sperimentale 3 0 0

# openssl: needed at runtime to generate this party's own certificate for
# encrypted channels (-e) if one doesn't already exist on the mounted
# Player-Data volume -- see entrypoint.sh.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssl && rm -rf /var/lib/apt/lists/*

COPY node/requirements.txt /tmp/node-requirements.txt
RUN pip install --no-cache-dir -r /tmp/node-requirements.txt

# Explicit copy (not relying on the base image's earlier COPY . .) so
# rebuilding the node app alone doesn't require rebuilding mascot-party.x.
COPY node/app /usr/src/MP-SPDZ/node_app
COPY node/scripts /usr/src/MP-SPDZ/node_scripts
COPY node/entrypoint.sh /usr/src/MP-SPDZ/entrypoint.sh
RUN chmod +x /usr/src/MP-SPDZ/entrypoint.sh /usr/src/MP-SPDZ/node_scripts/*.sh

ENV PYTHONUNBUFFERED=1
ENV MP_SPDZ_HOME=/usr/src/MP-SPDZ
# No fixed program name set here: node/app/config.py's PROTOCOLS mapping
# derives the right one of the four programs compiled above (field/binary
# x N=2/N=3) from N_PLATFORMS (set per container by the deploy script)
# AND the per-REQUEST `protocol` field (mascot/tinier) -- selected fresh
# on every /calcola call, not pinned at container start.

# HTTP_PORT default (see node/app/config.py); MP-SPDZ's own ports
# (PORT_BASE + each party's own id, see README) are opened dynamically
# by mascot-party.x, not something Docker EXPOSE can pin ahead of time to
# a single value -- documented in node/README.md instead.
EXPOSE 8000

ENTRYPOINT ["/usr/src/MP-SPDZ/entrypoint.sh"]
CMD ["api"]
