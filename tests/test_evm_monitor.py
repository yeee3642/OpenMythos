"""Offline tests for the Ethereum / BSC pool collector and filters."""

import json
from pathlib import Path

import pytest

from evm_monitor.__main__ import main as cli
from evm_monitor.config import (
    BSC,
    CHAINS,
    ETHEREUM,
    PAIR_CREATED,
    POOL_CREATED,
    FilterConfig,
)
from evm_monitor.filters import RULES, evaluate, security_rules
from evm_monitor.models import DROP, FLAG, KEEP, NewPoolEvent, PoolFacts, PoolSnapshot
from evm_monitor.pipeline import Pipeline
from evm_monitor.sources.evm_rpc import decode_factory_log
from evm_monitor.sources.factory_listener import poll_new_pools
from evm_monitor.sources.goplus import GoPlusClient, GoPlusError, parse_token_security
from evm_monitor.store import MarketStore
from monitor_common.dexscreener import pair_to_snapshot

SAMPLE = (
    Path(__file__).resolve().parent.parent
    / "evm_monitor/examples/sample_pairs_bsc.json"
)
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT = "0x55d398326f99059fF775485246999027B3197955"
TOKEN = "0xa000000000000000000000000000000000000001"


def word(addr_or_int) -> str:
    if isinstance(addr_or_int, int):
        return hex(addr_or_int)[2:].rjust(64, "0")
    return addr_or_int.lower().replace("0x", "").rjust(64, "0")


def snapshots():
    pairs = json.loads(SAMPLE.read_text(encoding="utf-8"))["pairs"]
    return {p["baseToken"]["symbol"]: pair_to_snapshot(p) for p in pairs}


def goplus_entry(**overrides):
    entry = {
        "is_open_source": "1",
        "is_proxy": "0",
        "is_mintable": "0",
        "owner_address": "0x0000000000000000000000000000000000000000",
        "can_take_back_ownership": "0",
        "owner_change_balance": "0",
        "hidden_owner": "0",
        "selfdestruct": "0",
        "external_call": "0",
        "is_honeypot": "0",
        "cannot_buy": "0",
        "cannot_sell_all": "0",
        "buy_tax": "0",
        "sell_tax": "0",
        "slippage_modifiable": "0",
        "transfer_pausable": "0",
        "is_blacklisted": "0",
        "creator_percent": "0.02",
        "holder_count": "1200",
        "lp_holders": [
            {
                "address": "0x000000000000000000000000000000000000dEaD",
                "percent": "0.9",
                "is_locked": 0,
            },
            {
                "address": "0xdev0000000000000000000000000000000000000",
                "percent": "0.1",
                "is_locked": 0,
            },
        ],
        "holders": [
            {
                "address": "0x1111111111111111111111111111111111111111",
                "percent": "0.40",
                "is_locked": 0,
            },
            {
                "address": "0xbeef000000000000000000000000000000000001",
                "percent": "0.05",
                "is_locked": 0,
            },
            {
                "address": "0xbeef000000000000000000000000000000000002",
                "percent": "0.20",
                "is_locked": 1,
            },
        ],
    }
    entry.update(overrides)
    return entry


def test_addresses_pass_eip55_checksums():
    keccak = pytest.importorskip("Crypto.Hash.keccak")

    def checksum(addr):
        low = addr[2:].lower()
        h = keccak.new(digest_bits=256)
        h.update(low.encode())
        digest = h.hexdigest()
        return "0x" + "".join(
            c.upper() if c.isalpha() and int(digest[i], 16) >= 8 else c
            for i, c in enumerate(low)
        )

    for chain in CHAINS.values():
        addrs = [f.address for f in chain.factories]
        addrs += list(chain.quote_tokens) + list(chain.canonical_tokens.values())
        for addr in addrs:
            assert checksum(addr) == addr, (chain.key, addr)


# --- log decoding -----------------------------------------------------------------


def test_decode_v2_pair_created():
    factory = BSC.factories[0]
    log = {
        "address": factory.address.lower(),
        "topics": [PAIR_CREATED, "0x" + word(TOKEN), "0x" + word(WBNB)],
        "data": "0x" + word("0x1111111111111111111111111111111111111111") + word(1234),
        "transactionHash": "0xabc",
        "blockNumber": hex(100),
    }
    event = decode_factory_log(log, BSC)
    assert event.dex == "pancakeswap_v2"
    assert event.pool == "0x1111111111111111111111111111111111111111"
    assert event.tokens == (TOKEN, WBNB.lower())
    assert (event.signature, event.slot) == ("0xabc", 100)


