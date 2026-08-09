"""Per-platform key management and signing for the on-chain publish step
(see crypto_risk_run.py's build_onchain_payload). Real ECDSA over
secp256k1 -- the same curve Ethereum and Bitcoin use -- via the
`cryptography` library. This is single-machine plaintext-key management
for a demo (each platform's private key lives in a local PEM file), not
production custody: a real deployment would keep each platform's key in
its own HSM/KMS, never co-located with the other N-1 platforms' keys the
way this repo's Scripts/risk_data/keys/ directory does for convenience.
"""

import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

KEYS_DIR = Path(__file__).parent / 'keys'
CURVE = ec.SECP256K1()


def _private_key_path(platform_id: int) -> Path:
    return KEYS_DIR / f'platform_{platform_id}.pem'


def _public_key_path(platform_id: int) -> Path:
    return KEYS_DIR / f'platform_{platform_id}.pub.pem'


def get_or_create_keypair(platform_id: int):
    """Loads the platform's persisted keypair, generating one on first
    use. Never regenerates an existing key: a platform's identity (and
    every past signature's verifiability against its public key) must
    stay stable across runs -- unlike the fixture/derived data elsewhere
    in this demo, which is fine to recompute freely."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = _private_key_path(platform_id)
    if priv_path.exists():
        return serialization.load_pem_private_key(priv_path.read_bytes(), password=None)

    private_key = ec.generate_private_key(CURVE)
    priv_path.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    priv_path.chmod(0o600)
    _public_key_path(platform_id).write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    return private_key


def public_key_hex(platform_id: int) -> str:
    """Uncompressed SEC1 public key point, hex-encoded -- what a
    counterparty needs to verify this platform's signatures."""
    private_key = get_or_create_keypair(platform_id)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint).hex()


def canonical_bytes(payload: dict) -> bytes:
    """Deterministic serialization: sorted keys, no whitespace. Signing
    (and later verifying) must hash the exact same bytes every time, or
    two honest platforms computing the identical decision would produce
    payload encodings that don't match, and signatures that don't verify
    against each other's copy."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()


def sign_payload(platform_id: int, payload: dict) -> str:
    """Signs SHA-256(canonical_bytes(payload)) with platform_id's private
    key (generating the key on first use). Returns the DER-encoded ECDSA
    signature as a hex string (JSON-friendly)."""
    private_key = get_or_create_keypair(platform_id)
    signature = private_key.sign(canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
    return signature.hex()


def verify_signature(platform_id: int, payload: dict, signature_hex: str) -> bool:
    """Checks signature_hex against platform_id's own public key for this
    exact payload. Returns False (never raises) on a bad signature or a
    malformed hex string, so callers can use it as a plain boolean check."""
    private_key = get_or_create_keypair(platform_id)
    try:
        private_key.public_key().verify(
            bytes.fromhex(signature_hex), canonical_bytes(payload),
            ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def fingerprint(platform_id: int) -> str:
    """Short, human-checkable identifier for a platform's public key
    (SHA-256 of the uncompressed point, first 16 hex chars) -- handy for
    logging/display without dumping the full key."""
    return hashlib.sha256(bytes.fromhex(public_key_hex(platform_id))).hexdigest()[:16]
