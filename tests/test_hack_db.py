"""Validate every case library (Solana / Ethereum / BSC) and the database builds."""

import sqlite3

import pytest

from hack_db.build import (
    DATASETS,
    Dataset,
    build,
    build_combined,
    load_json,
    validate,
)

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
