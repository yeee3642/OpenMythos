"""Read-only access to the Solana hack case library (solana_hacks/solana_hacks.db)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_CASE_DB = (
    Path(__file__).resolve().parent.parent / "solana_hacks" / "solana_hacks.db"
)

# Incident categories that reflect on the safety of the protocol itself.
PROTOCOL_RISK_CATEGORIES = {
    "smart_contract_bug",
    "oracle_price_manipulation",
    "economic_exploit",
    "private_key_compromise",
    "insider_threat",
    "governance_attack",
    "third_party_compromise",
    "supply_chain",
    "protocol_vulnerability",
}


class CaseLibrary:
    def __init__(self, db_path: Path = DEFAULT_CASE_DB):
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"{db_path} not found; run `python -m hack_db` first"
            )
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def case_ids(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT id FROM incidents")}

    def recent_incidents(
        self, projects: tuple[str, ...], as_of: date, days: int
    ) -> list[tuple[str, str, str]]:
        """(id, date, project) of protocol-level incidents within `days`.

        Only categories that say something about the protocol's own safety are
        counted; e.g. a team falling for an OTC scam or a hijacked X account
        does not make the protocol's pools riskier.
        """
        if not projects:
            return []
        since = (as_of - timedelta(days=days)).isoformat()
        clauses = " OR ".join("project LIKE ?" for _ in projects)
        categories = sorted(PROTOCOL_RISK_CATEGORIES)
        rows = self.conn.execute(
            f"SELECT id, date, project FROM incidents"
            f" WHERE ({clauses}) AND date >= ? AND date <= ?"
            f" AND category IN ({', '.join('?' for _ in categories)})"
            f" AND COALESCE(loss_usd, 0) > 0 ORDER BY date DESC",
            [f"{p}%" for p in projects] + [since, as_of.isoformat(), *categories],
        )
        return rows.fetchall()
