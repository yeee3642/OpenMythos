"""Validate every case library (Solana / Ethereum / BSC) and the database builds."""

import sqlite3

import pytest

from hack_db import defihacklabs as dhl
from hack_db.build import (
    DATASETS,
    Dataset,
    build,
    build_combined,
    load_json,
    validate,
)
from hack_db.merge import apply_verdicts, merge_results

AVAILABLE = [Dataset(n) for n in DATASETS if Dataset(n).available]
IDS = [d.name for d in AVAILABLE]


@pytest.fixture(scope="module", params=AVAILABLE, ids=IDS)
def dataset(request):
    return request.param


@pytest.fixture(scope="module")
def conn(dataset, tmp_path_factory):
    connection = build(dataset, tmp_path_factory.mktemp("db") / f"{dataset.name}.db")
    yield connection
    connection.close()


def test_solana_dataset_is_available():
    assert "solana_hacks" in IDS


def test_dataset_is_valid(dataset):
    incidents = dataset.incidents()
    assert incidents, "dataset should not be empty"
    assert validate(incidents, dataset.lookups()) == []


def test_validate_rejects_bad_rows():
    ds = Dataset("solana_hacks")
    lookups = ds.lookups()
    good = ds.incidents()[0]
    bad = dict(good, id="Bad_ID", category="nope", loss_usd=-1, sources=[])
    errors = validate([bad], lookups)
    assert any("kebab-case" in e for e in errors)
    assert any("invalid category" in e for e in errors)
    assert any("loss_usd" in e for e in errors)
    assert any("source" in e for e in errors)
    over = dict(good, loss_usd=10, recovered_usd=20, recovery_status="partial")
    assert any("exceeds" in e for e in validate([over], lookups))


def test_validate_enforces_vuln_pattern_rules():
    ds = Dataset("solana_hacks")
    lookups, incidents = ds.lookups(), ds.incidents()
    logic_bug = next(i for i in incidents if i["category"] == "smart_contract_bug")
    other = next(i for i in incidents if i["category"] == "private_key_compromise")
    errors = validate([dict(logic_bug, vuln_pattern=None)], lookups)
    assert any("needs a valid vuln_pattern" in e for e in errors)
    errors = validate([dict(other, vuln_pattern="arithmetic")], lookups)
    assert any("only for logic-bug categories" in e for e in errors)


def test_chain_scope_codes_are_per_dataset():
    for name, single in (
        ("solana_hacks", "solana_only"),
        ("ethereum_hacks", "ethereum_only"),
        ("bsc_hacks", "bsc_only"),
    ):
        codes = {
            c["code"] for c in load_json(Dataset(name).lookups_path)["chain_scopes"]
        }
        assert codes == {single, "multi_chain"}


def test_every_incident_has_a_source(conn):
    orphans = conn.execute(
        "SELECT id FROM incidents i"
        " WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.incident_id = i.id)"
    ).fetchall()
    assert orphans == []


def test_views_are_consistent(conn):
    incidents = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM v_incidents").fetchone()[0] == incidents
    for view in ("v_yearly_summary", "v_category_summary", "v_project_type_summary"):
        total = conn.execute(f"SELECT SUM(incidents) FROM {view}").fetchone()[0]
        assert total == incidents, view


def test_logic_bugs_are_fully_classified(conn):
    unclassified = conn.execute(
        "SELECT id FROM incidents"
        " WHERE category IN ('smart_contract_bug', 'protocol_vulnerability')"
        " AND vuln_pattern IS NULL"
    ).fetchall()
    assert unclassified == []
    tagged = conn.execute(
        "SELECT COUNT(*) FROM incidents WHERE vuln_pattern IS NOT NULL"
    ).fetchone()[0]
    summarized = conn.execute(
        "SELECT SUM(incidents) FROM v_vuln_pattern_summary"
    ).fetchone()[0]
    assert tagged == summarized


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (incident_id, title, publisher, url)"
            " VALUES ('does-not-exist', 't', 'p', 'https://example.com')"
        )


def test_combined_database(tmp_path):
    conn = build_combined(AVAILABLE, tmp_path / "all.db")
    expected = sum(len(d.incidents()) for d in AVAILABLE)
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == expected
    chains = {
        row[0] for row in conn.execute("SELECT DISTINCT chain FROM v_chain_yearly")
    }
    assert chains == set(IDS)


