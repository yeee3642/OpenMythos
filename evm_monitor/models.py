"""EVM-specific data classes; shared ones live in monitor_common.models."""

from __future__ import annotations

from dataclasses import dataclass, field

from monitor_common.models import (  # noqa: F401  (re-exported for callers)
    DROP,
    FLAG,
    KEEP,
    Decision,
    NewPoolEvent,
    PoolFacts,
    PoolSnapshot,
    RuleHit,
)


@dataclass
class TokenSecurity:
    """Contract-risk facts for one token, as reported by GoPlus token_security.

    Booleans are None when GoPlus did not report the field; taxes are
    fractions (0.05 = 5%).
    """

    token: str
    is_open_source: bool | None = None
    is_proxy: bool | None = None
    is_mintable: bool | None = None
    owner_address: str | None = None
    can_take_back_ownership: bool | None = None
    owner_change_balance: bool | None = None
    hidden_owner: bool | None = None
    selfdestruct: bool | None = None
    external_call: bool | None = None
    is_honeypot: bool | None = None
    cannot_buy: bool | None = None
    cannot_sell_all: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    slippage_modifiable: bool | None = None
    personal_slippage_modifiable: bool | None = None
    transfer_pausable: bool | None = None
    trading_cooldown: bool | None = None
    is_blacklisted: bool | None = None
    is_anti_whale: bool | None = None
    honeypot_with_same_creator: bool | None = None
    is_airdrop_scam: bool | None = None
    is_fake_token: bool | None = None
    is_in_dex: bool | None = None
    creator_address: str | None = None
    creator_pct: float | None = None  # percent of supply held by the creator
    lp_locked_pct: float | None = None  # percent of LP locked or burned
    top10_holder_pct: float | None = None  # excluding pools, lockers, burn
    holder_count: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def owner_renounced(self) -> bool:
        return not self.owner_address or self.owner_address.lower() in {
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead",
        }
