"""Offline tests for the Solana pool collector and filters (no network access)."""

import json
from datetime import date
from pathlib import Path

import pytest

from solana_monitor.case_library import DEFAULT_CASE_DB, CaseLibrary
from solana_monitor.config import (
    DEX_PROGRAMS,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    USDC_MINT,
    WSOL_MINT,
    FilterConfig,
)
from solana_monitor.filters import RULES, evaluate
from solana_monitor.models import DROP, FLAG, KEEP, PoolSnapshot
from solana_monitor.pipeline import Pipeline
from solana_monitor.sources.ave import AveClient
from solana_monitor.sources.dexscreener import pair_to_snapshot
from solana_monitor.sources.pool_listener import (
    NewPoolEvent,
    event_from_notification,
    is_pool_creation,
    program_log_messages,
)
from solana_monitor.sources.solana_rpc import (
    RpcError,
    parse_mint_account,
    pool_facts_from_transaction,
    top10_holder_pct,
)
from solana_monitor.store import MarketStore

SAMPLE_PAIRS = (
    Path(__file__).resolve().parent.parent
    / "solana_monitor"
    / "examples"
    / "sample_pairs.json"
)
PROGRAMS = {p.label: p for p in DEX_PROGRAMS}


def mint_account(
    owner=TOKEN_PROGRAM,
    mint_authority=None,
    freeze_authority=None,
    extensions=None,
    supply="1000000000000",
):
    info = {
        "decimals": 6,
        "supply": supply,
        "isInitialized": True,
        "mintAuthority": mint_authority,
        "freezeAuthority": freeze_authority,
    }
    if extensions is not None:
        info["extensions"] = extensions
    return {
        "context": {"slot": 1},
        "value": {
            "owner": owner,
            "data": {"program": "spl-token", "parsed": {"type": "mint", "info": info}},
        },
    }


def clean_mint(mint="SampleMintGood111111111111111111111111111111"):
    return parse_mint_account(mint, mint_account())


def snapshots():
    data = json.loads(SAMPLE_PAIRS.read_text(encoding="utf-8"))
    return {p["baseToken"]["symbol"]: pair_to_snapshot(p) for p in data["pairs"]}


# --- market-stage filters -------------------------------------------------------


def test_sample_pairs_market_decisions():
    snaps = snapshots()
    expected = {
        "GOOD": (KEEP, []),
        "THIN": (DROP, ["low_liquidity"]),
        "USDC": (DROP, ["symbol_impersonation"]),
        "ONLYUP": (DROP, ["honeypot_suspect"]),
        "DEAD": (DROP, ["inactive_pool"]),
        "ODD": (DROP, ["quote_not_allowed"]),
        "HYPE": (KEEP, []),
    }
    for symbol, (verdict, dropped_by) in expected.items():
        decision = evaluate(snaps[symbol])
        assert decision.verdict == verdict, symbol
        assert decision.dropped_by == dropped_by, symbol
    hype = evaluate(snaps["HYPE"])
    assert [h.rule_id for h in hype.hits] == ["fdv_liquidity_ratio"]
    assert hype.risk_score == RULES["fdv_liquidity_ratio"].weight


def test_real_usdc_is_not_flagged_as_impersonation():
    snap = PoolSnapshot(
        "dexscreener",
        "p",
        "raydium",
        USDC_MINT,
        base_symbol="USDC",
        quote_mint=WSOL_MINT,
        quote_symbol="SOL",
    )
    assert evaluate(snap).verdict == KEEP


def test_deprecated_program_rule_uses_config():
    snap = PoolSnapshot("raydium_amm_v4", "p", "raydium", "M", program_id="OldProgram")
    cfg = FilterConfig(deprecated_programs=("OldProgram",))
    assert evaluate(snap, cfg=cfg).dropped_by == ["deprecated_program"]
    assert evaluate(snap).verdict == KEEP


def test_filter_config_from_file(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"min_liquidity_usd": 1000, "deprecated_programs": ["X"]})
    )
    cfg = FilterConfig.from_file(path)
    assert cfg.min_liquidity_usd == 1000 and cfg.deprecated_programs == ("X",)
    path.write_text(json.dumps({"typo": 1}))
    with pytest.raises(ValueError):
        FilterConfig.from_file(path)


