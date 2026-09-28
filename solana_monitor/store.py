"""SQLite storage for collected pools, mint checks and filter decisions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .filters import RULES
from .models import Decision, MintInfo, PoolSnapshot
from .sources.pool_listener import NewPoolEvent

DEFAULT_MARKET_DB = Path(__file__).resolve().parent / "data" / "market.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    rule_id       TEXT PRIMARY KEY,
    stage         TEXT NOT NULL,
    action        TEXT NOT NULL,
    title_zh      TEXT NOT NULL,
    rationale_zh  TEXT NOT NULL,
    case_refs     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pool_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at         TEXT NOT NULL,
    source              TEXT NOT NULL,
    pair_address        TEXT NOT NULL,
    dex_id              TEXT NOT NULL,
    program_id          TEXT,
    base_mint           TEXT NOT NULL,
    base_symbol         TEXT,
    base_name           TEXT,
    quote_mint          TEXT,
    quote_symbol        TEXT,
    price_usd           REAL,
    liquidity_usd       REAL,
    fdv_usd             REAL,
    buys_h1             INTEGER,
    sells_h1            INTEGER,
    volume_h24_usd      REAL,
    pair_created_at_ms  INTEGER,
    signature           TEXT,
    url                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_mint ON pool_snapshots (base_mint);

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

CREATE TABLE IF NOT EXISTS decisions (
    snapshot_id  INTEGER PRIMARY KEY REFERENCES pool_snapshots (id),
    verdict      TEXT NOT NULL CHECK (verdict IN ('keep', 'drop')),
    risk_score   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_hits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  INTEGER NOT NULL REFERENCES pool_snapshots (id),
    rule_id      TEXT NOT NULL REFERENCES rules (rule_id),
    action       TEXT NOT NULL,
    reason_zh    TEXT NOT NULL,
    case_refs    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hits_snapshot ON rule_hits (snapshot_id);

CREATE TABLE IF NOT EXISTS new_pool_events (
    signature    TEXT PRIMARY KEY,
    dex          TEXT NOT NULL,
    program_id   TEXT NOT NULL,
    slot         INTEGER NOT NULL,
    base_mints   TEXT NOT NULL,
    quote_mints  TEXT NOT NULL,
    detected_at  TEXT NOT NULL
);

-- Pools that passed every filter, newest first.
CREATE VIEW IF NOT EXISTS v_kept AS
SELECT s.id, s.observed_at, s.dex_id, s.base_symbol, s.base_mint,
       s.liquidity_usd, d.risk_score,
       (SELECT group_concat(h.reason_zh, '；') FROM rule_hits h
         WHERE h.snapshot_id = s.id) AS flags,
       s.url
  FROM pool_snapshots s JOIN decisions d ON d.snapshot_id = s.id
 WHERE d.verdict = 'keep'
 ORDER BY s.id DESC;

-- What was filtered out, and why.
CREATE VIEW IF NOT EXISTS v_drop_reasons AS
SELECT r.stage, h.rule_id, r.title_zh, COUNT(DISTINCT h.snapshot_id) AS pools
  FROM rule_hits h JOIN rules r ON r.rule_id = h.rule_id
 WHERE h.action = 'drop'
 GROUP BY r.stage, h.rule_id, r.title_zh
 ORDER BY pools DESC;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MarketStore:
    def __init__(self, db_path: Path = DEFAULT_MARKET_DB):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.executemany(
            "INSERT OR REPLACE INTO rules VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    r.rule_id,
                    r.stage,
                    r.action,
                    r.title_zh,
                    r.rationale_zh,
                    ",".join(r.case_refs),
                )
                for r in RULES.values()
            ],
        )
        self.conn.commit()

    def seen_event(self, signature: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM new_pool_events WHERE signature = ?", (signature,)
        ).fetchone()
        return row is not None

    def save_event(self, event: NewPoolEvent, base: list[str], quote: list[str]):
        self.conn.execute(
            "INSERT OR IGNORE INTO new_pool_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.signature,
                event.dex,
                event.program_id,
                event.slot,
                ",".join(base),
                ",".join(quote),
                _now(),
            ),
        )
        self.conn.commit()

    def save(
        self, snap: PoolSnapshot, decision: Decision, mint: MintInfo | None = None
    ) -> int:
        row = asdict(snap)
        cols = ", ".join(["observed_at", *row])
        marks = ", ".join("?" for _ in range(len(row) + 1))
        cur = self.conn.execute(
            f"INSERT INTO pool_snapshots ({cols}) VALUES ({marks})",
            [_now(), *row.values()],
        )
        snapshot_id = cur.lastrowid
        if mint is not None:
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
        self.conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?)",
            (snapshot_id, decision.verdict, decision.risk_score),
        )
        self.conn.executemany(
            "INSERT INTO rule_hits (snapshot_id, rule_id, action, reason_zh, case_refs)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (snapshot_id, h.rule_id, h.action, h.reason_zh, ",".join(h.case_refs))
                for h in decision.hits
            ],
        )
        self.conn.commit()
        return snapshot_id
