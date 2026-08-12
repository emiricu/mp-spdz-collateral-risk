# Node image for the crypto-collateral-risk MPC network (mascot, field,
# production mode). Built FROM the repo's own top-level Dockerfile's
# "machine" target (see node/build.sh) -- no new MP-SPDZ build method
# introduced, this is the same multi-stage build already documented
# there, just invoked with machine=mascot-party.x.
#
# buildenv/machine already did `COPY . .` from the repo root, so
# Scripts/risk_data/, Scripts/crypto_risk_run.py, Scripts/mpc_bench.py,
# Programs/Source/crypto_collateral_risk.mpc and compile.py are already
# present in this image -- reused as-is, not duplicated here.
FROM mpspdz-mascot:local

WORKDIR /usr/src/MP-SPDZ

# Compile crypto_collateral_risk.mpc for this deployment's ONE fixed
# shape: domain=field, N=2 platforms, requester=party 0, mode=2
# (decide_production -- see the .mpc's own module docstring). Same
# program, same compile-time args already validated across every run in
# results_produzione/n2/ -- not touched, not recompiled per request.
# Naming matches Scripts/mpc_bench.py's compile_domain() convention
# (-o field prefixes the output so it can never collide with a
# different domain's compile of the same args).
RUN ./compile.py -o field crypto_collateral_risk 2 0 2

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
ENV MP_SPDZ_PROG=field-crypto_collateral_risk-2-0-2

# HTTP_PORT default (see node/app/config.py); MP-SPDZ's own ports
# (PORT_BASE.. PORT_BASE+1, see README) are opened dynamically by
# mascot-party.x, not something Docker EXPOSE can pin ahead of time to a
# single value -- documented in node/README.md instead.
EXPOSE 8000

ENTRYPOINT ["/usr/src/MP-SPDZ/entrypoint.sh"]
CMD ["api"]