# --- on-chain mint filters ------------------------------------------------------


def test_clean_mint_passes():
    assert evaluate(snapshots()["GOOD"], clean_mint()).verdict == KEEP


def test_authorities_are_dropped_unless_allowlisted():
    mint = parse_mint_account(
        "M", mint_account(mint_authority="Dev", freeze_authority="Dev")
    )
    rules = evaluate(snapshots()["GOOD"], mint).dropped_by
    assert rules == ["mint_authority_active", "freeze_authority_active"]
    usdc = parse_mint_account(
        USDC_MINT, mint_account(mint_authority="C", freeze_authority="C")
    )
    assert evaluate(snapshots()["GOOD"], usdc).verdict == KEEP


def test_token2022_extensions():
    extensions = [
        {"extension": "permanentDelegate", "state": {"delegate": "Dev"}},
        {
            "extension": "transferHook",
            "state": {"authority": "Dev", "programId": "Hook"},
        },
        {"extension": "nonTransferable", "state": {}},
        {"extension": "defaultAccountState", "state": {"accountState": "frozen"}},
        {"extension": "pausableConfig", "state": {"authority": "Dev", "paused": False}},
        {
            "extension": "transferFeeConfig",
            "state": {
                "transferFeeConfigAuthority": "Dev",
                "olderTransferFee": {
                    "epoch": 1,
                    "maximumFee": 1,
                    "transferFeeBasisPoints": 0,
                },
                "newerTransferFee": {
                    "epoch": 2,
                    "maximumFee": 1,
                    "transferFeeBasisPoints": 500,
                },
            },
        },
        {"extension": "confidentialTransferMint", "state": {}},
        {"extension": "mintCloseAuthority", "state": {"closeAuthority": "Dev"}},
    ]
    mint = parse_mint_account(
        "M", mint_account(TOKEN_2022_PROGRAM, extensions=extensions)
    )
    decision = evaluate(snapshots()["GOOD"], mint)
    assert decision.dropped_by == [
        "permanent_delegate",
        "transfer_hook",
        "non_transferable",
        "default_frozen",
        "pausable",
        "transfer_fee",
    ]
    flags = [h.rule_id for h in decision.hits if h.action == FLAG]
    assert flags == ["confidential_transfer", "mint_close_authority"]


def test_small_transfer_fee_and_null_extensions_only_flag_or_pass():
    extensions = [
        {
            "extension": "transferFeeConfig",
            "state": {
                "transferFeeConfigAuthority": None,
                "newerTransferFee": {"transferFeeBasisPoints": 50},
            },
        },
        {"extension": "permanentDelegate", "state": {"delegate": None}},
        {"extension": "transferHook", "state": {"authority": None, "programId": None}},
    ]
    mint = parse_mint_account(
        "M", mint_account(TOKEN_2022_PROGRAM, extensions=extensions)
    )
    decision = evaluate(snapshots()["GOOD"], mint)
    assert decision.verdict == KEEP
    assert [(h.rule_id, h.action) for h in decision.hits] == [("transfer_fee", FLAG)]


def test_unknown_token_program_and_non_mint_account():
    mint = parse_mint_account("M", mint_account(owner="SomethingElse"))
    assert evaluate(snapshots()["GOOD"], mint).dropped_by == ["unknown_token_program"]
    with pytest.raises(RpcError):
        parse_mint_account("M", {"value": None})


def test_holder_concentration_excludes_pools_and_burns():
    info = clean_mint()  # supply 1_000_000_000_000
    largest = [
        {"address": "PoolVault", "amount": "600000000000"},
        {"address": "Burned", "amount": "100000000000"},
        {"address": "Whale", "amount": "200000000000"},
        {"address": "Small", "amount": "50000000000"},
    ]
    owners = {
        "PoolVault": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "Burned": "1nc1nerator11111111111111111111111111111111",
        "Whale": "SomeWallet",
        "Small": "OtherWallet",
    }
    assert top10_holder_pct(largest, owners, info) == 25.0
    info.top10_holder_pct = 25.0
    assert evaluate(snapshots()["GOOD"], info).verdict == KEEP
    info.top10_holder_pct = 40.0
    hit = evaluate(snapshots()["GOOD"], info).hits[0]
    assert (hit.rule_id, hit.action) == ("holder_concentration", FLAG)
    info.top10_holder_pct = 75.0
    assert evaluate(snapshots()["GOOD"], info).dropped_by == ["holder_concentration"]


