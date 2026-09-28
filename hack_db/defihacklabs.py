"""Import incidents from DeFiHackLabs (github.com/SunWeb3Sec/DeFiHackLabs).

DeFiHackLabs lists EVM DeFi exploits, each with a Foundry proof-of-concept
(``src/test/<yyyy-mm>/<Name>_exp.sol``). This module downloads the lists and
PoC files, detects each incident's chain from the PoC's fork call and
explorer links, and writes per-chain input files for the record-writing
workflow in ``hack_db/workflows/dhl_case_records.js``.

Usage:
    python -m hack_db.defihacklabs OUT_DIR          # download + build inputs
    python -m hack_db.defihacklabs OUT_DIR --offline  # reuse downloaded files
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

RAW = "https://raw.githubusercontent.com/SunWeb3Sec/DeFiHackLabs/main/"
BLOB = "https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/"
PAST_YEARS = range(2021, 2026)

EXPLORER_CHAIN = (
    ("bscscan.com", "bsc"),
    ("optimistic.etherscan.io", "optimism"),
    ("etherscan.io", "ethereum"),
    ("arbiscan.io", "arbitrum"),
    ("basescan.org", "base"),
    ("polygonscan.com", "polygon"),
    ("snowtrace.io", "avalanche"),
    ("ftmscan.com", "fantom"),
)
FORK_ALIAS = {
    "mainnet": "ethereum",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "binance": "bsc",
    "arbitrum": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "optimism": "optimism",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "blast": "blast",
    "linea": "linea",
    "scroll": "scroll",
    "zksync": "zksync",
    "gnosis": "gnosis",
    "sonic": "sonic",
}
PUBLISHERS = {
    "x.com": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "medium.com": "Medium",
    "rekt.news": "rekt.news",
    "etherscan.io": "Etherscan",
    "bscscan.com": "BscScan",
    "github.com": "GitHub",
}
ANALYSIS_LINK = re.compile(
    r"https?://(?:x\.com|twitter\.com|medium\.com|[\w.-]*mirror\.xyz|rekt\.news"
    r"|[\w.-]*(?:blocksec|peckshield|certik|slowmist|halborn|quillaudits"
    r"|neptunemutual|defimon)[\w.-]*|github\.com)/[^\s\"')]+"
)
EXPLORER_LINK = re.compile(
    r"https?://(?:[\w-]+\.)?(?:bscscan\.com|etherscan\.io)/(?:tx|address)/0x[0-9a-fA-F]+"
)


def parse_entries(markdown: str) -> list[dict]:
    """Parse '### YYYYMMDD Name - root cause' blocks (both list formats)."""
    entries = []
    for block in re.split(r"(?m)^### (?=\d{8} )", markdown)[1:]:
        head = block.splitlines()[0]
        m = re.match(r"(\d{8})\s+(?:-\s+)?(.+?)(?:\s+-\s+(.*))?$", head)
        if not m:
            continue
        lost = re.search(r"(?m)^### Lost:\s*(.*)$", block)
        poc = re.search(r"(src/test/[\w./-]+?\.sol)", block)
        links = re.findall(r"https?://[^\s)\]>]+", block)
        d = m.group(1)
        entries.append(
            {
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "name": m.group(2).strip(),
                "root_cause": (m.group(3) or "").strip(),
                "lost": lost.group(1).strip() if lost else "",
                "poc": poc.group(1) if poc else "",
                "links": [u.rstrip(".,") for u in links if "blastapi" not in u],
            }
        )
    return entries


def detect_chain(poc_source: str) -> str | None:
    m = re.search(
        r'createSelectFork\(\s*(?:vm\.rpcUrl\()?\s*"([A-Za-z_]+)"', poc_source
    )
    if m and m.group(1).lower() in FORK_ALIAS:
        return FORK_ALIAS[m.group(1).lower()]
    for host, chain in EXPLORER_CHAIN:
        if host in poc_source:
            return chain
    return None


def parse_usd(text: str) -> float | None:
    """Explicit USD amounts ('$1.2M', '~$30k', '100k USD'); None otherwise."""
    m = re.search(
        r"\$\s?~?([\d,.]+)\s*([kKmMbB]|million|billion)?"
        r"|([\d,.]+)\s*([kKmMbB]|million|billion)?\s*(?:USD|usd)",
        text or "",
    )
    if not m:
        return None
    try:
        value = float((m.group(1) or m.group(3)).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or m.group(4) or "").lower()
    mult = {"k": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
    return round(value * mult.get(unit, 1), 2)


def publisher(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in PUBLISHERS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def candidate_sources(entry: dict, poc_source: str) -> list[dict]:
    """Real URLs only: the PoC, analysis links and the attack transaction."""
    sources = [
        {
            "title": f"DeFiHackLabs PoC: {entry['name']}",
            "publisher": "DeFiHackLabs (GitHub)",
            "url": BLOB + entry["poc"],
        }
    ]
    links = []
    for url in entry["links"] + ANALYSIS_LINK.findall(poc_source):
        url = url.rstrip(".,")
        if url not in links and "DeFiHackLabs" not in url:
            links.append(url)
    sources += [
        {
            "title": f"{entry['name']} incident analysis",
            "publisher": publisher(u),
            "url": u,
        }
        for u in links[:3]
    ]
    tx = [u for u in EXPLORER_LINK.findall(poc_source) if "/tx/" in u][:1]
    sources += [
        {
            "title": f"{entry['name']} attack transaction",
            "publisher": publisher(u),
            "url": u,
        }
        for u in tx
    ]
    return sources


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def download(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(_fetch(RAW + "README.md"), encoding="utf-8")
    for year in PAST_YEARS:
        text = _fetch(f"{RAW}past/{year}/README.md")
        (out_dir / f"past_{year}.md").write_text(text, encoding="utf-8")


def load_entries(out_dir: Path) -> list[dict]:
    text = (out_dir / "README.md").read_text(encoding="utf-8")
    marker = "### List of DeFi Hacks & POCs"
    entries = parse_entries(text[text.index(marker) :] if marker in text else text)
    for year in PAST_YEARS:
        path = out_dir / f"past_{year}.md"
        if path.exists():
            entries += parse_entries(path.read_text(encoding="utf-8"))
    seen, unique = set(), []
    for e in entries:
        key = (e["date"], e["name"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def download_pocs(entries: list[dict], out_dir: Path, workers: int = 8) -> None:
    def get(entry):
        path = out_dir / "poc" / entry["poc"]
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(_fetch(RAW + entry["poc"]), encoding="utf-8")
        except OSError:
            pass

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(get, [e for e in entries if e["poc"]]))


def build_inputs(entries: list[dict], out_dir: Path, chains=("ethereum", "bsc")):
    """Write <chain>_inputs.json for each chain; returns counts per chain."""
    per_chain: dict[str, list[dict]] = {c: [] for c in chains}
    for e in entries:
        path = out_dir / "poc" / e["poc"] if e["poc"] else None
        if not path or not path.exists():
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if source.startswith("404: Not Found"):
            continue
        chain = detect_chain(source)
        if chain not in per_chain:
            continue
        per_chain[chain].append(
            {
                "date": e["date"],
                "name": e["name"],
                "dhl_root_cause": e["root_cause"],
                "lost": e["lost"],
                "loss_usd_parsed": parse_usd(e["lost"]),
                "poc_path": str(path.resolve()),
                "candidate_sources": candidate_sources(e, source),
            }
        )
    for chain, items in per_chain.items():
        items.sort(key=lambda x: x["date"])
        (out_dir / f"{chain}_inputs.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return {c: len(v) for c, v in per_chain.items()}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="hack_db.defihacklabs", description=__doc__.split("\n")[0]
    )
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--offline", action="store_true", help="reuse downloaded files")
    args = parser.parse_args(argv)
    if not args.offline:
        download(args.out_dir)
    entries = load_entries(args.out_dir)
    if not args.offline:
        download_pocs(entries, args.out_dir)
    counts = build_inputs(entries, args.out_dir)
    print(f"{len(entries)} DeFiHackLabs entries; inputs written: {counts}")


if __name__ == "__main__":
    main()