def test_decode_v3_pool_created_and_rejects_unknown():
    factory = ETHEREUM.factories[1]
    log = {
        "address": factory.address,
        "topics": [
            POOL_CREATED,
            "0x" + word(TOKEN),
            "0x" + word(ETHEREUM.canonical_tokens["WETH"]),
            "0x" + word(3000),
        ],
        "data": "0x" + word(60) + word("0x9999999999999999999999999999999999999999"),
        "transactionHash": "0xdef",
        "blockNumber": "0x10",
    }
    event = decode_factory_log(log, ETHEREUM)
    assert event.dex == "uniswap_v3"
    assert event.pool == "0x9999999999999999999999999999999999999999"
    assert decode_factory_log({**log, "address": "0x" + "1" * 40}, ETHEREUM) is None
    assert decode_factory_log({**log, "removed": True}, ETHEREUM) is None


# --- GoPlus -------------------------------------------------------------------------


def test_parse_token_security():
    sec = parse_token_security(
        TOKEN, goplus_entry(), pool="0x1111111111111111111111111111111111111111"
    )
    assert sec.is_honeypot is False and sec.owner_renounced
    assert sec.lp_locked_pct == 90.0  # burned LP counts as locked
    assert sec.top10_holder_pct == 5.0  # pool and locked holders excluded
    assert sec.creator_pct == 2.0 and sec.holder_count == 1200
    assert parse_token_security(TOKEN, {}).is_honeypot is None


class FakeHttp:
    def __init__(self, response):
        self.response, self.requests = response, []

    def request(self, url, payload=None, headers=None):
        self.requests.append((url, headers))
        return self.response


def test_goplus_client_request_and_errors():
    http = FakeHttp({"code": 1, "message": "OK", "result": {TOKEN: goplus_entry()}})
    result = GoPlusClient(api_key="k", http=http).token_security("56", [TOKEN])
    url, headers = http.requests[0]
    assert (
        url
        == f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={TOKEN}"
    )
    assert headers == {"Authorization": "k"}
    assert TOKEN in result
    with pytest.raises(GoPlusError):
        GoPlusClient(http=FakeHttp({"code": 2, "message": "bad"})).token_security(
            "56", [TOKEN]
        )


# --- filters ---------------------------------------------------------------------------


POOL = "0x1111111111111111111111111111111111111111"


def cfg_hits(**overrides):
    sec = parse_token_security(TOKEN, goplus_entry(**overrides), pool=POOL)
    return security_rules(sec, FilterConfig())


def test_security_rules_drop_and_flag():
    assert cfg_hits() == []
    assert [h.rule_id for h in cfg_hits(is_honeypot="1")] == ["honeypot"]
    assert [h.rule_id for h in cfg_hits(sell_tax="0.25")] == ["high_tax"]
    # Owner privileges only matter while an owner exists.
    assert cfg_hits(is_mintable="1") == []
    owned = cfg_hits(
        is_mintable="1", owner_address="0xowner00000000000000000000000000000000001"
    )
    assert [h.rule_id for h in owned] == ["owner_privileges"]
    assert [h.rule_id for h in cfg_hits(is_open_source="0")] == ["not_open_source"]
    unlocked = cfg_hits(
        lp_holders=[{"address": "0xdev", "percent": "1", "is_locked": 0}]
    )
    assert [h.rule_id for h in unlocked] == ["lp_unlocked"]
    concentrated = cfg_hits(
        holders=[
            {"address": "0xbeef000000000000000000000000000000000001", "percent": "0.35"}
        ]
    )
    assert [(h.rule_id, h.action, h.weight) for h in concentrated] == [
        ("holder_concentration", FLAG, 3)
    ]
    flags = cfg_hits(is_proxy="1", creator_percent="0.2")
    assert [(h.rule_id, h.action) for h in flags] == [
        ("upgradeable_proxy", FLAG),
        ("creator_holds_supply", FLAG),
    ]


def test_market_decisions_on_sample():
    snaps = snapshots()
    assert evaluate(snaps["GOOD"], BSC).verdict == KEEP
    assert evaluate(snaps["USDT"], BSC).dropped_by == ["symbol_impersonation"]
    assert evaluate(snaps["DUST"], BSC).dropped_by == ["low_liquidity"]
    real_usdt = PoolSnapshot(
        "dexscreener",
        "p",
        "pancakeswap",
        USDT,
        base_symbol="USDT",
        quote_mint=WBNB,
        quote_symbol="WBNB",
    )
    assert evaluate(real_usdt, BSC).verdict == KEEP
    odd = PoolSnapshot(
        "dexscreener",
        "p",
        "pancakeswap",
        TOKEN,
        quote_mint="0x" + "7" * 40,
        quote_symbol="RND",
    )
    assert evaluate(odd, BSC).dropped_by == ["quote_not_allowed"]


def test_every_rule_has_a_known_stage():
    assert {r.stage for r in RULES.values()} == {
        "launch",
        "market",
        "security",
        "knowledge",
    }


# --- pipeline ------------------------------------------------------------------------


