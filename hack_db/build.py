"""Build the per-chain case-library databases and a combined cross-chain database.

Each case library lives in its own directory (``solana_hacks/``,
``ethereum_hacks/``, ``bsc_hacks/``) with ``data/incidents.json`` and
``data/lookups.json``. Code tables shared by every chain live in
``hack_db/common_lookups.json``; vulnerability patterns are shared per VM
family in ``hack_db/vuln_patterns/<family>.json``.

Only the standard library is used.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

HACK_DB = Path(__file__).resolve().parent
REPO = HACK_DB.parent
SCHEMA_PATH = HACK_DB / "schema.sql"
COMMON_LOOKUPS_PATH = HACK_DB / "common_lookups.json"
DATASETS = ("solana_hacks", "ethereum_hacks", "bsc_hacks")
COMBINED_DB_PATH = REPO / "crypto_hacks.db"

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
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Dataset:
    name: str

    @property
    def root(self) -> Path:
        return REPO / self.name

    @property
    def incidents_path(self) -> Path:
        return self.root / "data" / "incidents.json"

    @property
    def lookups_path(self) -> Path:
        return self.root / "data" / "lookups.json"

    @property
    def db_path(self) -> Path:
        return self.root / f"{self.name}.db"

    @property
    def export_dir(self) -> Path:
        return self.root / "exports"

    @property
    def available(self) -> bool:
        return self.incidents_path.exists()

    def lookups(self) -> dict:
        """Common code tables merged with this dataset's own tables."""
        own = load_json(self.lookups_path)
        merged = {**load_json(COMMON_LOOKUPS_PATH), **own}
        family = own["vuln_patterns"]
        merged["vuln_family"] = family
        merged["vuln_patterns"] = load_json(
            HACK_DB / "vuln_patterns" / f"{family}.json"
        )
        return merged

    def incidents(self) -> list[dict]:
        return load_json(self.incidents_path)