# --- case library linkage --------------------------------------------------------


def test_every_rule_case_ref_exists_in_case_library():
    ids = CaseLibrary(DEFAULT_CASE_DB).case_ids()
    for rule in RULES.values():
        missing = set(rule.case_refs) - ids
        assert not missing, (rule.rule_id, missing)


def test_recent_protocol_incident_flag():
    cases = CaseLibrary(DEFAULT_CASE_DB)
    snap = snapshots()["GOOD"]  # dexId raydium
    decision = evaluate(snap, clean_mint(), cases=cases, as_of=date(2026, 7, 1))
    hit = next(h for h in decision.hits if h.rule_id == "protocol_recent_incident")
    assert hit.action == FLAG and "raydium-legacy-amm-v3-2026" in hit.case_refs
    later = evaluate(snap, clean_mint(), cases=cases, as_of=date(2027, 6, 1))
    assert later.hits == []


def test_non_protocol_incidents_do_not_flag_pools():
    # meteora-otc-scam-2026 was a team OTC scam, not a protocol exploit.
    cases = CaseLibrary(DEFAULT_CASE_DB)
    meteora = snapshots()["ONLYUP"]
    assert meteora.dex_id == "meteora"
    assert cases.recent_incidents(("Meteora",), date(2026, 5, 1), 180) == []
    decision = evaluate(meteora, cases=cases, as_of=date(2026, 5, 1))
    assert "protocol_recent_incident" not in [h.rule_id for h in decision.hits]


# --- listener log parsing ----------------------------------------------------------

RAYDIUM_V4 = PROGRAMS["raydium_amm_v4"].program_id
PUMPFUN = PROGRAMS["pumpfun"].program_id


def test_program_log_messages_tracks_nested_invocations():
    logs = [
        f"Program {RAYDIUM_V4} invoke [1]",
        f"Program {TOKEN_PROGRAM} invoke [2]",
        "Program log: Instruction: Transfer",
        f"Program {TOKEN_PROGRAM} consumed 4645 of 180000 compute units",
        f"Program {TOKEN_PROGRAM} success",
        "Program log: initialize2: InitializeInstruction2 { nonce: 254 }",
        f"Program {RAYDIUM_V4} success",
    ]
    assert program_log_messages(logs) == [
        (TOKEN_PROGRAM, "Instruction: Transfer"),
        (RAYDIUM_V4, "initialize2: InitializeInstruction2 { nonce: 254 }"),
    ]
    assert is_pool_creation(PROGRAMS["raydium_amm_v4"], logs)


def test_pool_creation_markers_are_exact():
    swap = [
        f"Program {PUMPFUN} invoke [1]",
        "Program log: Instruction: Buy",
        f"Program {PUMPFUN} success",
    ]
    create = [
        f"Program {PUMPFUN} invoke [1]",
        "Program log: Instruction: Create",
        f"Program {PUMPFUN} success",
    ]
    # A marker logged by a different program must not count.
    foreign = [
        f"Program {RAYDIUM_V4} invoke [1]",
        "Program log: Instruction: Create",
        f"Program {RAYDIUM_V4} success",
    ]
    assert not is_pool_creation(PROGRAMS["pumpfun"], swap)
    assert is_pool_creation(PROGRAMS["pumpfun"], create)
    assert not is_pool_creation(PROGRAMS["pumpfun"], foreign)


def notification(logs, err=None):
    return {
        "jsonrpc": "2.0",
        "method": "logsNotification",
        "params": {
            "result": {
                "context": {"slot": 123},
                "value": {"signature": "Sig1", "err": err, "logs": logs},
            },
            "subscription": 7,
        },
    }


def test_event_from_notification():
    create = [
        f"Program {PUMPFUN} invoke [1]",
        "Program log: Instruction: Create",
        f"Program {PUMPFUN} success",
    ]
    event = event_from_notification(PROGRAMS["pumpfun"], notification(create))
    assert event == NewPoolEvent("pumpfun", PUMPFUN, "Sig1", 123)
    failed = notification(create, err={"InstructionError": [0, "Custom"]})
    assert event_from_notification(PROGRAMS["pumpfun"], failed) is None
    assert event_from_notification(PROGRAMS["pumpfun"], {"id": 1, "result": 7}) is None


