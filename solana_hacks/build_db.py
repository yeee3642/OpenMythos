"""Build the Solana security-incident SQLite database from the JSON sources.

Usage:
    python solana_hacks/build_db.py            # writes solana_hacks.db + exports/
    python solana_hacks/build_db.py --out x.db # custom database path

Only the standard library is used, so no extra dependencies are needed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.sql"
LOOKUPS_PATH = ROOT / "data" / "lookups.json"
INCIDENTS_PATH = ROOT / "data" / "incidents.json"
DEFAULT_DB_PATH = ROOT / "solana_hacks.db"
EXPORT_DIR = ROOT / "exports"

INCIDENT_FIELDS = (
    "id",
    "date",
    "project",
    "project_type",
    "category",
    "vuln_pattern",
    "attack_vector",
    "loss_usd",
    "assets_stolen",
    "recovered_usd",
    "recovery_status",
    "attribution",
    "chain_scope",
    "summary_zh",
    "root_cause_zh",
    "aftermath_zh",
    "confidence",
    "notes",
)
# Categories whose root cause is a code logic flaw; these must carry a vuln_pattern.
LOGIC_BUG_CATEGORIES = {"smart_contract_bug", "protocol_vulnerability"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate(incidents: list[dict], lookups: dict) -> list[str]:
    """Return a list of human-readable problems; empty means the data is valid."""
    errors: list[str] = []
    allowed = {
        "project_type": {row["code"] for row in lookups["project_types"]},
        "category": {row["code"] for row in lookups["categories"]},
        "recovery_status": {row["code"] for row in lookups["recovery_statuses"]},
        "chain_scope": {"solana_only", "multi_chain"},
        "confidence": {"high", "medium", "low"},
    }
    vuln_patterns = {row["code"] for row in lookups["vuln_patterns"]}
    seen_ids: set[str] = set()

    for idx, inc in enumerate(incidents):
        label = inc.get("id") or f"#{idx}"
        missing = [f for f in INCIDENT_FIELDS if f not in inc]
        extra = [f for f in inc if f not in INCIDENT_FIELDS and f != "sources"]
        if missing:
            errors.append(f"{label}: missing fields {missing}")
        if extra:
            errors.append(f"{label}: unknown fields {extra}")
        if missing:
            continue

        if not ID_PATTERN.match(inc["id"]):
            errors.append(f"{label}: id must be kebab-case")
        if inc["id"] in seen_ids:
            errors.append(f"{label}: duplicate id")
        seen_ids.add(inc["id"])

        if not DATE_PATTERN.match(inc["date"]):
            errors.append(f"{label}: date must be YYYY-MM-DD")

        for field, values in allowed.items():
            if inc[field] not in values:
                errors.append(f"{label}: invalid {field} {inc[field]!r}")

        pattern = inc["vuln_pattern"]
        if inc["category"] in LOGIC_BUG_CATEGORIES:
            if pattern not in vuln_patterns:
                errors.append(f"{label}: logic-bug incident needs a valid vuln_pattern")
        elif pattern is not None:
            errors.append(f"{label}: vuln_pattern is only for logic-bug categories")

        if inc["recovery_status"] == "not_applicable" and inc["loss_usd"]:
            errors.append(f"{label}: not_applicable recovery but loss_usd > 0")

        for field in ("loss_usd", "recovered_usd"):
            value = inc[field]
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                errors.append(f"{label}: {field} must be a non-negative number")

        for field in ("project", "attack_vector", "attribution", "summary_zh"):
            if not str(inc[field]).strip():
                errors.append(f"{label}: {field} is empty")

        sources = inc.get("sources") or []
        if not sources:
            errors.append(f"{label}: at least one source is required")
        for src in sources:
            url = src.get("url", "")
            if not url.startswith(("https://", "http://")):
                errors.append(f"{label}: bad source url {url!r}")

    dates = [inc.get("date", "") for inc in incidents]
    if dates != sorted(dates):
        errors.append("incidents.json must be sorted by date")
    return errors


def build(db_path: Path) -> sqlite3.Connection:
    lookups = load_json(LOOKUPS_PATH)
    incidents = load_json(INCIDENTS_PATH)
    errors = validate(incidents, lookups)
    if errors:
        raise ValueError("invalid incident data:\n  " + "\n  ".join(errors))

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    conn.executemany(
        "INSERT INTO project_types (code, name_zh, name_en) VALUES (?, ?, ?)",
        [(r["code"], r["name_zh"], r["name_en"]) for r in lookups["project_types"]],
    )
    conn.executemany(
        "INSERT INTO categories (code, name_zh, name_en, description_zh)"
        " VALUES (?, ?, ?, ?)",
        [
            (r["code"], r["name_zh"], r["name_en"], r["description_zh"])
            for r in lookups["categories"]
        ],
    )
    conn.executemany(
        "INSERT INTO recovery_statuses (code, name_zh, name_en) VALUES (?, ?, ?)",
        [(r["code"], r["name_zh"], r["name_en"]) for r in lookups["recovery_statuses"]],
    )
    conn.executemany(
        "INSERT INTO vuln_patterns (code, name_zh, name_en, description_zh, defense_zh)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (
                r["code"],
                r["name_zh"],
                r["name_en"],
                r["description_zh"],
                r["defense_zh"],
            )
            for r in lookups["vuln_patterns"]
        ],
    )

    placeholders = ", ".join("?" for _ in INCIDENT_FIELDS)
    conn.executemany(
        f"INSERT INTO incidents ({', '.join(INCIDENT_FIELDS)}) VALUES ({placeholders})",
        [tuple(inc[f] for f in INCIDENT_FIELDS) for inc in incidents],
    )
    conn.executemany(
        "INSERT INTO sources (incident_id, title, publisher, url) VALUES (?, ?, ?, ?)",
        [
            (inc["id"], src["title"], src["publisher"], src["url"])
            for inc in incidents
            for src in inc["sources"]
        ],
    )
    conn.commit()
    return conn


def export_csv(conn: sqlite3.Connection, export_dir: Path) -> None:
    """Write CSV copies (UTF-8 with BOM so Excel shows Chinese correctly)."""
    export_dir.mkdir(parents=True, exist_ok=True)
    queries = {
        "incidents.csv": """
            SELECT v.*,
                   (SELECT group_concat(s.url, ' | ') FROM sources s
                     WHERE s.incident_id = v.id) AS source_urls
              FROM v_incidents v
             ORDER BY v.date, v.id
        """,
        "sources.csv": "SELECT incident_id, publisher, title, url FROM sources"
        " ORDER BY incident_id, id",
        "yearly_summary.csv": "SELECT * FROM v_yearly_summary",
        "category_summary.csv": "SELECT * FROM v_category_summary",
        "vuln_pattern_summary.csv": "SELECT * FROM v_vuln_pattern_summary",
    }
    for filename, sql in queries.items():
        cur = conn.execute(sql)
        with (export_dir / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([col[0] for col in cur.description])
            writer.writerows(cur.fetchall())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--no-export", action="store_true", help="skip CSV export")
    args = parser.parse_args()

    conn = build(args.out)
    if not args.no_export:
        export_csv(conn, EXPORT_DIR)
    count, total = conn.execute(
        "SELECT COUNT(*), SUM(COALESCE(loss_usd, 0)) FROM incidents"
    ).fetchone()
    conn.close()
    print(f"built {args.out} with {count} incidents, total loss ${total:,.0f}")


if __name__ == "__main__":
    main()
