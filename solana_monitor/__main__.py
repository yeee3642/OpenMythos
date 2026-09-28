"""Command-line entry point: ``python -m solana_monitor <command>``.

Commands:
  rules   列出所有過濾規則（過濾掉什麼、留下什麼）
  scan    從 DexScreener 取得最新 Solana 代幣的池子並過濾
  listen  監聽 Raydium / Pump.fun / Meteora / Orca 新池建立事件並過濾
  report  統計已收集資料中被丟棄的原因與保留的池子
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .case_library import DEFAULT_CASE_DB, CaseLibrary
from .config import DEFAULT_RPC_URL, DEFAULT_WS_URL, DEX_PROGRAMS, FilterConfig
from .filters import RULES
from .models import DROP
from .pipeline import Pipeline, Result
from .sources.dexscreener import DexScreenerClient
from .sources.solana_rpc import SolanaRpcClient
from .store import DEFAULT_MARKET_DB, MarketStore


def _print_result(r: Result) -> None:
    s, d = r.snapshot, r.decision
    label = "丟棄" if d.verdict == DROP else "保留"
    liq = f"${s.liquidity_usd:,.0f}" if s.liquidity_usd is not None else "-"
    print(
        f"[{label}] {s.dex_id:<9} {s.base_symbol or s.base_mint[:8]:<12} 流動性 {liq:<12} 風險分 {d.risk_score}"
    )
    for h in d.hits:
        print(f"    {'✗' if h.action == DROP else '!'} {h.reason_zh}")


def _pipeline(args) -> Pipeline:
    cfg = FilterConfig.from_file(args.config) if args.config else FilterConfig()
    cases = CaseLibrary(args.case_db) if Path(args.case_db).exists() else None
    rpc = None if getattr(args, "no_rpc", False) else SolanaRpcClient(args.rpc)
    return Pipeline(MarketStore(args.db), rpc, cfg, cases)


def cmd_rules(_args) -> None:
    for stage in ("market", "onchain", "knowledge"):
        print(f"\n== {stage} ==")
        for r in RULES.values():
            if r.stage == stage:
                refs = f"  [案例: {', '.join(r.case_refs)}]" if r.case_refs else ""
                print(
                    f"- {r.rule_id} ({r.action}) {r.title_zh}：{r.rationale_zh}{refs}"
                )


def cmd_scan(args) -> None:
    pipe = _pipeline(args)
    if args.pairs_file:
        data = json.loads(Path(args.pairs_file).read_text(encoding="utf-8"))
        pairs = data.get("pairs", []) if isinstance(data, dict) else data
    else:
        dex = DexScreenerClient()
        tokens = dex.latest_token_addresses()[: args.limit]
        pairs = dex.pairs_for_tokens(tokens)
    results = pipe.process_pairs(pairs)
    for r in results:
        _print_result(r)
    kept = sum(r.decision.verdict != DROP for r in results)
    print(f"\n共 {len(results)} 個池子：保留 {kept}，丟棄 {len(results) - kept}")


async def _listen(args) -> None:
    from .sources.pool_listener import listen

    pipe = _pipeline(args)
    wanted = set(args.programs.split(",")) if args.programs else None
    programs = [p for p in DEX_PROGRAMS if wanted is None or p.label in wanted]
    print("監聽中：" + ", ".join(p.label for p in programs))
    async for event in listen(args.ws, programs):
        # RPC calls are blocking; run them off the event loop.
        results = await asyncio.to_thread(pipe.process_event, event)
        for r in results:
            _print_result(r)


def cmd_report(args) -> None:
    store = MarketStore(args.db)
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
    for dex_id, symbol, mint, liq, score, flags in rows:
        liq_s = f"${liq:,.0f}" if liq is not None else "-"
        print(
            f"  {dex_id:<9} {symbol or mint[:8]:<12} {liq_s:<12} 風險分 {score}  {flags or ''}"
        )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="solana_monitor", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, rpc=True):
        p.add_argument(
            "--db", type=Path, default=DEFAULT_MARKET_DB, help="市場資料 SQLite 路徑"
        )
        p.add_argument(
            "--case-db", type=Path, default=DEFAULT_CASE_DB, help="案例庫路徑"
        )
        p.add_argument("--config", type=Path, help="過濾門檻 JSON 設定檔")
        if rpc:
            p.add_argument("--rpc", default=DEFAULT_RPC_URL, help="Solana RPC URL")

    sub.add_parser("rules", help="列出過濾規則").set_defaults(func=cmd_rules)

    scan = sub.add_parser("scan", help="從 DexScreener 抓取並過濾")
    common(scan)
    scan.add_argument("--limit", type=int, default=30, help="最多處理幾個代幣")
    scan.add_argument("--no-rpc", action="store_true", help="不做鏈上 mint 檢查")
    scan.add_argument(
        "--pairs-file", type=Path, help="改用本機的 DexScreener 回應 JSON"
    )
    scan.set_defaults(func=cmd_scan)

    lis = sub.add_parser("listen", help="監聽新池建立事件")
    common(lis)
    lis.add_argument("--ws", default=DEFAULT_WS_URL, help="Solana WebSocket URL")
    lis.add_argument("--programs", help="只監聽這些 DEX（逗號分隔 label）")
    lis.set_defaults(func=lambda a: asyncio.run(_listen(a)))

    rep = sub.add_parser("report", help="統計收集結果")
    rep.add_argument("--db", type=Path, default=DEFAULT_MARKET_DB)
    rep.add_argument("--limit", type=int, default=20)
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
