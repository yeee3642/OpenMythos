"""EVM JSON-RPC collector: factory logs, pool-creation facts, ERC-20 reads."""

from __future__ import annotations

from monitor_common.http import HttpJsonClient

from ..config import (
    PAIR_CREATED,
    POOL_CREATED,
    SELECTOR_BALANCE_OF,
    SELECTOR_DECIMALS,
    Chain,
)
from ..models import NewPoolEvent, PoolFacts


class RpcError(RuntimeError):
    pass


def _addr_from_word(word: str) -> str:
    """Last 20 bytes of a 32-byte hex word, as a 0x-prefixed lowercase address."""
    return "0x" + word[-40:].lower()


def decode_factory_log(log: dict, chain: Chain) -> NewPoolEvent | None:
    """Decode a PairCreated (V2) or PoolCreated (V3) log from a known factory."""
    factory = next(
        (f for f in chain.factories if f.address.lower() == log["address"].lower()),
        None,
    )
    topics = log.get("topics") or []
    if factory is None or len(topics) < 3 or log.get("removed"):
        return None
    token0, token1 = _addr_from_word(topics[1]), _addr_from_word(topics[2])
    data = log["data"][2:]
    words = [data[i : i + 64] for i in range(0, len(data), 64)]
    if topics[0].lower() == POOL_CREATED:
        # data: int24 tickSpacing, address pool
        pool = _addr_from_word(words[1])
    else:
        # PairCreated data: address pair, uint256 allPairsLength
        pool = _addr_from_word(words[0])
    return NewPoolEvent(
        dex=factory.label,
        program_id=factory.address,
        signature=log["transactionHash"],
        slot=int(log["blockNumber"], 16),
        pool=pool,
        tokens=(token0, token1),
    )


class EvmRpcClient:
    def __init__(self, url: str, http: HttpJsonClient | None = None):
        self.url = url
        self.http = http or HttpJsonClient(min_interval_s=0.2)
        self._next_id = 0

    def call(self, method: str, params: list):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        resp = self.http.request(self.url, {**payload, "params": params})
        if "error" in resp:
            raise RpcError(f"{method}: {resp['error']}")
        return resp["result"]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def factory_logs(self, chain: Chain, from_block: int, to_block: int) -> list[dict]:
        flt = {
            "address": [f.address for f in chain.factories],
            "topics": [[PAIR_CREATED, POOL_CREATED]],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        return self.call("eth_getLogs", [flt])

    def transaction(self, tx_hash: str) -> dict | None:
        return self.call("eth_getTransactionByHash", [tx_hash])

    def erc20_call(self, token: str, data: str, block: str = "latest") -> int:
        result = self.call("eth_call", [{"to": token, "data": data}, block])
        return int(result, 16) if result not in (None, "0x") else 0

    def balance_of(self, token: str, holder: str, block: str = "latest") -> int:
        data = SELECTOR_BALANCE_OF + holder.lower().replace("0x", "").rjust(64, "0")
        return self.erc20_call(token, data, block)

    def decimals(self, token: str) -> int:
        return self.erc20_call(token, SELECTOR_DECIMALS)

    def pool_facts(self, event: NewPoolEvent, chain: Chain) -> PoolFacts:
        """Creator and quote liquidity in the pool at the creation block."""
        tx = self.transaction(event.signature) or {}
        quote = [t for t in event.tokens if chain.quote_symbol(t)]
        base = [t for t in event.tokens if not chain.quote_symbol(t)]
        initial = {}
        for token in quote:
            raw = self.balance_of(token, event.pool, hex(event.slot))
            initial[token] = raw / 10 ** self.decimals(token)
        return PoolFacts(
            creator=(tx.get("from") or "").lower(),
            base_mints=base,
            quote_mints=quote,
            initial_quote=initial,
        )
