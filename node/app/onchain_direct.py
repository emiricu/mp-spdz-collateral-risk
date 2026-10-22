"""Direct-to-chain commit/reveal submission for the thesis demo: this
module is the concrete replacement for the old /firma endpoint's
signature-for-relay pattern. There is no publisher/backend relay here --
this node builds, signs (with its OWN persisted secp256k1 key), and
broadcasts its commit and reveal transactions straight to
SolvencyRegistry on Sepolia via plain JSON-RPC. No web3.py dependency:
just eth_account for signing and `requests` for the RPC call, matching
this repo's existing minimal-dependency style (see requirements.txt).

Security note this module exists to preserve: signing here always follows
the SAME defense-in-depth check the old /firma had -- a node only signs
(now: submits) an esito that ITS OWN locally-run MPC job actually
produced (see node_app/main.py's /commit and /reveal handlers, which read
that from `store` before ever calling into this module). This module
itself is deliberately "dumb" about that policy -- it only knows how to
sign and send, the caller decides what's safe to send.
"""
import logging
import time

import requests
from eth_account import Account
from eth_utils import keccak

from . import config

log = logging.getLogger('node.onchain_direct')

ESITO_TO_CODE = {'RIFIUTATO': 0, 'REVIEW MANUALE': 1, 'APPROVATO': 2}

_SELECTOR_SUBMIT_COMMIT = bytes.fromhex('65d0b563')
_SELECTOR_SUBMIT_REVEAL = bytes.fromhex('2d4a5c91')


def _load_private_key() -> str:
    with open(config.NODE_PRIVATE_KEY_PATH, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    return raw if raw.startswith('0x') else '0x' + raw


_PRIVATE_KEY = None
_ACCOUNT = None


def _account():
    global _PRIVATE_KEY, _ACCOUNT
    if _ACCOUNT is None:
        _PRIVATE_KEY = _load_private_key()
        _ACCOUNT = Account.from_key(_PRIVATE_KEY)
    return _ACCOUNT


def address() -> str:
    return _account().address


def _rpc(method: str, params: list):
    resp = requests.post(
        config.SEPOLIA_RPC_URL,
        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if 'error' in body:
        raise RuntimeError(f'RPC {method} failed: {body["error"]}')
    return body['result']


def _int(hex_str: str) -> int:
    return int(hex_str, 16)


def _build_and_send(data: bytes) -> str:
    """Signs and broadcasts a plain legacy transaction calling
    SolvencyRegistry with `data`, waits for it to be mined, and returns
    the transaction hash. Raises if the transaction reverted -- a
    reverted commit/reveal must never be silently swallowed here, the
    caller (node_app/main.py) needs to see it fail."""
    acct = _account()
    nonce = _int(_rpc('eth_getTransactionCount', [acct.address, 'pending']))
    gas_price = _int(_rpc('eth_gasPrice', []))
    # A generous fixed multiplier and gas limit rather than eth_estimateGas:
    # this repo's own Solidity deploy tooling hit unreliable gas estimation
    # under real network conditions for CREATE2-heavy transactions (see
    # docs/solvency-mpc-integration.md's Sepolia deploy notes) -- a plain
    # state-changing call here is far smaller and simpler, but the same
    # public-RPC unpredictability applies, so headroom is cheap insurance.
    gas_price = int(gas_price * 1.3)

    tx = {
        'nonce': nonce,
        'gasPrice': gas_price,
        'gas': 300_000,
        'to': config.SOLVENCY_REGISTRY_ADDRESS,
        'value': 0,
        'data': '0x' + data.hex(),
        'chainId': config.CHAIN_ID,
    }
    signed = acct.sign_transaction(tx)
    tx_hash = _rpc('eth_sendRawTransaction', ['0x' + signed.raw_transaction.hex()])
    log.info('sent tx %s (nonce=%s)', tx_hash, nonce)

    receipt = _wait_for_receipt(tx_hash)
    if _int(receipt['status']) != 1:
        raise RuntimeError(f'transaction {tx_hash} reverted on-chain')
    return tx_hash


def _wait_for_receipt(tx_hash: str, timeout_seconds: float = 180.0, poll_interval: float = 3.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        receipt = _rpc('eth_getTransactionReceipt', [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(poll_interval)
    raise TimeoutError(f'transaction {tx_hash} not mined within {timeout_seconds}s')


def _encode_bytes32(value: bytes) -> bytes:
    assert len(value) == 32
    return value


def _encode_uint256(value: int) -> bytes:
    return value.to_bytes(32, 'big')


def _encode_uint8(value: int) -> bytes:
    return value.to_bytes(32, 'big')


def compute_commit_hash(session_id: bytes, esito_code: int, policy_version: int,
                          timestamp_unix: int, salt: bytes) -> bytes:
    """Must match SolvencyRegistry.submitReveal's own recomputation exactly:
    keccak256(abi.encode(sessionId, esito, policyVersion, timestamp, salt)).
    abi.encode pads every argument to its own 32-byte slot (no packing),
    so this is a plain concatenation of five 32-byte words."""
    encoded = (
        _encode_bytes32(session_id)
        + _encode_uint8(esito_code)
        + _encode_uint256(policy_version)
        + _encode_uint256(timestamp_unix)
        + _encode_bytes32(salt)
    )
    return keccak(encoded)


def submit_commit(session_id: bytes, commit_hash: bytes) -> str:
    data = _SELECTOR_SUBMIT_COMMIT + _encode_bytes32(session_id) + _encode_bytes32(commit_hash)
    return _build_and_send(data)


def submit_reveal(session_id: bytes, esito_code: int, policy_version: int,
                    timestamp_unix: int, salt: bytes) -> str:
    data = (
        _SELECTOR_SUBMIT_REVEAL
        + _encode_bytes32(session_id)
        + _encode_uint8(esito_code)
        + _encode_uint256(policy_version)
        + _encode_uint256(timestamp_unix)
        + _encode_bytes32(salt)
    )
    return _build_and_send(data)
