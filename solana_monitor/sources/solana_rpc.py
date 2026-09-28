"""Solana JSON-RPC collector: mint safety facts and pool-creation transactions."""

from __future__ import annotations

from ..config import DEFAULT_RPC_URL, POOL_AUTHORITIES, QUOTE_MINTS
from ..models import MintInfo
from .http import HttpJsonClient


class RpcError(RuntimeError):
    pass


class SolanaRpcClient:
    def __init__(self, url: str = DEFAULT_RPC_URL, http: HttpJsonClient | None = None):
        self.url = url
        # The public endpoint is heavily rate-limited; use a paid RPC for volume.
        self.http = http or HttpJsonClient(min_interval_s=0.2)
        self._next_id = 0

    def call(self, method: str, params: list):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        resp = self.http.request(self.url, {**payload, "params": params})
        if "error" in resp:
            raise RpcError(f"{method}: {resp['error']}")
        return resp["result"]

    def mint_info(self, mint: str, with_holders: bool = True) -> MintInfo:
        account = self.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        info = parse_mint_account(mint, account)
        if with_holders:
            largest = self.call("getTokenLargestAccounts", [mint])["value"]
            owners = self.token_account_owners([a["address"] for a in largest])
            info.top10_holder_pct = top10_holder_pct(largest, owners, info)
        return info

    def token_account_owners(self, addresses: list[str]) -> dict[str, str]:
        if not addresses:
            return {}
        resp = self.call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])
        owners = {}
        for address, acct in zip(addresses, resp["value"]):
            parsed = ((acct or {}).get("data") or {}).get("parsed") or {}
            owner = (parsed.get("info") or {}).get("owner")
            if owner:
                owners[address] = owner
        return owners

    def transaction(self, signature: str) -> dict | None:
        opts = {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        }
        return self.call("getTransaction", [signature, opts])


def parse_mint_account(mint: str, account_result: dict) -> MintInfo:
    """Build MintInfo from a getAccountInfo(jsonParsed) result."""
    value = (account_result or {}).get("value")
    if not value:
        raise RpcError(f"mint account {mint} not found")
    parsed = (value.get("data") or {}).get("parsed") or {}
    if parsed.get("type") != "mint":
        raise RpcError(f"{mint} is not a token mint")
    info = parsed["info"]
    extensions = {
        ext["extension"]: ext.get("state") or {} for ext in info.get("extensions") or []
    }
    return MintInfo(
        mint=mint,
        token_program=value.get("owner", ""),
        decimals=int(info["decimals"]),
        supply=int(info["supply"]),
        mint_authority=info.get("mintAuthority"),
        freeze_authority=info.get("freezeAuthority"),
        extensions=extensions,
    )


def top10_holder_pct(largest: list[dict], owners: dict[str, str], info: MintInfo):
    """Share of supply held by the 10 largest holders, ignoring pools and burns."""
    if info.supply <= 0:
        return None
    holders = [
        int(acct["amount"])
        for acct in largest
        if owners.get(acct["address"]) not in POOL_AUTHORITIES
    ]
    return round(100 * sum(sorted(holders, reverse=True)[:10]) / info.supply, 2)


def new_mints_from_transaction(tx: dict) -> tuple[list[str], list[str]]:
    """Split the mints touched by a pool-creation tx into (base, quote) lists.

    Uses post-token balances, which works for every DEX without decoding each
    program's instruction account layout.
    """
    meta = (tx or {}).get("meta") or {}
    mints: dict[str, None] = {}
    for bal in meta.get("postTokenBalances") or []:
        if bal.get("mint"):
            mints[bal["mint"]] = None
    base = [m for m in mints if m not in QUOTE_MINTS]
    quote = [m for m in mints if m in QUOTE_MINTS]
    return base, quote
