"""Collection → filtering → storage pipelines for Ethereum / BSC pools."""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass

from monitor_common.ave import AveClient
from monitor_common.case_library import CaseLibrary
from monitor_common.dexscreener import pair_to_snapshot

from .config import Chain, FilterConfig
from .filters import evaluate
from .models import DROP, Decision, NewPoolEvent, PoolSnapshot, TokenSecurity
from .sources.evm_rpc import EvmRpcClient
from .sources.goplus import GoPlusClient, GoPlusError
from .store import MarketStore


@dataclass
class Result:
    snapshot: PoolSnapshot
    decision: Decision
    security: TokenSecurity | None


class Pipeline:
    def __init__(
        self,
        store: MarketStore,
        chain: Chain,
        rpc: EvmRpcClient | None = None,
        goplus: GoPlusClient | None = None,
        cfg: FilterConfig | None = None,
        cases: CaseLibrary | None = None,
        ave: AveClient | None = None,
    ):
        self.store = store
        self.chain = chain
        self.rpc = rpc
        self.goplus = goplus
        self.cfg = cfg or FilterConfig()
        self.cases = cases
        self.ave = ave
        self._security_cache: dict[str, TokenSecurity | str] = {}

    def _security(self, token: str, pool: str) -> TokenSecurity | str:
        """TokenSecurity, or an error message if it could not be read."""
        key = token.lower()
        if key not in self._security_cache:
            try:
                found = self.goplus.token_security(
                    self.chain.goplus_chain_id, [token], pool
                )
                self._security_cache[key] = found.get(key) or "GoPlus 沒有回傳此代幣"
            except (GoPlusError, urllib.error.URLError, ValueError) as exc:
                self._security_cache[key] = str(exc)
        return self._security_cache[key]

    def process(self, snap: PoolSnapshot) -> Result:
        # Launch and market filters are free; only survivors cost a GoPlus call.
        decision = evaluate(snap, self.chain, None, self.cfg, self.cases)
        sec = None
        if decision.verdict != DROP and self.goplus is not None:
            info = self._security(snap.base_mint, snap.pair_address)
            if isinstance(info, TokenSecurity):
                sec = info
                decision = evaluate(snap, self.chain, sec, self.cfg, self.cases)
            else:
                decision = evaluate(
                    snap, self.chain, None, self.cfg, self.cases, security_error=info
                )
        self.store.save(snap, decision, sec)
        if self.ave is not None and decision.verdict != DROP:
            try:
                report = self.ave.contract_risk(snap.base_mint)
            except (urllib.error.URLError, ValueError) as exc:
                report = {"error": str(exc)}
            self.store.save_external_report(
                "ave", "contract_risk", snap.base_mint, report
            )
        return Result(snap, decision, sec)

    def process_pairs(self, pairs: list[dict]) -> list[Result]:
        return [
            self.process(pair_to_snapshot(p))
            for p in pairs
            if p.get("chainId", self.chain.dexscreener_id) == self.chain.dexscreener_id
        ]

    def process_event(self, event: NewPoolEvent) -> list[Result]:
        """Evaluate the token(s) of a newly created pool seen by the listener."""
        if self.store.seen_event(event.signature):
            return []
        facts = self.rpc.pool_facts(event, self.chain)
        recent = self.store.creator_pool_count(facts.creator) if facts.creator else None
        self.store.save_event(event, facts)
        factory = next(f for f in self.chain.factories if f.label == event.dex)
        results = []
        for token in facts.base_mints:
            # Pair against a whitelisted quote if present, else the other token
            # (which the quote filter will then reject).
            others = [t for t in event.tokens if t != token]
            quote = facts.quote_mints[0] if facts.quote_mints else others[0]
            snap = PoolSnapshot(
                source=event.dex,
                pair_address=event.pool,
                dex_id=factory.dex_id,
                base_mint=token,
                quote_mint=quote,
                quote_symbol=self.chain.quote_symbol(quote),
                program_id=event.program_id,
                signature=event.signature,
                creator=facts.creator,
                initial_quote_amount=facts.initial_quote.get(quote),
                creator_recent_pools=recent,
            )
            results.append(self.process(snap))
        return results
