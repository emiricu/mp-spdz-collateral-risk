"""On-chain wallet history analysis.

WalletAnalyzer looks up raw on-chain facts about an address (first seen,
transaction count, counterparties, balances). AddressBook turns
counterparties into a risk signal (does this wallet talk to a known
KYC'd exchange, a known mixer, or neither?). wallet_risk_signals()
combines both into the two numbers crypto_collateral_risk.mpc actually
consumes: wallet_age_days and wallet_flags.
"""

import json
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# EVM chain -> Etherscan-family API base URL. Etherscan's V2 API is
# multi-chain via a `chainid` query param; kept as a dict here so adding a
# chain is a one-line change, not a new class.
ETHERSCAN_CHAIN_IDS = {
    "ethereum": 1,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
}


class WalletError(RuntimeError):
    pass


class WalletProfile:
    """Raw facts about one address, source-agnostic."""

    def __init__(self, address, first_tx_timestamp, tx_count, counterparties,
                 token_balances):
        self.address = address
        self.first_tx_timestamp = first_tx_timestamp  # unix seconds, or None
        self.tx_count = tx_count
        self.counterparties = counterparties  # list of address strings
        self.token_balances = token_balances  # dict symbol -> float

    def age_days(self, now=None) -> int:
        if self.first_tx_timestamp is None:
            return 0
        now = now if now is not None else time.time()
        return max(0, int((now - self.first_tx_timestamp) // 86400))


class WalletAnalyzer(ABC):
    provenance = "abstract"

    @abstractmethod
    def analyze(self, address: str) -> WalletProfile:
        ...


class FixtureWalletAnalyzer(WalletAnalyzer):
    """Default source: registered profiles under fixtures/, keyed by
    lowercased address, for reproducible offline runs."""

    provenance = "fixture"

    def __init__(self, fixtures_dir=FIXTURES_DIR):
        self.fixtures_dir = Path(fixtures_dir)

    def analyze(self, address: str) -> WalletProfile:
        path = self.fixtures_dir / f"wallet_{address.lower()}.json"
        if not path.exists():
            raise WalletError(f"no fixture for wallet {address!r} at {path}")
        data = json.loads(path.read_text())
        return WalletProfile(
            address=address,
            first_tx_timestamp=data["first_tx_timestamp"],
            tx_count=data["tx_count"],
            counterparties=data["counterparties"],
            token_balances=data["token_balances"],
        )


class EvmWalletAnalyzer(WalletAnalyzer):
    """Live source: Etherscan-family API. `chain` selects the chain id
    (see ETHERSCAN_CHAIN_IDS); `api_key` is required by Etherscan for
    anything beyond a trickle of requests."""

    provenance = "etherscan"

    def __init__(self, chain="ethereum", api_key=None, timeout=10,
                 max_counterparties=50):
        if chain not in ETHERSCAN_CHAIN_IDS:
            raise WalletError(f"unknown chain {chain!r}, add it to ETHERSCAN_CHAIN_IDS")
        if not api_key:
            raise WalletError(
                "EvmWalletAnalyzer requires an Etherscan API key -- the "
                "no-key free tier this was originally written against no "
                "longer works (Etherscan's V2 API now returns "
                "'Missing/Invalid API Key' for every request without one). "
                "Get a free key at https://etherscan.io/apis and pass it as "
                "api_key=... (or set it via whatever env var your caller "
                "reads -- this class does not read one itself).")
        self.chain = chain
        self.chain_id = ETHERSCAN_CHAIN_IDS[chain]
        self.api_key = api_key
        self.timeout = timeout
        self.max_counterparties = max_counterparties

    def _get(self, params: dict):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"https://api.etherscan.io/v2/api?chainid={self.chain_id}&{query}&apikey={self.api_key}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise WalletError(f"Etherscan request failed: {exc}") from exc
        # Etherscan reports errors with status='0' and a string in
        # "result" (e.g. "Missing/Invalid API Key", or "No transactions
        # found" for a real-but-quiet address) instead of an HTTP error
        # code -- indexing into that string as if it were the normal
        # list-of-transactions shape used to raise a confusing
        # TypeError deep in analyze() instead of a clear message here.
        if data.get("status") == "0" and data.get("message") != "No transactions found":
            raise WalletError(f"Etherscan API error: {data.get('result')}")
        return data

    def analyze(self, address: str) -> WalletProfile:
        data = self._get({
            "module": "account", "action": "txlist", "address": address,
            "startblock": 0, "endblock": 99999999, "sort": "asc",
        })
        txs = data.get("result") or []
        if not txs or isinstance(txs, str):
            return WalletProfile(address, None, 0, [], {})

        first_ts = int(txs[0]["timeStamp"])
        counterparties = []
        seen = set()
        for tx in txs:
            other = tx["to"] if tx["from"].lower() == address.lower() else tx["from"]
            if other and other.lower() not in seen:
                seen.add(other.lower())
                counterparties.append(other)
            if len(counterparties) >= self.max_counterparties:
                break

        return WalletProfile(
            address=address,
            first_tx_timestamp=first_ts,
            tx_count=len(txs),
            counterparties=counterparties,
            token_balances={},  # balances need a separate endpoint; not
                                 # needed for the risk signals used here
        )


class AddressBook:
    """Classifies counterparties as known-KYC-exchange, known-mixer, or
    unknown. Backed by plain address lists -- in a real deployment these
    would come from a maintained feed (e.g. Chainalysis/TRM lists), but
    the shape of the check doesn't change."""

    def __init__(self, kyc_exchanges=(), mixers=()):
        self.kyc_exchanges = {a.lower() for a in kyc_exchanges}
        self.mixers = {a.lower() for a in mixers}

    @classmethod
    def from_fixture(cls, path=None):
        path = Path(path) if path else FIXTURES_DIR / "address_book.json"
        data = json.loads(path.read_text())
        return cls(kyc_exchanges=data["kyc_exchanges"], mixers=data["mixers"])

    def classify(self, address: str) -> str:
        a = address.lower()
        if a in self.mixers:
            return "mixer"
        if a in self.kyc_exchanges:
            return "kyc_exchange"
        return "unknown"

    def has_mixer_interaction(self, counterparties) -> bool:
        return any(self.classify(a) == "mixer" for a in counterparties)

    def has_kyc_exchange_interaction(self, counterparties) -> bool:
        return any(self.classify(a) == "kyc_exchange" for a in counterparties)


def wallet_risk_signals(profile: WalletProfile, address_book: AddressBook,
                         dormant_gap_days=180, now=None) -> dict:
    """Reduces raw on-chain facts to the two numbers the .mpc program
    reads: wallet_age_days and wallet_flags (count of red flags, 0=clean).

    Flags, in order of severity:
      - mixer interaction: weight 2 on its own (decisive AML signal,
        pushes flags to >=2 = "flags_bad" in the .mpc regardless of
        anything else)
      - no KYC-exchange interaction ever: weight 1 (weak signal alone,
        many legitimate wallets never touch a KYC'd exchange)
      - dormant-then-reactivated pattern: weight 1 (proxy: fewer than 2
        transactions despite an old first-seen date, i.e. it sat idle
        for most of its life and just woke up -- cheap heuristic without
        needing full tx-timestamp history)
    """
    age_days = profile.age_days(now=now)
    flags = 0
    if address_book.has_mixer_interaction(profile.counterparties):
        flags += 2
    if not address_book.has_kyc_exchange_interaction(profile.counterparties):
        flags += 1
    if age_days > dormant_gap_days and profile.tx_count < 2:
        flags += 1
    return {"wallet_age_days": age_days, "wallet_flags": flags}
