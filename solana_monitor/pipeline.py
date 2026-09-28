"""Collection → filtering → storage pipelines for DexScreener scans and the listener."""

from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass

from .case_library import CaseLibrary
from .config import QUOTE_MINTS, FilterConfig
from .filters import evaluate
from .models import DROP, Decision, MintInfo, PoolSnapshot
from .sources.dexscreener import pair_to_snapshot
from .sources.pool_listener import NewPoolEvent
from .sources.solana_rpc import RpcError, SolanaRpcClient, new_mints_from_transaction
from .store import MarketStore

# Listener DEX labels → DexScreener dexId (so case-library lookups match).
LABEL_TO_DEX_ID = {
    "raydium_amm_v4": "raydium",
    "raydium_cpmm": "raydium",
    "raydium_clmm": "raydium",
    "pumpfun": "pumpfun",
    "pumpswap": "pumpswap",
    "meteora_dlmm": "meteora",
    "orca_whirlpool": "orca",
}
# Pump.fun bonding curves trade against native SOL, which has no token balance.
DEFAULT_QUOTE = {"pumpfun": "SOL"}


@dataclass
class Result:
    snapshot: PoolSnapshot
    decision: Decision
    mint: MintInfo | None


class Pipeline:
    def __init__(
        self,
        store: MarketStore,
        rpc: SolanaRpcClient | None = None,
        cfg: FilterConfig | None = None,
        cases: CaseLibrary | None = None,
    ):
        self.store = store
        self.rpc = rpc
        self.cfg = cfg or FilterConfig()
        self.cases = cases
        self._mint_cache: dict[str, MintInfo | str] = {}

    def _mint(self, mint: str) -> MintInfo | str:
        """MintInfo, or an error message if it could not be read."""
        if mint not in self._mint_cache:
            try:
                self._mint_cache[mint] = self.rpc.mint_info(mint)
            except (RpcError, urllib.error.URLError, KeyError, ValueError) as exc:
                self._mint_cache[mint] = str(exc)
        return self._mint_cache[mint]

    def process(self, snap: PoolSnapshot) -> Result:
        # Market filters are free; skip the RPC round-trips for pools they drop.
        decision = evaluate(snap, None, self.cfg, self.cases)
        mint = None
        if decision.verdict != DROP and self.rpc is not None:
            info = self._mint(snap.base_mint)
            if isinstance(info, MintInfo):
                mint = info
                decision = evaluate(snap, mint, self.cfg, self.cases)
            else:
                decision = evaluate(
                    snap, None, self.cfg, self.cases, onchain_error=info
                )
        self.store.save(snap, decision, mint)
        return Result(snap, decision, mint)

    def process_pairs(
        self, pairs: list[dict], chain_id: str = "solana"
    ) -> list[Result]:
        return [
            self.process(pair_to_snapshot(p))
            for p in pairs
            if p.get("chainId", chain_id) == chain_id
        ]

    def process_event(
        self, event: NewPoolEvent, tx_retries: int = 5, retry_delay_s: float = 1.0
    ) -> list[Result]:
        """Evaluate the token(s) of a newly created pool seen by the listener."""
        if self.store.seen_event(event.signature):
            return []
        tx = None
        for _ in range(tx_retries):
            # A just-confirmed transaction may not be queryable immediately.
            tx = self.rpc.transaction(event.signature)
            if tx:
                break
            time.sleep(retry_delay_s)
        base, quote = new_mints_from_transaction(tx)
        self.store.save_event(event, base, quote)
        quote_mint = quote[0] if quote else ""
        results = []
        for mint in base:
            snap = PoolSnapshot(
                source=event.dex,
                pair_address="",
                dex_id=LABEL_TO_DEX_ID.get(event.dex, event.dex),
                base_mint=mint,
                quote_mint=quote_mint,
                quote_symbol=QUOTE_MINTS.get(
                    quote_mint, DEFAULT_QUOTE.get(event.dex, "")
                ),
                program_id=event.program_id,
                signature=event.signature,
            )
            results.append(self.process(snap))
        return results
