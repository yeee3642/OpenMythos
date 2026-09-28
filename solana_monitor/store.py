"""Solana market store: the shared tables plus on-chain mint checks."""

from __future__ import annotations

import json
from pathlib import Path

from monitor_common.store import MarketStore as BaseMarketStore

from .filters import RULES
from .models import MintInfo

DEFAULT_MARKET_DB = Path(__file__).resolve().parent / "data" / "market.db"


class MarketStore(BaseMarketStore):
    EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS mint_checks (
    snapshot_id       INTEGER PRIMARY KEY REFERENCES pool_snapshots (id),
    mint              TEXT NOT NULL,
    token_program     TEXT NOT NULL,
    decimals          INTEGER NOT NULL,
    supply            TEXT NOT NULL,
    mint_authority    TEXT,
    freeze_authority  TEXT,
    extensions_json   TEXT NOT NULL,
    top10_holder_pct  REAL
);
"""

    def __init__(self, db_path: Path = DEFAULT_MARKET_DB):
        super().__init__(db_path, RULES)

    def _save_check(self, snapshot_id: int, mint: MintInfo) -> None:
        self.conn.execute(
            "INSERT INTO mint_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                mint.mint,
                mint.token_program,
                mint.decimals,
                str(mint.supply),
                mint.mint_authority,
                mint.freeze_authority,
                json.dumps(mint.extensions, ensure_ascii=False),
                mint.top10_holder_pct,
            ),
        )