def creation_tx(creator="Creator", base="NewToken", quote_amount=12.5):
    return {
        "transaction": {
            "message": {"accountKeys": [{"pubkey": creator, "signer": True}, "Other"]}
        },
        "meta": {
            "postTokenBalances": [
                {"mint": base, "owner": "PoolAuthority"},
                {
                    "mint": WSOL_MINT,
                    "owner": "PoolAuthority",
                    "uiTokenAmount": {"uiAmount": quote_amount},
                },
                # The creator's leftover wSOL is not pool liquidity.
                {
                    "mint": WSOL_MINT,
                    "owner": creator,
                    "uiTokenAmount": {"uiAmount": 99.0},
                },
                {"mint": base, "owner": creator},
            ]
        },
    }


def test_pool_facts_from_transaction():
    facts = pool_facts_from_transaction(creation_tx())
    assert facts.creator == "Creator"
    assert (facts.base_mints, facts.quote_mints) == (["NewToken"], [WSOL_MINT])
    assert facts.initial_quote == {WSOL_MINT: 12.5}
    # Legacy transactions list account keys as plain strings.
    legacy = {"transaction": {"message": {"accountKeys": ["Payer"]}}, "meta": {}}
    assert pool_facts_from_transaction(legacy).creator == "Payer"
    empty = pool_facts_from_transaction(None)
    assert (empty.creator, empty.base_mints, empty.initial_quote) == ("", [], {})


def test_launch_rules():
    snap = PoolSnapshot("raydium_amm_v4", "", "raydium", "M", quote_symbol="SOL")
    snap.initial_quote_amount, snap.creator_recent_pools = 2.0, 0
    assert evaluate(snap).dropped_by == ["low_initial_liquidity"]
    snap.initial_quote_amount, snap.creator_recent_pools = 50.0, 3
    assert evaluate(snap).dropped_by == ["serial_creator"]
    snap.creator_recent_pools = 2
    assert evaluate(snap).verdict == KEEP


# --- end-to-end pipeline with fake clients -----------------------------------------


class FakeRpc:
    def __init__(self, mints, txs=None):
        self.mints, self.txs, self.calls = mints, txs or {}, []

    def mint_info(self, mint):
        self.calls.append(mint)
        if mint not in self.mints:
            raise RpcError("not found")
        return self.mints[mint]

    def transaction(self, signature):
        return self.txs.get(signature)


def test_pipeline_scan_skips_rpc_for_market_drops_and_stores_reasons(tmp_path):
    good = "SampleMintGood111111111111111111111111111111"
    rpc = FakeRpc({good: clean_mint(good)})
    store = MarketStore(tmp_path / "m.db")
    pairs = json.loads(SAMPLE_PAIRS.read_text(encoding="utf-8"))["pairs"]
    results = Pipeline(store, rpc).process_pairs(pairs)

    assert len(results) == 7  # the BSC pair is ignored
    # Only pools that survive the market stage cost an RPC call; HYPE has no
    # mint in the fake RPC, so it is dropped as onchain_check_failed.
    assert rpc.calls == [good, "SampleMintHype111111111111111111111111111111"]
    kept = store.conn.execute("SELECT base_symbol FROM v_kept").fetchall()
    assert kept == [("GOOD",)]
    reasons = {
        rule_id: pools
        for _, rule_id, _, pools in store.conn.execute("SELECT * FROM v_drop_reasons")
    }
    assert reasons == {
        "low_liquidity": 1,
        "symbol_impersonation": 1,
        "honeypot_suspect": 1,
        "inactive_pool": 1,
        "quote_not_allowed": 1,
        "onchain_check_failed": 1,
    }
    assert store.conn.execute("SELECT COUNT(*) FROM mint_checks").fetchone()[0] == 1


