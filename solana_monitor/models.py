"""Solana-specific data classes; shared ones live in monitor_common.models."""

from __future__ import annotations

from dataclasses import dataclass, field

from monitor_common.models import (  # noqa: F401  (re-exported for callers)
    DROP,
    FLAG,
    KEEP,
    Decision,
    NewPoolEvent,
    PoolFacts,
    PoolSnapshot,
    RuleHit,
)


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