# --- merging workflow results -------------------------------------------------------


def _record(**overrides):
    base = dict(Dataset("solana_hacks").incidents()[0])
    base.update(overrides)
    return base


def test_apply_verdicts_splits_fields_between_lenses():
    rec = _record(id="x-2024", loss_usd=100)
    facts = {"verdict": "fix", "corrections": {"loss_usd": 90, "category": "nope"}}
    scope = {
        "verdict": "fix",
        "corrections": {"category": "insider_threat", "loss_usd": 1},
    }
    merged, reason = apply_verdicts(rec, facts, scope)
    assert reason is None
    # Facts may not reclassify; scope may not change amounts.
    assert merged["loss_usd"] == 90
    assert merged["category"] == "insider_threat"


def test_apply_verdicts_drops_on_either_lens():
    rec = _record(id="x-2024")
    assert apply_verdicts(rec, {"verdict": "drop", "reasons": "fake"}, None)[0] is None
    assert apply_verdicts(rec, {"verdict": "keep"}, {"verdict": "drop"})[0] is None


def test_merge_results_dedupes_ids_and_sorts():
    existing = [_record(id="a-2022", date="2022-01-01")]
    results = [
        {
            "record": _record(id="a-2022", date="2021-01-01", in_scope=True),
            "facts": None,
            "scope": None,
        },
        {
            "record": _record(id="b-2023", date="2023-01-01"),
            "facts": {"verdict": "drop"},
            "scope": None,
        },
    ]
    merged, log = merge_results(existing, results)
    assert [r["id"] for r in merged] == ["a-2022-2", "a-2022"]
    assert "in_scope" not in merged[0]
    assert any(line.startswith("dropped b-2023") for line in log)


# --- DeFiHackLabs importer -----------------------------------------------------------


DHL_SAMPLE = """
### 20260918 Likwid - missing pairDelta update in leverage=0 margin borrow
### Lost: 74.31 BNB (reproduced to within 3 wei)
```sh
forge test --contracts src/test/2026-09/Likwid_exp.sol -vvv
```
### 20230218 - RevertFinance - Arbitrary External Call Vulnerability

### Lost: ~$30k

forge test --contracts ./src/test/2023-02/RevertFinance_exp.sol -vvv

https://mirror.xyz/revertfinance.eth/abc
"""


def test_dhl_parse_entries_handles_both_formats():
    likwid, revert = dhl.parse_entries(DHL_SAMPLE)
    assert (likwid["date"], likwid["name"], likwid["poc"]) == (
        "2026-09-18",
        "Likwid",
        "src/test/2026-09/Likwid_exp.sol",
    )
    assert revert["name"] == "RevertFinance"
    assert revert["root_cause"] == "Arbitrary External Call Vulnerability"
    assert revert["links"] == ["https://mirror.xyz/revertfinance.eth/abc"]


def test_dhl_detect_chain_and_usd():
    assert dhl.detect_chain('vm.createSelectFork("bsc", 123);') == "bsc"
    assert dhl.detect_chain('vm.createSelectFork("mainnet", 1);') == "ethereum"
    assert dhl.detect_chain("// Attack Tx : https://etherscan.io/tx/0xab") == "ethereum"
    assert dhl.detect_chain("// https://optimistic.etherscan.io/tx/0xab") == "optimism"
    assert dhl.parse_usd("~$30k") == 30000
    assert dhl.parse_usd("$1.2M") == 1_200_000
    assert dhl.parse_usd("100k USD") == 100_000
    assert dhl.parse_usd("74.31 BNB") is None


def test_dhl_candidate_sources_use_real_links_only():
    entry = dhl.parse_entries(DHL_SAMPLE)[1]
    poc = "// Attack Tx : https://etherscan.io/tx/0xdead\n// https://x.com/a/status/1"
    sources = dhl.candidate_sources(entry, poc)
    assert sources[0]["url"].startswith(dhl.BLOB)
    urls = [s["url"] for s in sources]
    assert "https://mirror.xyz/revertfinance.eth/abc" in urls
    assert "https://x.com/a/status/1" in urls
    assert "https://etherscan.io/tx/0xdead" in urls
