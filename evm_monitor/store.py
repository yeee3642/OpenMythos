"""EVM market store: the shared tables plus GoPlus token-security checks."""

from __future__ import annotations

import json
from pathlib import Path

from monitor_common.store import MarketStore as BaseMarketStore

from .filters import RULES
from .models import TokenSecurity

DATA_DIR = Path(__file__).resolve().parent / "data"


def default_db(chain_key: str) -> Path:
    return DATA_DIR / f"market_{chain_key}.db"


class MarketStore(BaseMarketStore):
    EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_checks (
    snapshot_id       INTEGER PRIMARY KEY REFERENCES pool_snapshots (id),
    token             TEXT NOT NULL,
    is_honeypot       INTEGER,
    buy_tax           REAL,
    sell_tax          REAL,
    is_open_source    INTEGER,
    owner_address     TEXT,
    lp_locked_pct     REAL,
    top10_holder_pct  REAL,
    raw_json          TEXT NOT NULL
);
"""

    def __init__(self, db_path: Path):
        super().__init__(db_path, RULES)

    def _save_check(self, snapshot_id: int, sec: TokenSecurity) -> None:
        self.conn.execute(
            "INSERT INTO token_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                sec.token,
                sec.is_honeypot,
                sec.buy_tax,
                sec.sell_tax,
                sec.is_open_source,
                sec.owner_address,
                sec.lp_locked_pct,
                sec.top10_holder_pct,
                json.dumps(sec.raw, ensure_ascii=False),
            ),
        )
