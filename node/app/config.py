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
# to coordinate startup" (verified via mascot-party.x --help) -- party 0
# never dials out to this value itself (it's the listener, bound on
# 0.0.0.0 regardless), so PEER_HOST is only load-bearing for party 1. See
# node/README.md.
PEER_HOST = os.environ.get('PEER_HOST', 'localhost')
if PARTY_ID == 1 and not os.environ.get('PEER_HOST'):
    raise RuntimeError(
        "PEER_HOST is required for PARTY_ID=1 -- must resolve to party "
        "0's reachable address (its public/private network hostname or "
        "IP when the two nodes run on separate machines, never "
        "'localhost' in that case)")

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
