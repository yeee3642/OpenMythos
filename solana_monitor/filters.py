"""Pool / token filters: decide what to drop and what to keep.

Rules run in four stages:

0. ``launch``    – facts from the pool-creation transaction (listener only);
1. ``market``    – cheap checks on market data (DexScreener or listener);
2. ``onchain``   – token-mint safety checks read from Solana RPC;
3. ``knowledge`` – checks against the hack case library.

A single DROP hit discards the pool. FLAG hits keep it but add to its risk
score. Every hit is stored with its reason, so what was filtered out stays
auditable. ``case_refs`` point to incident ids in solana_hacks/.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from monitor_common.rules import Rule, decide, make_hit, rule_table

from .case_library import CaseLibrary
from .config import (
    DEX_ID_TO_CASE_PROJECTS,
    QUOTE_MINTS,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    FilterConfig,
)
from .models import DROP, FLAG, Decision, MintInfo, PoolSnapshot, RuleHit

RULES = rule_table(
    # --- stage 1: market data -------------------------------------------------
    Rule(
        "quote_not_allowed",
        "market",
        DROP,
        "報價代幣不在白名單",
        "只保留以 SOL / USDC / USDT 報價的池子；其他報價代幣流動性差、價格容易被操縱，也常被用來製造假流動性。",
    ),
    Rule(
        "low_liquidity",
        "market",
        DROP,
        "流動性過低",
        "流動性太低的池子價格很容易被操縱。Mango、Solend USDH、Loopscale 都是先拉抬低流動性資產的價格，再以此借出真實資產。",
        (
            "mango-markets-2022",
            "solend-usdh-oracle-manipulation-2022",
            "loopscale-2025",
        ),
    ),
    Rule(
        "inactive_pool",
        "market",
        DROP,
        "沒有交易活動",
        "過去一小時完全沒有買賣，屬於死池或垃圾池，只是雜訊。",
    ),
    Rule(
        "symbol_impersonation",
        "market",
        DROP,
        "冒充知名代幣",
        "代幣符號與知名代幣相同但 mint 地址不同。駭客挾持 Jupiter、Pump.fun 的 X 帳號後，就是推廣這類假冒代幣。",
        ("jupiter-x-account-hijack-2025", "pumpfun-x-account-hijack-2025"),
    ),
    Rule(
        "honeypot_suspect",
        "market",
        DROP,
        "疑似貔貅盤（只能買不能賣）",
        "一小時內大量買入卻完全沒有賣出，通常代表賣出被凍結權限、transfer hook 或黑名單擋下。",
    ),
    Rule(
        "deprecated_program",
        "market",
        DROP,
        "池子建立在已淘汰的程式上",
        "已淘汰但沒有關閉的舊程式仍是攻擊面：2026 年 Raydium 舊版 AMM V3 池被偽造 LP mint 提走資產。",
        ("raydium-legacy-amm-v3-2026",),
    ),
    Rule(
        "fdv_liquidity_ratio",
        "market",
        FLAG,
        "估值遠高於流動性",
        "完全稀釋估值 (FDV) 是流動性的數十倍以上，少量賣壓就會讓價格崩跌。",
        weight=2,
    ),
    # --- stage 0: pool-creation transaction (listener) ---------------------------
    Rule(
        "low_initial_liquidity",
        "launch",
        DROP,
        "建池時注入的流動性過低",
        "建池交易存入的 SOL / USDC / USDT 太少，多半是測試池、垃圾池或拉盤前的誘餌。",
    ),
    Rule(
        "serial_creator",
        "launch",
        DROP,
        "同一錢包短時間大量建池",
        "同一個建池錢包 24 小時內開了多個池子，典型的批量發幣、割完就跑的模式。",
    ),
    # --- stage 2: on-chain mint checks ------------------------------------------
    Rule(
        "onchain_check_failed",
        "onchain",
        DROP,
        "無法讀取鏈上 mint 資料",
        "讀不到 mint 帳戶就無法確認權限與擴充功能，一律丟棄（寧可錯殺，不可放過）。",
    ),
    Rule(
        "unknown_token_program",
        "onchain",
        DROP,
        "非標準代幣程式",
        "mint 不屬於 SPL Token 或 Token-2022 程式，行為無法預期。",
    ),
    Rule(
        "mint_authority_active",
        "onchain",
        DROP,
        "增發權限未放棄",
        "mint authority 仍在，發行者可以隨時無限增發。Cashio 被無限鑄幣後，CASH 價格在數小時內歸零。",
        ("cashio-2022",),
    ),
    Rule(
        "freeze_authority_active",
        "onchain",
        DROP,
        "凍結權限未放棄",
        "freeze authority 可以凍結任何持有人的代幣帳戶，是 Solana 上常見的貔貅盤手法。",
    ),
    Rule(
        "permanent_delegate",
        "onchain",
        DROP,
        "Token-2022 永久委託",
        "permanent delegate 可以不經同意轉走或銷毀任何人的代幣。",
    ),
    Rule(
        "transfer_hook",
        "onchain",
        DROP,
        "Token-2022 transfer hook",
        "每次轉帳都會呼叫發行者指定的程式，可以用來阻擋賣出。",
    ),
    Rule(
        "non_transferable",
        "onchain",
        DROP,
        "Token-2022 不可轉讓",
        "代幣無法轉讓，買了就賣不掉。",
    ),
    Rule(
        "default_frozen",
        "onchain",
        DROP,
        "Token-2022 新帳戶預設凍結",
        "新建立的代幣帳戶預設為凍結狀態，需要發行者解凍才能轉出。",
    ),
    Rule(
        "pausable",
        "onchain",
        DROP,
        "Token-2022 可暫停",
        "發行者可以暫停所有轉帳。",
    ),
    Rule(
        "transfer_fee",
        "onchain",
        DROP,
        "Token-2022 轉帳手續費過高",
        "轉帳手續費超過上限；手續費權限者也能事後調高費率。低於上限時只標記為風險。",
    ),
    Rule(
        "confidential_transfer",
        "onchain",
        FLAG,
        "Token-2022 機密轉帳",
        "ZK ElGamal 證明程式在 2025 年兩度被發現可偽造證明（可無限鑄造機密代幣），之後被停用。",
        ("zk-elgamal-proof-bug-2025-04", "zk-elgamal-proof-bug-2025-06"),
        weight=1,
    ),
    Rule(
        "mint_close_authority",
        "onchain",
        FLAG,
        "Token-2022 mint 可關閉",
        "mint close authority 可以在供給歸零後關閉 mint，再用相同地址重建。",
        weight=1,
    ),
    Rule(
        "holder_concentration",
        "onchain",
        DROP,
        "持幣過度集中",
        "前 10 大持有人（已排除池子與銷毀地址）持有過高比例，隨時可以砸盤。低於丟棄門檻但超過標記門檻時只標記為風險。",
    ),
    # --- stage 3: case library --------------------------------------------------
    Rule(
        "protocol_recent_incident",
        "knowledge",
        FLAG,
        "所在協議近期曾遭攻擊",
        "池子所在的 DEX / 協議在案例庫中近期有造成損失的資安事件。",
        weight=2,
    ),
)


def _hit(rule_id: str, detail: str = "", action: str | None = None, refs=None):
    return make_hit(RULES, rule_id, detail, action, refs)


def market_rules(snap: PoolSnapshot, cfg: FilterConfig) -> list[RuleHit]:
    hits = []
    quote_ok = (
        snap.quote_mint in QUOTE_MINTS
        or snap.quote_symbol.upper() in cfg.allowed_quote_symbols
    )
    if (snap.quote_mint or snap.quote_symbol) and not quote_ok:
        hits.append(_hit("quote_not_allowed", snap.quote_symbol or snap.quote_mint))
    if snap.liquidity_usd is not None and snap.liquidity_usd < cfg.min_liquidity_usd:
        hits.append(
            _hit(
                "low_liquidity",
                f"${snap.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}",
            )
        )
    if snap.buys_h1 == 0 and snap.sells_h1 == 0:
        hits.append(_hit("inactive_pool"))
    symbol = snap.base_symbol.upper().lstrip("$")
    canonical = cfg.canonical_mints.get(symbol)
    if canonical and snap.base_mint != canonical:
        hits.append(_hit("symbol_impersonation", f"{snap.base_symbol} ≠ {canonical}"))
    if (
        snap.buys_h1 is not None
        and snap.buys_h1 >= cfg.honeypot_min_buys_h1
        and snap.sells_h1 == 0
    ):
        hits.append(_hit("honeypot_suspect", f"1 小時內 {snap.buys_h1} 買 / 0 賣"))
    if snap.program_id and snap.program_id in cfg.deprecated_programs:
        hits.append(_hit("deprecated_program", snap.program_id))
    if snap.fdv_usd and snap.liquidity_usd:
        ratio = snap.fdv_usd / snap.liquidity_usd
        if ratio > cfg.max_fdv_to_liquidity:
            hits.append(_hit("fdv_liquidity_ratio", f"FDV / 流動性 = {ratio:.0f}"))
    return hits


def launch_rules(snap: PoolSnapshot, cfg: FilterConfig) -> list[RuleHit]:
    hits = []
    minimum = cfg.min_initial_quote.get(snap.quote_symbol.upper())
    if (
        snap.initial_quote_amount is not None
        and minimum is not None
        and snap.initial_quote_amount < minimum
    ):
        hits.append(
            _hit(
                "low_initial_liquidity",
                f"{snap.initial_quote_amount:,.2f} {snap.quote_symbol} < {minimum:,.0f}",
            )
        )
    if (
        snap.creator_recent_pools is not None
        and snap.creator_recent_pools >= cfg.max_creator_pools_24h
    ):
        hits.append(
            _hit("serial_creator", f"24 小時內已建 {snap.creator_recent_pools} 個池")
        )
    return hits


def onchain_rules(mint: MintInfo, cfg: FilterConfig) -> list[RuleHit]:
    if mint.token_program not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        return [_hit("unknown_token_program", mint.token_program)]
    hits = []
    exempt = mint.mint in cfg.authority_allowlist
    if mint.mint_authority and not exempt:
        hits.append(_hit("mint_authority_active", mint.mint_authority))
    if mint.freeze_authority and not exempt:
        hits.append(_hit("freeze_authority_active", mint.freeze_authority))

    ext = mint.extensions
    if (ext.get("permanentDelegate") or {}).get("delegate"):
        hits.append(_hit("permanent_delegate", ext["permanentDelegate"]["delegate"]))
    if (ext.get("transferHook") or {}).get("programId"):
        hits.append(_hit("transfer_hook", ext["transferHook"]["programId"]))
    if "nonTransferable" in ext:
        hits.append(_hit("non_transferable"))
    if (ext.get("defaultAccountState") or {}).get("accountState") == "frozen":
        hits.append(_hit("default_frozen"))
    pausable = ext.get("pausableConfig")
    if pausable is not None and (pausable.get("authority") or pausable.get("paused")):
        hits.append(_hit("pausable"))
    if "transferFeeConfig" in ext:
        cfg_fee = ext["transferFeeConfig"]
        bps = max(
            (cfg_fee.get(k) or {}).get("transferFeeBasisPoints", 0)
            for k in ("olderTransferFee", "newerTransferFee")
        )
        if bps > cfg.max_transfer_fee_bps:
            hits.append(_hit("transfer_fee", f"{bps / 100:.2f}%"))
        elif bps > 0 or cfg_fee.get("transferFeeConfigAuthority"):
            hits.append(_hit("transfer_fee", f"{bps / 100:.2f}%", action=FLAG))
    if "confidentialTransferMint" in ext:
        hits.append(_hit("confidential_transfer"))
    if (ext.get("mintCloseAuthority") or {}).get("closeAuthority"):
        hits.append(_hit("mint_close_authority"))

    pct = mint.top10_holder_pct
    if pct is not None:
        if pct > cfg.drop_top10_holder_pct:
            hits.append(_hit("holder_concentration", f"前 10 大持有 {pct:.1f}%"))
        elif pct > cfg.flag_top10_holder_pct:
            hits.append(
                _hit("holder_concentration", f"前 10 大持有 {pct:.1f}%", action=FLAG)
            )
    return hits


def knowledge_rules(
    snap: PoolSnapshot, cfg: FilterConfig, cases: CaseLibrary, as_of: date
) -> list[RuleHit]:
    projects = DEX_ID_TO_CASE_PROJECTS.get(snap.dex_id, ())
    recent = cases.recent_incidents(projects, as_of, cfg.recent_incident_days)
    if not recent:
        return []
    detail = "、".join(f"{d} {p}" for _, d, p in recent)
    return [_hit("protocol_recent_incident", detail, refs=[i for i, _, _ in recent])]


def evaluate(
    snap: PoolSnapshot,
    mint: MintInfo | None = None,
    cfg: FilterConfig | None = None,
    cases: CaseLibrary | None = None,
    as_of: date | None = None,
    onchain_error: str | None = None,
) -> Decision:
    cfg = cfg or FilterConfig()
    hits = launch_rules(snap, cfg) + market_rules(snap, cfg)
    if mint is not None:
        hits += onchain_rules(mint, cfg)
    elif onchain_error:
        hits.append(_hit("onchain_check_failed", onchain_error))
    if cases is not None:
        hits += knowledge_rules(
            snap, cfg, cases, as_of or datetime.now(timezone.utc).date()
        )
    return decide(hits)
