"""Data classes shared by the collectors, filters and storage."""

from __future__ import annotations

from dataclasses import dataclass, field

KEEP = "keep"
DROP = "drop"
FLAG = "flag"


@dataclass
class PoolSnapshot:
    """One liquidity pool as seen by a collector at a point in time."""

    source: str  # "dexscreener" or a listener label such as "raydium_amm_v4"
    pair_address: str
    dex_id: str
    base_mint: str
    base_symbol: str = ""
    base_name: str = ""
    quote_mint: str = ""
    quote_symbol: str = ""
    program_id: str = ""
    price_usd: float | None = None
    liquidity_usd: float | None = None
    fdv_usd: float | None = None
    buys_h1: int | None = None
    sells_h1: int | None = None
    volume_h24_usd: float | None = None
    pair_created_at_ms: int | None = None
    signature: str = ""  # creating transaction, when self-collected
    url: str = ""


@dataclass
class MintInfo:
    """Token-mint facts read from chain (getAccountInfo, jsonParsed)."""

    mint: str
    token_program: str
    decimals: int
    supply: int
    mint_authority: str | None
    freeze_authority: str | None
    # Token-2022 extensions keyed by jsonParsed name, e.g. "transferFeeConfig".
    extensions: dict[str, dict] = field(default_factory=dict)
    # Percentage of supply held by the 10 largest non-pool holders, if measured.
    top10_holder_pct: float | None = None


@dataclass
class RuleHit:
    rule_id: str
    action: str  # DROP or FLAG
    reason_zh: str
    case_refs: tuple[str, ...] = ()
    weight: int = 0


@dataclass
class Decision:
    verdict: str  # KEEP or DROP
    risk_score: int
    hits: list[RuleHit]

    @property
    def dropped_by(self) -> list[str]:
        return [h.rule_id for h in self.hits if h.action == DROP]