def test_pipeline_listener_event(tmp_path):
    tx = {
        "transaction": {"message": {"accountKeys": ["Creator"]}},
        "meta": {"postTokenBalances": [{"mint": "NewToken", "owner": "Curve"}]},
    }
    bad = parse_mint_account("NewToken", mint_account(mint_authority="Dev"))
    rpc = FakeRpc({"NewToken": bad}, {"Sig1": tx})
    store = MarketStore(tmp_path / "m.db")
    pipe = Pipeline(store, rpc)
    event = NewPoolEvent("pumpfun", PUMPFUN, "Sig1", 123)

    results = pipe.process_event(event, retry_delay_s=0)
    assert len(results) == 1
    snap, decision = results[0].snapshot, results[0].decision
    assert (snap.dex_id, snap.quote_symbol, snap.signature) == (
        "pumpfun",
        "SOL",
        "Sig1",
    )
    assert decision.dropped_by == ["mint_authority_active"]
    # The same signature is not processed twice.
    assert pipe.process_event(event, retry_delay_s=0) == []
    row = store.conn.execute(
        "SELECT dex, creator, base_mints FROM new_pool_events"
    ).fetchone()
    assert row == ("pumpfun", "Creator", "NewToken")


def test_pipeline_drops_serial_creator_and_thin_launches(tmp_path):
    txs = {
        f"Sig{i}": creation_tx(base=f"Token{i}", quote_amount=20.0) for i in range(5)
    }
    txs["Thin"] = creation_tx(creator="Someone", base="ThinToken", quote_amount=0.5)
    mints = {m: clean_mint(m) for m in [f"Token{i}" for i in range(5)] + ["ThinToken"]}
    pipe = Pipeline(MarketStore(tmp_path / "m.db"), FakeRpc(mints, txs))
    raydium = PROGRAMS["raydium_amm_v4"].program_id

    verdicts = []
    for i in range(5):
        event = NewPoolEvent("raydium_amm_v4", raydium, f"Sig{i}", i)
        (result,) = pipe.process_event(event, retry_delay_s=0)
        verdicts.append(result.decision.verdict)
        assert result.snapshot.creator_recent_pools == i
        assert result.snapshot.initial_quote_amount == 20.0
    # max_creator_pools_24h = 3: the 4th and 5th pools from one wallet are dropped.
    assert verdicts == [KEEP, KEEP, KEEP, DROP, DROP]

    thin = NewPoolEvent("raydium_amm_v4", raydium, "Thin", 9)
    (result,) = pipe.process_event(thin, retry_delay_s=0)
    assert result.decision.dropped_by == ["low_initial_liquidity"]


class FakeHttp:
    def __init__(self, response):
        self.response, self.requests = response, []

    def request(self, url, payload=None, headers=None):
        self.requests.append((url, headers))
        return self.response


def test_ave_client_builds_documented_requests(monkeypatch):
    http = FakeHttp({"status": 1, "data": {}})
    ave = AveClient(api_key="k", http=http, base_url="https://data.ave-api.xyz/v2")
    ave.contract_risk("Mint1")
    ave.holders("Mint1", limit=5)
    ave.trending(page=2, page_size=10)
    urls = [u for u, _ in http.requests]
    assert urls == [
        "https://data.ave-api.xyz/v2/contracts/Mint1-solana",
        "https://data.ave-api.xyz/v2/tokens/holders/Mint1-solana?limit=5",
        "https://data.ave-api.xyz/v2/tokens/trending?chain=solana&current_page=2&page_size=10",
    ]
    assert all(h == {"X-API-KEY": "k"} for _, h in http.requests)
    monkeypatch.delenv("AVE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        AveClient()


def test_pipeline_archives_ave_reports_for_kept_pools_only(tmp_path):
    good = "SampleMintGood111111111111111111111111111111"
    hype = "SampleMintHype111111111111111111111111111111"
    rpc = FakeRpc({good: clean_mint(good), hype: clean_mint(hype)})
    http = FakeHttp({"status": 1, "data": {"risk": "sample"}})
    store = MarketStore(tmp_path / "m.db")
    pairs = json.loads(SAMPLE_PAIRS.read_text(encoding="utf-8"))["pairs"]
    Pipeline(store, rpc, ave=AveClient(api_key="k", http=http)).process_pairs(pairs)
    rows = store.conn.execute(
        "SELECT provider, kind, mint FROM external_reports ORDER BY id"
    ).fetchall()
    assert rows == [("ave", "contract_risk", good), ("ave", "contract_risk", hype)]
