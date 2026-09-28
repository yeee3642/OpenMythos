"""GoPlus token-security API client (https://gopluslabs.io).

Endpoint and field names follow GoPlus's official Python SDK
(GoPlusSecurity/goplus-sdk-python: ``/api/v1/token_security/{chain_id}`` with
``contract_addresses``; host ``https://api.gopluslabs.io``). An API key is
optional; set ``GOPLUS_API_KEY`` to send it as the ``Authorization`` header.
"""

from __future__ import annotations

import os
import urllib.parse

from monitor_common.http import HttpJsonClient

from ..config import BURN_ADDRESSES
from ..models import TokenSecurity

BASE_URL = "https://api.gopluslabs.io"
BOOL_FIELDS = (
    "is_open_source",
    "is_proxy",
    "is_mintable",
    "can_take_back_ownership",
    "owner_change_balance",
    "hidden_owner",
    "selfdestruct",
    "external_call",
    "is_honeypot",
    "cannot_buy",
    "cannot_sell_all",
    "slippage_modifiable",
    "personal_slippage_modifiable",
    "transfer_pausable",
    "trading_cooldown",
    "is_blacklisted",
    "is_anti_whale",
    "honeypot_with_same_creator",
    "is_airdrop_scam",
    "is_in_dex",
)


class GoPlusError(RuntimeError):
    pass


class GoPlusClient:
    def __init__(self, api_key: str | None = None, http: HttpJsonClient | None = None):
        self.api_key = api_key or os.environ.get("GOPLUS_API_KEY")
        # The free tier is rate limited; stay slow by default.
        self.http = http or HttpJsonClient(min_interval_s=2.0)

    def token_security(
        self, chain_id: str, addresses: list[str], pool: str = ""
    ) -> dict[str, TokenSecurity]:
        query = urllib.parse.urlencode({"contract_addresses": ",".join(addresses)})
        url = f"{BASE_URL}/api/v1/token_security/{chain_id}?{query}"
        headers = {"Authorization": self.api_key} if self.api_key else None
        resp = self.http.request(url, headers=headers)
        if not isinstance(resp, dict) or resp.get("code") != 1:
            raise GoPlusError(f"GoPlus error: {str(resp)[:200]}")
        result = resp.get("result") or {}
        return {
            addr.lower(): parse_token_security(addr, data, pool)
            for addr, data in result.items()
        }


def _bool(value):
    if value in (None, ""):
        return None
    return str(value) == "1"


def _float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def parse_token_security(token: str, data: dict, pool: str = "") -> TokenSecurity:
    """Turn one GoPlus result entry into TokenSecurity.

    Holder percentages from GoPlus are fractions; they are converted to
    percent. The pool itself, locked holders and burn addresses are excluded
    from the top-10 concentration.
    """
    info = TokenSecurity(token=token, raw=data)
    for name in BOOL_FIELDS:
        setattr(info, name, _bool(data.get(name)))
    fake = data.get("fake_token")
    info.is_fake_token = bool(fake and str((fake or {}).get("value", "")) == "1")
    info.owner_address = data.get("owner_address") or None
    info.creator_address = data.get("creator_address") or None
    info.buy_tax = _float(data.get("buy_tax"))
    info.sell_tax = _float(data.get("sell_tax"))
    creator = _float(data.get("creator_percent"))
    info.creator_pct = None if creator is None else round(100 * creator, 2)
    count = data.get("holder_count")
    info.holder_count = int(count) if str(count or "").isdigit() else None

    lp = data.get("lp_holders")
    if lp:
        locked = sum(
            _float(h.get("percent")) or 0
            for h in lp
            if str(h.get("is_locked")) == "1"
            or (h.get("address") or "").lower() in BURN_ADDRESSES
        )
        info.lp_locked_pct = round(100 * locked, 2)

    holders = data.get("holders")
    if holders:
        excluded = BURN_ADDRESSES | ({pool.lower()} if pool else set())
        shares = sorted(
            (
                _float(h.get("percent")) or 0
                for h in holders
                if str(h.get("is_locked")) != "1"
                and (h.get("address") or "").lower() not in excluded
            ),
            reverse=True,
        )
        info.top10_holder_pct = round(100 * sum(shares[:10]), 2)
    return info
