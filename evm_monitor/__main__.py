"""Command-line entry point: ``python -m evm_monitor --chain bsc <command>``.

Commands:
  rules   列出所有過濾規則（過濾掉什麼、留下什麼）
  scan    從 DexScreener 取得最新代幣的池子並過濾
  listen  監聽 Uniswap / PancakeSwap / SushiSwap 工廠合約的新池事件並過濾
  report  統計已收集資料中被丟棄的原因與保留的池子
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from monitor_common.ave import AveClient
from monitor_common.case_library import CaseLibrary, case_db_path
from monitor_common.dexscreener import DexScreenerClient

from .config import CHAINS, FilterConfig
from .filters import RULES
from .models import DROP
from .pipeline import Pipeline, Result
from .sources.evm_rpc import EvmRpcClient
from .sources.goplus import GoPlusClient
from .store import MarketStore, default_db


def _print_result(r: Result) -> None:
    s, d = r.snapshot, r.decision
    label = "丟棄" if d.verdict == DROP else "保留"
    liq = f"${s.liquidity_usd:,.0f}" if s.liquidity_usd is not None else "-"
    name = s.base_symbol or s.base_mint[:10]
    print(f"[{label}] {s.dex_id:<12} {name:<12} 流動性 {liq:<12} 風險分 {d.risk_score}")
    for h in d.hits:
        print(f"    {'✗' if h.action == DROP else '!'} {h.reason_zh}")


def _pipeline(args) -> Pipeline:
    chain = CHAINS[args.chain]
    cfg = FilterConfig.from_file(args.config) if args.config else FilterConfig()
    case_db = case_db_path(chain.case_dataset)
    cases = CaseLibrary(case_db) if case_db.exists() else None
    rpc = EvmRpcClient(args.rpc or chain.rpc_url)
    goplus = None if getattr(args, "no_security", False) else GoPlusClient()
    ave = AveClient(chain=chain.ave_chain) if getattr(args, "ave", False) else None
    store = MarketStore(args.db or default_db(chain.key))
    return Pipeline(store, chain, rpc, goplus, cfg, cases, ave)


def cmd_rules(_args) -> None:
    for stage in ("launch", "market", "security", "knowledge"):
        print(f"\n== {stage} ==")
        for r in RULES.values():
            if r.stage == stage:
                print(f"- {r.rule_id} ({r.action}) {r.title_zh}：{r.rationale_zh}")


def cmd_scan(args) -> None:
    pipe = _pipeline(args)
    if args.pairs_file:
        data = json.loads(Path(args.pairs_file).read_text(encoding="utf-8"))
        pairs = data.get("pairs", []) if isinstance(data, dict) else data
    else:
        dex = DexScreenerClient()
        tokens = dex.latest_token_addresses(pipe.chain.dexscreener_id)[: args.limit]
        pairs = dex.pairs_for_tokens(tokens, pipe.chain.dexscreener_id)
    results = pipe.process_pairs(pairs)
    for r in results:
        _print_result(r)
    kept = sum(r.decision.verdict != DROP for r in results)
    print(f"\n共 {len(results)} 個池子：保留 {kept}，丟棄 {len(results) - kept}")


def cmd_listen(args) -> None:
    from .sources.factory_listener import poll_new_pools, subscribe_new_pools

    pipe = _pipeline(args)
    chain = pipe.chain
    print(f"監聽 {chain.name_zh}：" + ", ".join(f.label for f in chain.factories))
    ws_url = args.ws or chain.ws_url
    if ws_url:

        async def run():
            async for event in subscribe_new_pools(ws_url, chain):
                for r in await asyncio.to_thread(pipe.process_event, event):
                    _print_result(r)

        asyncio.run(run())
    else:
        for event in poll_new_pools(pipe.rpc, chain, interval_s=args.interval):
            for r in pipe.process_event(event):
                _print_result(r)


def cmd_report(args) -> None:
    store = MarketStore(args.db or default_db(args.chain))
    print("被丟棄的原因：")
    for stage, rule_id, title, pools in store.conn.execute(
        "SELECT * FROM v_drop_reasons"
    ):
        print(f"  {pools:>6}  [{stage}] {title} ({rule_id})")
    print("\n最近保留的池子：")
    rows = store.conn.execute(
        "SELECT dex_id, base_symbol, base_mint, liquidity_usd, risk_score, flags"
        " FROM v_kept LIMIT ?",
        (args.limit,),
    )
    for dex_id, symbol, token, liq, score, flags in rows:
        liq_s = f"${liq:,.0f}" if liq is not None else "-"
        print(
            f"  {dex_id:<12} {symbol or token[:10]:<12} {liq_s:<12} 風險分 {score}  {flags or ''}"
        )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="evm_monitor", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--chain", choices=sorted(CHAINS), default="bsc")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--db", type=Path, help="市場資料 SQLite 路徑（預設依鏈分開）")
        p.add_argument("--config", type=Path, help="過濾門檻 JSON 設定檔")
        p.add_argument("--rpc", help="RPC URL（預設讀 ETH_RPC_URL / BSC_RPC_URL）")
        p.add_argument(
            "--ave", action="store_true", help="保留的池子另外向 AVE 取風險報告存檔"
        )

    sub.add_parser("rules", help="列出過濾規則").set_defaults(func=cmd_rules)

    scan = sub.add_parser("scan", help="從 DexScreener 抓取並過濾")
    common(scan)
    scan.add_argument("--limit", type=int, default=30)
    scan.add_argument("--no-security", action="store_true", help="不查 GoPlus 合約安全")
    scan.add_argument(
        "--pairs-file", type=Path, help="改用本機的 DexScreener 回應 JSON"
    )
    scan.set_defaults(func=cmd_scan)

    lis = sub.add_parser("listen", help="監聽工廠合約的新池事件")
    common(lis)
    lis.add_argument("--ws", help="WebSocket URL（沒有就用 eth_getLogs 輪詢）")
    lis.add_argument("--interval", type=float, default=3.0, help="輪詢間隔秒數")
    lis.set_defaults(func=cmd_listen)

    rep = sub.add_parser("report", help="統計收集結果")
    rep.add_argument("--db", type=Path)
    rep.add_argument("--limit", type=int, default=20)
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
