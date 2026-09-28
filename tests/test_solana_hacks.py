"""Validate the Solana security-incident dataset and database build."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "solana_hacks"))

import build_db  # noqa: E402


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "solana_hacks.db"
    connection = build_db.build(db_path)
    yield connection
    connection.close()


def test_dataset_is_valid():
    lookups = build_db.load_json(build_db.LOOKUPS_PATH)
    incidents = build_db.load_json(build_db.INCIDENTS_PATH)
    assert incidents, "dataset should not be empty"
    assert build_db.validate(incidents, lookups) == []


def test_validate_rejects_bad_rows():
    lookups = build_db.load_json(build_db.LOOKUPS_PATH)
    good = build_db.load_json(build_db.INCIDENTS_PATH)[0]
    bad = dict(good, id="Bad_ID", category="nope", loss_usd=-1, sources=[])
    errors = build_db.validate([bad], lookups)
    assert any("kebab-case" in e for e in errors)
    assert any("invalid category" in e for e in errors)
    assert any("loss_usd" in e for e in errors)
    assert any("source" in e for e in errors)


def test_every_incident_has_a_source(conn):
    orphans = conn.execute(
        "SELECT id FROM incidents i"
        " WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.incident_id = i.id)"
    ).fetchall()
    assert orphans == []


def test_views_are_consistent(conn):
    incidents = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM v_incidents").fetchone()[0] == incidents
    yearly = conn.execute("SELECT SUM(incidents) FROM v_yearly_summary").fetchone()[0]
    assert yearly == incidents
    by_category = conn.execute(
        "SELECT SUM(incidents) FROM v_category_summary"
    ).fetchone()[0]
    assert by_category == incidents


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (incident_id, title, publisher, url)"
            " VALUES ('does-not-exist', 't', 'p', 'https://example.com')"
        )


def test_recovered_never_exceeds_loss(conn):
    rows = conn.execute(
        "SELECT id FROM incidents"
        " WHERE loss_usd IS NOT NULL AND recovered_usd > loss_usd"
    ).fetchall()
    assert rows == []
