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


PARTY_ID = _require_int('PARTY_ID')
if PARTY_ID not in (0, 1):
    raise RuntimeError(f'PARTY_ID must be 0 or 1, got {PARTY_ID!r} -- this '
                        f'deployment is fixed at N=2 platforms')
REQUESTER = 0  # party 0 is always the requesting platform (compiled into
               # the program as `crypto_collateral_risk 2 0 2`)
IS_REQUESTER = PARTY_ID == REQUESTER

# -h/--hostname passed to mascot-party.x means "host where party 0 runs
# to coordinate startup". Verified directly (not just from --help) that
# this is required on BOTH parties, including party 0 itself: even party
# 0 connects to this address AS A CLIENT to register with its own
# just-started coordination server (Networking/Server.cpp's
# start_networking -> Names::setup_names -> set_up_client_socket), and
# the IP getsockname() reports on THAT connection is what gets announced
# to the other party for the real data-channel connection afterwards.
# Passing 'localhost' for party 0 makes it announce 127.0.0.1 -- fine on
# one host, unreachable from a genuinely separate machine or container,
# and the run hangs forever after the initial handshake with no error
# message (reproduced and fixed empirically, not assumed from docs).
# So PARTY0_HOST must be party 0's own externally-reachable address, set
# on BOTH machines -- on party 0's machine it's "myself" (its own
# hostname/IP as seen by party 1), on party 1's machine it's "party 0".
PARTY0_HOST = os.environ.get('PARTY0_HOST')
if not PARTY0_HOST:
    raise RuntimeError(
        "PARTY0_HOST is required on BOTH nodes -- party 0's own "
        "externally-reachable hostname/IP (never 'localhost' once the "
        "two nodes are on separate machines or containers, including on "
        "party 0's own machine -- see node/README.md)")

PORT_BASE = _require_int('PORT_BASE', default=5000)
HTTP_PORT = _require_int('HTTP_PORT', default=8000)
POLICY_VERSION = _require_int('POLICY_VERSION', default=1)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

MP_SPDZ_HOME = os.environ.get('MP_SPDZ_HOME', '/usr/src/MP-SPDZ')
PROG_NAME = os.environ.get('MP_SPDZ_PROG', 'field-crypto_collateral_risk-2-0-2')
N_PLATFORMS = 2

# MPC run timeout: results_produzione/n2/mascot.json (the exact scenario
# this deployment mirrors) measured well under 100s; the controlled
# re-test (results_retest_controllato/) saw mascot@N2 production up to
# ~67s across repetitions in a noisy shared environment -- 600s leaves
# generous headroom without letting a hung run block the single-worker
# queue indefinitely.
MPC_TIMEOUT_SECONDS = _require_int('MPC_TIMEOUT_SECONDS', default=600)

CALLBACK_MAX_RETRIES = _require_int('CALLBACK_MAX_RETRIES', default=6)
CALLBACK_BACKOFF_BASE_SECONDS = float(os.environ.get('CALLBACK_BACKOFF_BASE_SECONDS', '2'))
CALLBACK_TIMEOUT_SECONDS = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '10'))

JOB_TTL_SECONDS = _require_int('JOB_TTL_SECONDS', default=24 * 3600)

CACHE_DIR = os.environ.get('PRICE_CACHE_DIR', '/tmp/crypto_risk_run_cache')
