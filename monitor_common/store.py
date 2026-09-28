"""SQLite storage shared by the pool monitors: snapshots, decisions, rule hits."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Decision, NewPoolEvent, PoolFacts, PoolSnapshot
from .rules import Rule

COMMON_SCHEMA = """
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
    url                 TEXT,
    creator             TEXT,
    initial_quote_amount REAL,
    creator_recent_pools INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_mint ON pool_snapshots (base_mint);

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
    creator      TEXT NOT NULL,
    base_mints   TEXT NOT NULL,
    quote_mints  TEXT NOT NULL,
    initial_quote_json TEXT NOT NULL,
    detected_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_creator ON new_pool_events (creator, detected_at);

-- Raw reports from third-party providers (e.g. AVE contract risk), archived
-- for later comparison with this tool's own decisions.
CREATE TABLE IF NOT EXISTS external_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    mint        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_mint ON external_reports (mint);

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
    """Base store; chain monitors subclass it to add a token-check table."""

    EXTRA_SCHEMA = ""

    def __init__(self, db_path: Path, rules: dict[str, Rule]):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(COMMON_SCHEMA + self.EXTRA_SCHEMA)
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
                for r in rules.values()
            ],
        )
        self.conn.commit()

    def seen_event(self, signature: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM new_pool_events WHERE signature = ?", (signature,)
        ).fetchone()
        return row is not None

    def creator_pool_count(self, creator: str, hours: int = 24) -> int:
        """Pools this wallet created within the last `hours` (as seen by us)."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
            timespec="seconds"
        )
        row = self.conn.execute(
            "SELECT COUNT(*) FROM new_pool_events WHERE creator = ? AND detected_at >= ?",
            (creator, since),
        ).fetchone()
        return row[0]

    def save_event(self, event: NewPoolEvent, facts: PoolFacts):
        self.conn.execute(
            "INSERT OR IGNORE INTO new_pool_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.signature,
                event.dex,
                event.program_id,
                event.slot,
                facts.creator,
                ",".join(facts.base_mints),
                ",".join(facts.quote_mints),
                json.dumps(facts.initial_quote),
                _now(),
            ),
        )
        self.conn.commit()

    def save_external_report(self, provider: str, kind: str, mint: str, payload):
        self.conn.execute(
            "INSERT INTO external_reports (provider, kind, mint, fetched_at, payload)"
            " VALUES (?, ?, ?, ?, ?)",
            (provider, kind, mint, _now(), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def _save_check(self, snapshot_id: int, check) -> None:
        """Store a chain-specific token check; implemented by subclasses."""
        raise NotImplementedError

    def save(self, snap: PoolSnapshot, decision: Decision, check=None) -> int:
        row = asdict(snap)
        cols = ", ".join(["observed_at", *row])
        marks = ", ".join("?" for _ in range(len(row) + 1))
        cur = self.conn.execute(
            f"INSERT INTO pool_snapshots ({cols}) VALUES ({marks})",
            [_now(), *row.values()],
        )
        snapshot_id = cur.lastrowid
        if check is not None:
            self._save_check(snapshot_id, check)
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
