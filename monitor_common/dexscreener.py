"""DexScreener public API collector (https://docs.dexscreener.com/api/reference)."""

from __future__ import annotations

from .http import HttpJsonClient
from .models import PoolSnapshot

BASE_URL = "https://api.dexscreener.com"
MAX_TOKENS_PER_CALL = 30


class DexScreenerClient:
    def __init__(self, http: HttpJsonClient | None = None):
        # Stay well under the documented per-minute limits.
        self.http = http or HttpJsonClient(min_interval_s=1.0)

    def latest_token_addresses(self, chain_id: str = "solana") -> list[str]:
        """Tokens that most recently published a DexScreener profile."""
        profiles = self.http.request(f"{BASE_URL}/token-profiles/latest/v1")
        seen: dict[str, None] = {}
        for profile in profiles or []:
            if profile.get("chainId") == chain_id and profile.get("tokenAddress"):
                seen[profile["tokenAddress"]] = None
        return list(seen)

    def pairs_for_tokens(self, tokens: list[str], chain_id="solana") -> list[dict]:
        pairs: list[dict] = []
        for i in range(0, len(tokens), MAX_TOKENS_PER_CALL):
            batch = ",".join(tokens[i : i + MAX_TOKENS_PER_CALL])
            resp = self.http.request(f"{BASE_URL}/tokens/v1/{chain_id}/{batch}")
            # The endpoint returns a list; older endpoints wrap it in {"pairs": [...]}.
            if isinstance(resp, dict):
                resp = resp.get("pairs")
            pairs.extend(resp or [])
        return pairs


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def pair_to_snapshot(pair: dict) -> PoolSnapshot:
    """Normalize one DexScreener pair object."""
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    txns_h1 = (pair.get("txns") or {}).get("h1") or {}
    return PoolSnapshot(
        source="dexscreener",
        pair_address=pair.get("pairAddress", ""),
        dex_id=pair.get("dexId", ""),
        base_mint=base.get("address", ""),
        base_symbol=base.get("symbol", ""),
        base_name=base.get("name", ""),
        quote_mint=quote.get("address", ""),
        quote_symbol=quote.get("symbol", ""),
        price_usd=_num(pair.get("priceUsd")),
        liquidity_usd=_num((pair.get("liquidity") or {}).get("usd")),
        fdv_usd=_num(pair.get("fdv")),
        buys_h1=txns_h1.get("buys"),
        sells_h1=txns_h1.get("sells"),
        volume_h24_usd=_num((pair.get("volume") or {}).get("h24")),
        pair_created_at_ms=pair.get("pairCreatedAt"),
        url=pair.get("url", ""),
    )
