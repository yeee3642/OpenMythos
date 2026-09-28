"""EVM pool / token filters (Ethereum, BSC): decide what to drop and what to keep.

Stages:

0. ``launch``    – facts from the pool-creation transaction (listener only);
1. ``market``    – market data (DexScreener);
2. ``security``  – contract risk from GoPlus token_security;
3. ``knowledge`` – recent protocol incidents from the chain's case library.

EVM token permissions live in contract code (unlike Solana's mint account),
so stage 2 relies on GoPlus's static and simulated analysis.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from monitor_common.case_library import CaseLibrary
from monitor_common.rules import Rule, decide, make_hit, rule_table

from .config import DEX_ID_TO_CASE_PROJECTS, Chain, FilterConfig
from .models import DROP, FLAG, Decision, PoolSnapshot, RuleHit, TokenSecurity

RULES = rule_table(
    # --- stage 0: pool-creation transaction ---------------------------------------
    Rule(
        "low_initial_liquidity",
        "launch",
        DROP,
        "建池時注入的流動性過低",
        "建池時存進池子的 WETH / WBNB / 穩定幣太少，多半是測試池、垃圾池或誘餌。",
    ),
    Rule(
        "serial_creator",
        "launch",
        DROP,
        "同一錢包短時間大量建池",
        "同一個部署 / 建池錢包 24 小時內開了多個池子，典型的批量發幣模式。",
    ),
    # --- stage 1: market data -------------------------------------------------------
    Rule(
        "quote_not_allowed",
        "market",
        DROP,
        "報價代幣不在白名單",
        "只保留以原生代幣（WETH / WBNB）或主流穩定幣報價的池子。",
    ),
    Rule(
        "low_liquidity",
        "market",
        DROP,
        "流動性過低",
        "流動性太低的池子價格容易被操縱，也是預言機操縱攻擊的常見起點。",
    ),
    Rule("inactive_pool", "market", DROP, "沒有交易活動", "過去一小時沒有任何買賣。"),
    Rule(
        "symbol_impersonation",
        "market",
        DROP,
        "冒充知名代幣",
        "代幣符號與 USDT、WETH、CAKE 等知名代幣相同，但合約地址不同。",
    ),
    Rule(
        "honeypot_suspect",
        "market",
        DROP,
        "疑似貔貅盤（只能買不能賣）",
        "一小時內大量買入卻完全沒有賣出。",
    ),
    Rule(
        "fdv_liquidity_ratio",
        "market",
        FLAG,
        "估值遠高於流動性",
        "FDV 是流動性的數十倍以上，少量賣壓就會崩跌。",
        weight=2,
    ),
    # --- stage 2: contract security (GoPlus) ------------------------------------------
    Rule(
        "security_check_failed",
        "security",
        DROP,
        "無法取得合約安全檢測",
        "讀不到代幣的合約風險資料就無法判斷，一律丟棄（寧可錯殺）。",
    ),
    Rule(
        "honeypot",
        "security",
        DROP,
        "貔貅盤：無法賣出",
        "檢測判定為 honeypot、不能買或不能全部賣出。",
    ),
    Rule(
        "high_tax",
        "security",
        DROP,
        "買賣稅過高",
        "買入或賣出稅超過上限；也常被用來變相禁止賣出。",
    ),
    Rule(
        "tax_modifiable",
        "security",
        DROP,
        "稅率可被修改",
        "擁有者可以隨時調高稅率，或對特定地址設定不同稅率。",
    ),
    Rule(
        "owner_privileges",
        "security",
        DROP,
        "擁有者保留危險權限",
        "可以增發、收回所有權、直接修改餘額、隱藏擁有者或自毀合約；擁有者未放棄時這些都是跑路手段。",
    ),
    Rule(
        "transfer_restrictions",
        "security",
        DROP,
        "可暫停轉帳或有黑名單",
        "擁有者可以暫停交易或把地址列入黑名單，買家可能賣不掉。",
    ),
    Rule(
        "not_open_source",
        "security",
        DROP,
        "合約未開源",
        "原始碼沒有驗證，無法確認合約裡是否藏有後門。",
    ),
    Rule(
        "scam_history",
        "security",
        DROP,
        "詐騙紀錄",
        "同一部署者曾發過貔貅盤、被標記為空投詐騙或仿冒代幣。",
    ),
    Rule(
        "lp_unlocked",
        "security",
        DROP,
        "流動性未鎖倉",
        "鎖倉或銷毀的 LP 比例太低，建池者可以隨時撤走流動性（rug pull）。",
    ),
    Rule(
        "holder_concentration",
        "security",
        DROP,
        "持幣過度集中",
        "前 10 大持有人（排除池子、鎖倉與銷毀地址）比例過高；低於丟棄門檻時只標記。",
    ),
    Rule(
        "upgradeable_proxy",
        "security",
        FLAG,
        "可升級代理合約",
        "合約邏輯可以被擁有者替換，今天安全不代表明天安全。",
        weight=2,
    ),
    Rule(
        "external_call",
        "security",
        FLAG,
        "轉帳時呼叫外部合約",
        "代幣轉帳時會呼叫外部合約，行為可能被遠端改變。",
        weight=1,
    ),
    Rule(
        "trading_cooldown",
        "security",
        FLAG,
        "交易冷卻限制",
        "兩筆交易之間有強制冷卻時間，可能妨礙及時賣出。",
        weight=1,
    ),
    Rule(
        "creator_holds_supply",
        "security",
        FLAG,
        "部署者持幣比例高",
        "部署者錢包仍持有大量代幣。",
        weight=2,
    ),
    # --- stage 3: case library ---------------------------------------------------------
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


def launch_rules(snap: PoolSnapshot, cfg: FilterConfig) -> list[RuleHit]:
    hits = []
    minimum = cfg.min_initial_quote.get(snap.quote_symbol.upper())
    if (
        snap.initial_quote_amount is not None
        and minimum is not None
        and snap.initial_quote_amount < minimum
    ):
        detail = (
            f"{snap.initial_quote_amount:,.4g} {snap.quote_symbol} < {minimum:,.4g}"
        )
        hits.append(_hit("low_initial_liquidity", detail))
    if (
        snap.creator_recent_pools is not None
        and snap.creator_recent_pools >= cfg.max_creator_pools_24h
    ):
        hits.append(
            _hit("serial_creator", f"24 小時內已建 {snap.creator_recent_pools} 個池")
        )
    return hits


def market_rules(snap: PoolSnapshot, chain: Chain, cfg: FilterConfig) -> list[RuleHit]:
    hits = []
    quote_ok = bool(
        chain.quote_symbol(snap.quote_mint)
    ) or snap.quote_symbol.upper() in {s.upper() for s in chain.quote_tokens.values()}
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
    canonical = chain.canonical_tokens.get(snap.base_symbol.upper().lstrip("$"))
    if canonical and snap.base_mint.lower() != canonical.lower():
        hits.append(_hit("symbol_impersonation", f"{snap.base_symbol} ≠ {canonical}"))
    if (
        snap.buys_h1 is not None
        and snap.buys_h1 >= cfg.honeypot_min_buys_h1
        and snap.sells_h1 == 0
    ):
        hits.append(_hit("honeypot_suspect", f"1 小時內 {snap.buys_h1} 買 / 0 賣"))
    if snap.fdv_usd and snap.liquidity_usd:
        ratio = snap.fdv_usd / snap.liquidity_usd
        if ratio > cfg.max_fdv_to_liquidity:
            hits.append(_hit("fdv_liquidity_ratio", f"FDV / 流動性 = {ratio:.0f}"))
    return hits


def security_rules(sec: TokenSecurity, cfg: FilterConfig) -> list[RuleHit]:
    hits = []
    if sec.is_honeypot or sec.cannot_buy or sec.cannot_sell_all:
        flags = [
            name
            for name in ("is_honeypot", "cannot_buy", "cannot_sell_all")
            if getattr(sec, name)
        ]
        hits.append(_hit("honeypot", "、".join(flags)))
    taxes = []
    if sec.buy_tax is not None and sec.buy_tax > cfg.max_buy_tax:
        taxes.append(f"買 {sec.buy_tax:.0%}")
    if sec.sell_tax is not None and sec.sell_tax > cfg.max_sell_tax:
        taxes.append(f"賣 {sec.sell_tax:.0%}")
    if taxes:
        hits.append(_hit("high_tax", "、".join(taxes)))
    if not sec.owner_renounced and (
        sec.slippage_modifiable or sec.personal_slippage_modifiable
    ):
        hits.append(_hit("tax_modifiable"))

    privileges = [
        name
        for name in (
            "can_take_back_ownership",
            "owner_change_balance",
            "hidden_owner",
            "selfdestruct",
        )
        if getattr(sec, name)
    ]
    if sec.is_mintable and not sec.owner_renounced:
        privileges.insert(0, "is_mintable")
    if privileges:
        hits.append(_hit("owner_privileges", "、".join(privileges)))
    if not sec.owner_renounced and (sec.transfer_pausable or sec.is_blacklisted):
        restrictions = [
            n for n in ("transfer_pausable", "is_blacklisted") if getattr(sec, n)
        ]
        hits.append(_hit("transfer_restrictions", "、".join(restrictions)))
    if sec.is_open_source is False:
        hits.append(_hit("not_open_source"))
    scams = [
        n
        for n in ("honeypot_with_same_creator", "is_airdrop_scam", "is_fake_token")
        if getattr(sec, n)
    ]
    if scams:
        hits.append(_hit("scam_history", "、".join(scams)))
    if sec.lp_locked_pct is not None and sec.lp_locked_pct < cfg.min_lp_locked_pct:
        hits.append(
            _hit(
                "lp_unlocked",
                f"鎖倉 / 銷毀 {sec.lp_locked_pct:.0f}% < {cfg.min_lp_locked_pct:.0f}%",
            )
        )
    pct = sec.top10_holder_pct
    if pct is not None and pct > cfg.drop_top10_holder_pct:
        hits.append(_hit("holder_concentration", f"前 10 大持有 {pct:.1f}%"))
    elif pct is not None and pct > cfg.flag_top10_holder_pct:
        hits.append(
            _hit("holder_concentration", f"前 10 大持有 {pct:.1f}%", action=FLAG)
        )
    if sec.is_proxy:
        hits.append(_hit("upgradeable_proxy"))
    if sec.external_call:
        hits.append(_hit("external_call"))
    if sec.trading_cooldown:
        hits.append(_hit("trading_cooldown"))
    if sec.creator_pct is not None and sec.creator_pct > cfg.flag_creator_pct:
        hits.append(_hit("creator_holds_supply", f"{sec.creator_pct:.1f}%"))
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
    chain: Chain,
    security: TokenSecurity | None = None,
    cfg: FilterConfig | None = None,
    cases: CaseLibrary | None = None,
    as_of: date | None = None,
    security_error: str | None = None,
) -> Decision:
    cfg = cfg or FilterConfig()
    hits = launch_rules(snap, cfg) + market_rules(snap, chain, cfg)
    if security is not None:
        hits += security_rules(security, cfg)
    elif security_error:
        hits.append(_hit("security_check_failed", security_error))
    if cases is not None:
        hits += knowledge_rules(
            snap, cfg, cases, as_of or datetime.now(timezone.utc).date()
        )
    return decide(hits)
