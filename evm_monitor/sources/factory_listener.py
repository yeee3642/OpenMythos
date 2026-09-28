"""Self-collection: watch DEX factories for new pools (PancakeSwap / Uniswap V2 & V3).

Two modes:

* ``poll_new_pools`` — stdlib only; calls ``eth_getLogs`` on new block ranges.
* ``subscribe_new_pools`` — ``eth_subscribe("logs")`` over WebSocket
  (needs the ``websockets`` package and a WS endpoint).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator

from ..config import PAIR_CREATED, POOL_CREATED, Chain
from ..models import NewPoolEvent
from .evm_rpc import EvmRpcClient, decode_factory_log


def poll_new_pools(
    rpc: EvmRpcClient,
    chain: Chain,
    start_block: int | None = None,
    interval_s: float = 3.0,
    max_range: int = 500,
    confirmations: int = 2,
) -> Iterator[NewPoolEvent]:
    """Yield new pools forever by polling eth_getLogs over fresh block ranges."""
    next_block = start_block if start_block is not None else rpc.block_number()
    while True:
        head = rpc.block_number() - confirmations
        if head >= next_block:
            to_block = min(head, next_block + max_range - 1)
            for log in rpc.factory_logs(chain, next_block, to_block):
                event = decode_factory_log(log, chain)
                if event:
                    yield event
            next_block = to_block + 1
        else:
            time.sleep(interval_s)


async def subscribe_new_pools(ws_url: str, chain: Chain) -> AsyncIterator[NewPoolEvent]:
    import websockets  # optional dependency

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": [
            "logs",
            {
                "address": [f.address for f in chain.factories],
                "topics": [[PAIR_CREATED, POOL_CREATED]],
            },
        ],
    }
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps(request))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("method") != "eth_subscription":
                        continue
                    event = decode_factory_log(msg["params"]["result"], chain)
                    if event:
                        yield event
        except (OSError, websockets.ConnectionClosed):
            await asyncio.sleep(3)
