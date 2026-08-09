"""Price data sources.

PriceOracle is the interface the rest of the pipeline codes against, so a
future Chainlink (on-chain) source can replace CoinGecko without touching
callers -- ChainlinkOracle below is a stub for exactly that swap.

Every oracle returns the same shape from history():
    {"date": "YYYY-MM-DD", "price_usd": float}  (oldest first)
so metrics.py never needs to know which source produced it.
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDC": "usd-coin",
    "USDT": "tether",
    "SOL": "solana",
}


class OracleError(RuntimeError):
    pass


class PriceOracle(ABC):
    """Provenance is mandatory on every reading: the .mpc program never
    sees it (it's not privacy-relevant data), but the final JSON output
    that gets signed on-chain does, so an auditor can tell which source
    and timestamp a decision was based on."""

    provenance = "abstract"

    @abstractmethod
    def spot(self, asset: str) -> float:
        ...

    @abstractmethod
    def history(self, asset: str, days: int) -> list:
        ...

    @abstractmethod
    def volume_24h(self, asset: str) -> float:
        ...


class FixtureOracle(PriceOracle):
    """Default source: registered JSON responses under fixtures/, for
    reproducible offline runs (and CI/benchmarking, where hitting a real
    API on every invocation would be slow and flaky)."""

    provenance = "fixture"

    def __init__(self, fixtures_dir=FIXTURES_DIR):
        self.fixtures_dir = Path(fixtures_dir)
        self._loaded = {}

    def _load(self, asset: str) -> dict:
        asset = asset.upper()
        if asset not in self._loaded:
            path = self.fixtures_dir / f"prices_{asset}.json"
            if not path.exists():
                raise OracleError(f"no fixture for asset {asset!r} at {path}")
            self._loaded[asset] = json.loads(path.read_text())
        return self._loaded[asset]

    def spot(self, asset: str) -> float:
        return self._load(asset)["spot_usd"]

    def history(self, asset: str, days: int) -> list:
        hist = self._load(asset)["history"]
        return hist[-days:]

    def volume_24h(self, asset: str) -> float:
        return self._load(asset)["volume_24h_usd"]


class CoinGeckoOracle(PriceOracle):
    """Live source. Works without a key on the free tier (verified); an
    optional demo/pro API key just raises the rate limit -- passed as the
    x-cg-demo-api-key header, never as a URL query param, so it doesn't
    end up in logs or cached request keys. Talks HTTP via stdlib urllib
    (no extra dependency for a handful of GET requests)."""

    provenance = "coingecko"
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, cache=None, timeout=10, api_key=None):
        self.cache = cache
        self.timeout = timeout
        self.api_key = api_key

    def _coin_id(self, asset: str) -> str:
        try:
            return COINGECKO_IDS[asset.upper()]
        except KeyError:
            raise OracleError(f"unknown asset {asset!r}, add it to COINGECKO_IDS")

    def _get(self, path: str, cache_key: str):
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached
        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(url)
        if self.api_key:
            req.add_header("x-cg-demo-api-key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise OracleError(f"CoinGecko request failed: {exc}") from exc
        if self.cache is not None:
            self.cache.set(cache_key, data)
        return data

    def spot(self, asset: str) -> float:
        coin_id = self._coin_id(asset)
        data = self._get(
            f"/simple/price?ids={coin_id}&vs_currencies=usd",
            cache_key=f"spot_{coin_id}",
        )
        return float(data[coin_id]["usd"])

    def history(self, asset: str, days: int) -> list:
        coin_id = self._coin_id(asset)
        data = self._get(
            f"/coins/{coin_id}/market_chart?vs_currency=usd&days={days}&interval=daily",
            cache_key=f"history_{coin_id}_{days}",
        )
        out = []
        for ts_ms, price in data["prices"]:
            date = __import__("datetime").datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            out.append({"date": date, "price_usd": float(price)})
        return out

    def volume_24h(self, asset: str) -> float:
        coin_id = self._coin_id(asset)
        data = self._get(
            f"/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_vol=true",
            cache_key=f"vol_{coin_id}",
        )
        return float(data[coin_id]["usd_24h_vol"])


class ChainlinkOracle(PriceOracle):
    """Stub for a future on-chain source (Chainlink price feeds). Not
    implemented: reading a feed needs an RPC endpoint and a feed-address
    registry per (asset, chain) pair, out of scope for this demo -- kept
    here so PriceOracle's interface is proven to support the swap without
    changing any caller."""

    provenance = "chainlink"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "ChainlinkOracle is a stub: wire up an RPC endpoint and a "
            "feed-address registry before using it."
        )

    def spot(self, asset: str) -> float:
        raise NotImplementedError

    def history(self, asset: str, days: int) -> list:
        raise NotImplementedError

    def volume_24h(self, asset: str) -> float:
        raise NotImplementedError
