"""Environment-driven configuration -- no hardcoded party/network identity,
see node/README.md for what to set on each machine."""
import os


def _require_int(name, default=None):
    val = os.environ.get(name)
    if val is None:
        if default is None:
            raise RuntimeError(f'{name} is required')
        return default
    return int(val)


N_PLATFORMS = _require_int('N_PLATFORMS', default=2)

PARTY_ID = _require_int('PARTY_ID')
if PARTY_ID not in range(N_PLATFORMS):
    raise RuntimeError(f'PARTY_ID must be in 0..{N_PLATFORMS - 1} for '
                        f'N_PLATFORMS={N_PLATFORMS}, got {PARTY_ID!r}')
REQUESTER = 0  # party 0 is always the requesting platform (compiled into
               # the program as `crypto_collateral_risk N_PLATFORMS 0 2`)
IS_REQUESTER = PARTY_ID == REQUESTER

# -h/--hostname passed to mascot-party.x means "host where party 0 runs
# to coordinate startup". Verified directly (not just from --help) that
# this is required on EVERY party, including party 0 itself: even party
# 0 connects to this address AS A CLIENT to register with its own
# just-started coordination server (Networking/Server.cpp's
# start_networking -> Names::setup_names -> set_up_client_socket), and
# the IP getsockname() reports on THAT connection is what gets announced
# to every OTHER party for the real data-channel connections afterwards
# (Networking/Player.cpp's PlainPlayer::setup_sockets: each party dials
# out to every lower-or-equal-numbered party and accepts connections
# from every higher-or-equal-numbered one -- at N=3 this means party 1's
# own port must ALSO be reachable, by party 2, not just party 0's).
# Passing 'localhost' for party 0 makes it announce 127.0.0.1 -- fine on
# one host, unreachable from a genuinely separate machine or container,
# and the run hangs forever after the initial handshake with no error
# message (reproduced and fixed empirically, not assumed from docs).
# So PARTY0_HOST must be party 0's own externally-reachable address, set
# on EVERY node -- on party 0's own machine it's "myself" (its own
# hostname/IP as seen by the other parties), on every other machine it's
# "wherever party 0 is".
PARTY0_HOST = os.environ.get('PARTY0_HOST')
if not PARTY0_HOST:
    raise RuntimeError(
        "PARTY0_HOST is required on EVERY node -- party 0's own "
        "externally-reachable hostname/IP (never 'localhost' once nodes "
        "are on separate machines or containers, including on party 0's "
        "own machine -- see node/README.md)")

# No longer used to pick the actual MP-SPDZ port for a run -- that's
# now per-request (orchestrator-assigned, see node/app/main.py's
# CalcolaRequest.port_base and mpc_runner.run_party), specifically to
# stop every request sharing the same fixed port (node/README.md
# sec:9.5/9.6). Kept only because node/docker-compose.yml's own
# 2-machine template (a separate, less-used deployment path than
# deploy/deploy_nodes.sh) still reads it for its port-publish range.
PORT_BASE = _require_int('PORT_BASE', default=5000)
HTTP_PORT = _require_int('HTTP_PORT', default=8000)
POLICY_VERSION = _require_int('POLICY_VERSION', default=1)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

MP_SPDZ_HOME = os.environ.get('MP_SPDZ_HOME', '/usr/src/MP-SPDZ')

# Dual protocol support (node/README.md's tinier-integration section):
# which binary + compiled program a request uses is now chosen per
# REQUEST (the `protocol` field on POST /calcola), never a single
# node-wide static default -- the MPC protocol requires all N parties to
# run the identical program for a given request, but different requests
# to the same node, one after another, can legitimately use different
# protocols (see mpc_runner.run_party). Both entries derived from
# N_PLATFORMS/REQUESTER by the same naming convention node/Dockerfile's
# compile.py invocations use, so a deployment that only sets N_PLATFORMS
# can't silently end up pointing at a program that was never compiled.
PROTOCOLS = {
    'mascot': dict(
        binary='mascot-party.x',
        prog_name=f'field-crypto_collateral_risk-{N_PLATFORMS}-{REQUESTER}-2',
    ),
    'tinier': dict(
        binary='tinier-party.x',
        prog_name=f'binary-crypto_collateral_risk_binary_produzione_sperimentale-{N_PLATFORMS}-{REQUESTER}-0',
    ),
}

# MPC run timeout: results_produzione/n2/mascot.json (N=2) measured well
# under 100s, results_produzione/n3/ (N=3) under 5s for most protocols;
# the controlled re-test (results_retest_controllato/) saw mascot@N2
# production up to ~67s across repetitions in a noisy shared environment
# -- 600s leaves generous headroom regardless of N without letting a hung
# run block the single-worker queue indefinitely.
MPC_TIMEOUT_SECONDS = _require_int('MPC_TIMEOUT_SECONDS', default=600)

CALLBACK_MAX_RETRIES = _require_int('CALLBACK_MAX_RETRIES', default=6)
CALLBACK_BACKOFF_BASE_SECONDS = float(os.environ.get('CALLBACK_BACKOFF_BASE_SECONDS', '2'))
CALLBACK_TIMEOUT_SECONDS = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '10'))

JOB_TTL_SECONDS = _require_int('JOB_TTL_SECONDS', default=24 * 3600)

CACHE_DIR = os.environ.get('PRICE_CACHE_DIR', '/tmp/crypto_risk_run_cache')

# --- Thesis demo: direct on-chain commit/reveal (replaces the old /firma
# signature-for-relay endpoint, see onchain_direct.py) ---
SEPOLIA_RPC_URL = os.environ.get('SEPOLIA_RPC_URL', 'https://ethereum-sepolia-rpc.publicnode.com')
CHAIN_ID = _require_int('CHAIN_ID', default=11155111)
SOLVENCY_REGISTRY_ADDRESS = os.environ.get('SOLVENCY_REGISTRY_ADDRESS')
NODE_PRIVATE_KEY_PATH = os.environ.get('NODE_PRIVATE_KEY_PATH', '/run/secrets/node_key')
