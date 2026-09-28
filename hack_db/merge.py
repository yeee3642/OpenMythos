"""Merge case-library workflow results into a dataset's incidents.json.

The research workflows in ``hack_db/workflows/`` return, for each written
record, two independent verdicts: a *facts* lens and a *scope* lens. This
module applies them:

* a record is dropped if either lens says ``drop``;
* the facts lens may correct factual fields, the scope lens only
  classification fields (so the two never fight over the same field);
* helper fields added by the writers are removed and ids are made unique.

Usage:
    python -m hack_db.merge ethereum_hacks results1.json [results2.json ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .build import INCIDENT_FIELDS, Dataset, validate

CLASSIFICATION_FIELDS = {
    "id",
    "project_type",
    "category",
    "vuln_pattern",
    "chain_scope",
    "recovery_status",
}
RECORD_FIELDS = (*INCIDENT_FIELDS, "sources")
FACT_FIELDS = set(RECORD_FIELDS) - CLASSIFICATION_FIELDS


def apply_verdicts(record: dict, facts: dict | None, scope: dict | None):
    """Return (merged record, None) or (None, reason) if a lens dropped it."""
    for lens, verdict in (("facts", facts), ("scope", scope)):
        if verdict and verdict.get("verdict") == "drop":
            return None, f"{lens}: {verdict.get('reasons', '')}"
    merged = {k: record.get(k) for k in RECORD_FIELDS}
    for verdict, allowed in ((facts, FACT_FIELDS), (scope, CLASSIFICATION_FIELDS)):
        if verdict and verdict.get("verdict") == "fix":
            for key, value in (verdict.get("corrections") or {}).items():
                if key in allowed:
                    merged[key] = value
    return merged, None


def _unique_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def merge_results(
    existing: list[dict], results: list[dict]
) -> tuple[list[dict], list[str]]:
    """Merge workflow ``results`` entries ({record, facts, scope}) into existing."""
    taken = {r["id"] for r in existing}
    added, log = [], []
    for item in results:
        record, reason = apply_verdicts(
            item["record"], item.get("facts"), item.get("scope")
        )
        rid = item["record"].get("id")
        if record is None:
            log.append(f"dropped {rid}: {reason}")
            continue
        record["id"] = _unique_id(record["id"], taken)
        taken.add(record["id"])
        added.append(record)
    merged = sorted(existing + added, key=lambda r: (r["date"], r["id"]))
    log.append(f"added {len(added)} records")
    return merged, log


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="hack_db.merge", description=__doc__.split("\n")[0]
    )
    parser.add_argument("dataset")
    parser.add_argument(
        "results", nargs="+", type=Path, help="workflow result JSON files"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    ds = Dataset(args.dataset)
    existing = ds.incidents() if ds.available else []
    results = []
    for path in args.results:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Accept either {results: [...]} (EVM era) or {sweep: {results: [...]}} (Solana).
        results += data.get("results") or (data.get("sweep") or {}).get("results") or []
    merged, log = merge_results(existing, results)
    print("\n".join(log))
    errors = validate(merged, ds.lookups())
    if errors:
        raise SystemExit("validation failed:\n  " + "\n  ".join(errors))
    if not args.dry_run:
        ds.incidents_path.parent.mkdir(parents=True, exist_ok=True)
        ds.incidents_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {ds.incidents_path} ({len(merged)} incidents)")


if __name__ == "__main__":
    main()
