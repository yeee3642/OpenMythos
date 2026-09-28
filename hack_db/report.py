"""Generate the statistics section of a case library's README from its database.

Usage:
    python -m hack_db.report ethereum_hacks        # prints Markdown
"""

from __future__ import annotations

import argparse
import sqlite3

from .build import Dataset


def usd_zh(value: float | None) -> str:
    """Format USD the way Taiwan readers expect: 億 / 萬 units."""
    if not value:
        return "0"
    if value >= 1e8:
        return f"{value / 1e8:.2f} 億美元"
    if value >= 1e4:
        return f"{value / 1e4:,.0f} 萬美元"
    return f"{value:,.0f} 美元"


def markdown_report(conn: sqlite3.Connection, chain_zh: str, as_of: str) -> str:
    q = conn.execute
    total, first, last, sources = q(
        "SELECT COUNT(*), MIN(date), MAX(date), (SELECT COUNT(*) FROM sources)"
        " FROM incidents"
    ).fetchone()
    single, single_loss = q(
        "SELECT COUNT(*), SUM(COALESCE(loss_usd, 0)) FROM incidents"
        " WHERE chain_scope != 'multi_chain'"
    ).fetchone()
    all_loss, unknown = q(
        "SELECT SUM(COALESCE(loss_usd, 0)), SUM(loss_usd IS NULL) FROM incidents"
    ).fetchone()
    conf = dict(q("SELECT confidence, COUNT(*) FROM incidents GROUP BY 1").fetchall())
    lines = [
        f"## 資料概況（截至 {as_of}）",
        "",
        f"- **{total} 起事件**：時間範圍 {first} 至 {last}，共 {sources} 筆參考來源。",
        (
            f"- **只影響 {chain_zh} 的事件 {single} 起**，損失合計約"
            f" **{usd_zh(single_loss)}**；含多鏈事件合計約 {usd_zh(all_loss)}。"
            f"{unknown} 起事件的美元損失不明，未計入合計。"
        ),
        (
            f"- **資料可信度**：high {conf.get('high', 0)} 筆、"
            f"medium {conf.get('medium', 0)} 筆、low {conf.get('low', 0)} 筆。"
        ),
        "",
        "### 年度統計",
        "",
        "| 年份 | 事件數 | 總損失 | 單鏈損失 | 單一最大損失 |",
        "|---|---:|---:|---:|---:|",
    ]
    for year, n, loss, single_y, _rec, biggest in q(
        "SELECT year, incidents, total_loss_usd, single_chain_loss_usd,"
        " total_recovered_usd, largest_loss_usd FROM v_yearly_summary"
    ):
        lines.append(
            f"| {year} | {n} | {usd_zh(loss)} | {usd_zh(single_y)} | {usd_zh(biggest)} |"
        )
    lines += [
        "",
        "### 損失前十名",
        "",
        "| 日期 | 事件 | 類別 | 損失 |",
        "|---|---|---|---:|",
    ]
    for date, project, cat, loss in q(
        "SELECT date, project, category_zh, loss_usd FROM v_incidents"
        " WHERE loss_usd IS NOT NULL ORDER BY loss_usd DESC LIMIT 10"
    ):
        lines.append(f"| {date} | {project} | {cat} | {usd_zh(loss)} |")
    lines += [
        "",
        "### 攻擊類別",
        "",
        "| 類別 | 事件數 | 損失 |",
        "|---|---:|---:|",
    ]
    for cat, n, loss in q(
        "SELECT category_zh, incidents, total_loss_usd FROM v_category_summary"
    ):
        lines.append(f"| {cat} | {n} | {usd_zh(loss)} |")
    lines += [
        "",
        "### 邏輯漏洞模式",
        "",
        "| 漏洞模式 | 事件數 | 損失 |",
        "|---|---:|---:|",
    ]
    for pattern, n, loss in q(
        "SELECT vuln_pattern_zh, incidents, total_loss_usd FROM v_vuln_pattern_summary"
        " WHERE incidents > 0"
    ):
        lines.append(f"| {pattern} | {n} | {usd_zh(loss)} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="hack_db.report", description=__doc__.split("\n")[0]
    )
    parser.add_argument("dataset")
    parser.add_argument("--as-of", default="2026-09-28")
    args = parser.parse_args(argv)
    ds = Dataset(args.dataset)
    conn = sqlite3.connect(ds.db_path)
    chain_zh = ds.lookups()["dataset"]["chain_zh"]
    print(markdown_report(conn, chain_zh, args.as_of), end="")


if __name__ == "__main__":
    main()