def validate(incidents: list[dict], lookups: dict) -> list[str]:
    """Return a list of human-readable problems; empty means the data is valid."""
    errors: list[str] = []
    allowed = {
        "project_type": {row["code"] for row in lookups["project_types"]},
        "category": {row["code"] for row in lookups["categories"]},
        "recovery_status": {row["code"] for row in lookups["recovery_statuses"]},
        "chain_scope": {row["code"] for row in lookups["chain_scopes"]},
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
        if (
            inc["loss_usd"] is not None
            and inc["recovered_usd"] is not None
            and inc["recovered_usd"] > inc["loss_usd"]
        ):
            errors.append(f"{label}: recovered_usd exceeds loss_usd")

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


def _insert_lookups(conn: sqlite3.Connection, lookups: dict) -> None:
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


def build(dataset: Dataset, db_path: Path | None = None) -> sqlite3.Connection:
    """Validate a dataset and build its SQLite database."""
    lookups = dataset.lookups()
    incidents = dataset.incidents()
    errors = validate(incidents, lookups)
    if errors:
        raise ValueError(
            f"invalid incident data in {dataset.name}:\n  " + "\n  ".join(errors)
        )

    db_path = Path(db_path or dataset.db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    _insert_lookups(conn, lookups)
    conn.executemany(
        "INSERT INTO chain_scopes (code, name_zh, name_en) VALUES (?, ?, ?)",
        [(r["code"], r["name_zh"], r["name_en"]) for r in lookups["chain_scopes"]],
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
    info = {**lookups["dataset"], "vuln_family": lookups["vuln_family"]}
    conn.executemany(
        "INSERT INTO dataset_info (key, value) VALUES (?, ?)", list(info.items())
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


EXPORT_QUERIES = {
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


def export_csv(
    conn: sqlite3.Connection, export_dir: Path, queries: dict | None = None
) -> None:
    """Write CSV copies (UTF-8 with BOM so Excel shows Chinese correctly)."""
    export_dir.mkdir(parents=True, exist_ok=True)
    for filename, sql in (queries or EXPORT_QUERIES).items():
        cur = conn.execute(sql)
        with (export_dir / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([col[0] for col in cur.description])
            writer.writerows(cur.fetchall())


COMBINED_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE chains (
    dataset   TEXT PRIMARY KEY,
    chain_zh  TEXT NOT NULL
);
CREATE TABLE incidents (
    chain TEXT NOT NULL REFERENCES chains (dataset),
    {columns},
    PRIMARY KEY (chain, id)
);
CREATE TABLE sources (
    chain        TEXT NOT NULL,
    incident_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    url          TEXT NOT NULL,
    FOREIGN KEY (chain, incident_id) REFERENCES incidents (chain, id)
);
CREATE TABLE categories (code TEXT PRIMARY KEY, name_zh TEXT NOT NULL);
CREATE TABLE vuln_patterns (
    family   TEXT NOT NULL,
    code     TEXT NOT NULL,
    name_zh  TEXT NOT NULL,
    PRIMARY KEY (family, code)
);

-- 跨鏈比較：每條鏈每年的事件數與損失（單鏈損失不含多鏈事件）
CREATE VIEW v_chain_yearly AS
SELECT i.chain, c.chain_zh, CAST(substr(i.date, 1, 4) AS INTEGER) AS year,
       COUNT(*) AS incidents,
       SUM(COALESCE(i.loss_usd, 0)) AS total_loss_usd,
       SUM(CASE WHEN i.chain_scope != 'multi_chain'
                THEN COALESCE(i.loss_usd, 0) ELSE 0 END) AS single_chain_loss_usd
  FROM incidents i JOIN chains c ON c.dataset = i.chain
 GROUP BY i.chain, c.chain_zh, year
 ORDER BY i.chain, year;

-- 跨鏈比較：每條鏈各攻擊類別的事件數與損失
CREATE VIEW v_chain_category AS
SELECT i.chain, i.category, cat.name_zh AS category_zh,
       COUNT(*) AS incidents,
       SUM(COALESCE(i.loss_usd, 0)) AS total_loss_usd
  FROM incidents i JOIN categories cat ON cat.code = i.category
 GROUP BY i.chain, i.category, cat.name_zh
 ORDER BY i.chain, total_loss_usd DESC;
"""


def build_combined(
    datasets: list[Dataset], db_path: Path = COMBINED_DB_PATH
) -> sqlite3.Connection:
    """One database with every chain's incidents, keyed by (chain, id).

    Multi-chain incidents may appear once per chain they affected.
    """
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    columns = ",\n    ".join(
        f"{f} {'REAL' if f.endswith('_usd') else 'TEXT'}" for f in INCIDENT_FIELDS
    )
    conn.executescript(COMBINED_SCHEMA.replace("{columns}", columns))
    common = load_json(COMMON_LOOKUPS_PATH)
    conn.executemany(
        "INSERT INTO categories VALUES (?, ?)",
        [(c["code"], c["name_zh"]) for c in common["categories"]],
    )
    families = set()
    for ds in datasets:
        lookups = ds.lookups()
        conn.execute(
            "INSERT INTO chains VALUES (?, ?)",
            (ds.name, lookups["dataset"]["chain_zh"]),
        )
        if lookups["vuln_family"] not in families:
            families.add(lookups["vuln_family"])
            conn.executemany(
                "INSERT INTO vuln_patterns VALUES (?, ?, ?)",
                [
                    (lookups["vuln_family"], p["code"], p["name_zh"])
                    for p in lookups["vuln_patterns"]
                ],
            )
        incidents = ds.incidents()
        marks = ", ".join("?" for _ in range(len(INCIDENT_FIELDS) + 1))
        conn.executemany(
            f"INSERT INTO incidents (chain, {', '.join(INCIDENT_FIELDS)}) VALUES ({marks})",
            [(ds.name, *(inc[f] for f in INCIDENT_FIELDS)) for inc in incidents],
        )
        conn.executemany(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
            [
                (ds.name, inc["id"], s["title"], s["publisher"], s["url"])
                for inc in incidents
                for s in inc["sources"]
            ],
        )
    conn.commit()
    return conn