class FakeGoPlus:
    def __init__(self, entries):
        self.entries, self.calls = entries, []

    def token_security(self, chain_id, addresses, pool=""):
        self.calls.append(addresses[0])
        return {
            a.lower(): parse_token_security(a, self.entries[a.lower()], pool)
            for a in addresses
            if a.lower() in self.entries
        }


def test_pipeline_scan(tmp_path):
    tax = "0xa000000000000000000000000000000000000002"
    goplus = FakeGoPlus({TOKEN: goplus_entry(), tax: goplus_entry(sell_tax="0.3")})
    store = MarketStore(tmp_path / "m.db")
    pairs = json.loads(SAMPLE.read_text(encoding="utf-8"))["pairs"]
    results = Pipeline(store, BSC, goplus=goplus).process_pairs(pairs)
    assert len(results) == 4  # the Ethereum pair is ignored
    assert goplus.calls == [TOKEN, tax]  # market drops never reach GoPlus
    verdicts = {r.snapshot.base_symbol: r.decision.verdict for r in results}
    assert verdicts == {"GOOD": KEEP, "TAX": DROP, "USDT": DROP, "DUST": DROP}
    assert store.conn.execute("SELECT COUNT(*) FROM token_checks").fetchone()[0] == 2


class FakeRpc:
    def __init__(self, facts):
        self.facts = facts

    def pool_facts(self, event, chain):
        return self.facts[event.signature]


def test_pipeline_listener_events(tmp_path):
    pcs = BSC.factories[0]
    facts = {
        f"0x{i}": PoolFacts("0xcreator", [f"0xtoken{i}"], [WBNB], {WBNB: 10.0})
        for i in range(5)
    }
    facts["0xthin"] = PoolFacts("0xother", ["0xthin"], [WBNB], {WBNB: 0.1})
    facts["0xodd"] = PoolFacts("0xthird", ["0xaaa", "0xbbb"], [], {})
    goplus = FakeGoPlus(
        {f"0xtoken{i}": goplus_entry() for i in range(5)} | {"0xthin": goplus_entry()}
    )
    pipe = Pipeline(
        MarketStore(tmp_path / "m.db"), BSC, rpc=FakeRpc(facts), goplus=goplus
    )

    verdicts = []
    for i in range(5):
        event = NewPoolEvent(
            pcs.label,
            pcs.address,
            f"0x{i}",
            i,
            pool=f"0xpool{i}",
            tokens=(f"0xtoken{i}", WBNB.lower()),
        )
        (result,) = pipe.process_event(event)
        verdicts.append(result.decision.verdict)
        assert result.snapshot.quote_symbol == "WBNB"
    assert verdicts == [KEEP, KEEP, KEEP, DROP, DROP]  # serial creator after 3 pools

    thin = NewPoolEvent(
        pcs.label, pcs.address, "0xthin", 9, pool="0xp", tokens=("0xthin", WBNB.lower())
    )
    assert pipe.process_event(thin)[0].decision.dropped_by == ["low_initial_liquidity"]
    odd = NewPoolEvent(
        pcs.label, pcs.address, "0xodd", 10, pool="0xq", tokens=("0xaaa", "0xbbb")
    )
    assert all(
        "quote_not_allowed" in r.decision.dropped_by for r in pipe.process_event(odd)
    )
    assert pipe.process_event(odd) == []  # already seen


def test_poll_new_pools_walks_block_ranges():
    factory = BSC.factories[0]

    class Rpc:
        def __init__(self):
            self.ranges = []

        def block_number(self):
            return 110

        def factory_logs(self, chain, start, end):
            self.ranges.append((start, end))
            return [
                {
                    "address": factory.address,
                    "topics": [PAIR_CREATED, "0x" + word(TOKEN), "0x" + word(WBNB)],
                    "data": "0x"
                    + word("0x1111111111111111111111111111111111111111")
                    + word(1),
                    "transactionHash": f"0x{start}",
                    "blockNumber": hex(start),
                }
            ]

    rpc = Rpc()
    gen = poll_new_pools(rpc, BSC, start_block=100, max_range=4, confirmations=2)
    events = [next(gen) for _ in range(3)]
    assert rpc.ranges == [(100, 103), (104, 107), (108, 108)]
    assert [e.slot for e in events] == [100, 104, 108]


def test_cli_rules_and_offline_scan(tmp_path, capsys):
    cli(["--chain", "bsc", "rules"])
    assert "honeypot" in capsys.readouterr().out
    cli(
        [
            "--chain",
            "bsc",
            "scan",
            "--pairs-file",
            str(SAMPLE),
            "--no-security",
            "--db",
            str(tmp_path / "x.db"),
        ]
    )
    out = capsys.readouterr().out
    assert "共 4 個池子：保留 2，丟棄 2" in out
