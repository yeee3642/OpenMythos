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
    # Launch facts, only known when the pool creation was seen by the listener.
    creator: str = ""  # fee payer of the pool-creation transaction
    initial_quote_amount: float | None = None  # quote tokens deposited at creation
    creator_recent_pools: int | None = None  # pools this creator opened in 24h


@dataclass
class PoolFacts:
    """What a pool-creation transaction tells us, without per-DEX decoding."""

    creator: str
    base_mints: list[str]
    quote_mints: list[str]
    # Quote-token amount (UI units) held outside the creator's own accounts
    # after the transaction, i.e. the liquidity deposited into the pool.
    initial_quote: dict[str, float] = field(default_factory=dict)


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
