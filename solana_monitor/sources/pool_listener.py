"""Self-collection: detect new liquidity pools from Solana program logs.

This is the Solana counterpart of listening to PancakeSwap V2 ``PairCreated`` /
V3 ``PoolCreated`` events. One ``logsSubscribe`` subscription is opened per DEX
program (the RPC only accepts a single address in ``mentions``); a notification
whose logs contain that program's pool-creation marker is emitted as a
``NewPoolEvent``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterable

from ..config import DEFAULT_WS_URL, DEX_PROGRAMS, DexProgram
from ..models import NewPoolEvent

_INVOKE = re.compile(r"^Program (\w+) invoke \[\d+\]$")
_EXIT = re.compile(r"^Program (\w+) (?:success|failed.*)$")
_LOG = "Program log: "


def program_log_messages(logs: Iterable[str]) -> list[tuple[str, str]]:
    """Attribute each "Program log:" line to the program that emitted it.

    Logs of nested CPIs are interleaved, so a stack of invoked programs is kept
    and each message is assigned to the program on top of the stack.
    """
    stack: list[str] = []
    messages: list[tuple[str, str]] = []
    for line in logs:
        if m := _INVOKE.match(line):
            stack.append(m.group(1))
        elif _EXIT.match(line):
            if stack:
                stack.pop()
        elif line.startswith(_LOG) and stack:
            messages.append((stack[-1], line[len(_LOG) :]))
    return messages


def is_pool_creation(program: DexProgram, logs: Iterable[str]) -> bool:
    for program_id, message in program_log_messages(logs):
        if program_id != program.program_id:
            continue
        for marker in program.create_markers:
            # Anchor logs are exact ("Instruction: Create"); Raydium AMM v4 logs
            # "initialize2: InitializeInstruction2 {...}", hence the prefix check.
            if message == marker or message.startswith(marker + ":"):
                return True
    return False


def event_from_notification(program: DexProgram, msg: dict) -> NewPoolEvent | None:
    """Turn a logsNotification message into a NewPoolEvent, if it is one."""
    if msg.get("method") != "logsNotification":
        return None
    result = msg["params"]["result"]
    value = result["value"]
    if value.get("err") is not None or not is_pool_creation(program, value["logs"]):
        return None
    return NewPoolEvent(
        dex=program.label,
        program_id=program.program_id,
        signature=value["signature"],
        slot=result["context"]["slot"],
    )


async def _listen_one(ws_url: str, program: DexProgram, queue: asyncio.Queue):
    import websockets  # optional dependency, only needed for live listening

    subscribe = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [{"mentions": [program.program_id]}, {"commitment": "confirmed"}],
    }
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps(subscribe))
                async for raw in ws:
                    event = event_from_notification(program, json.loads(raw))
                    if event:
                        await queue.put(event)
        except (OSError, websockets.ConnectionClosed):
            await asyncio.sleep(3)  # reconnect


async def listen(
    ws_url: str = DEFAULT_WS_URL, programs: Iterable[DexProgram] = DEX_PROGRAMS
) -> AsyncIterator[NewPoolEvent]:
    queue: asyncio.Queue = asyncio.Queue()
    tasks = [asyncio.create_task(_listen_one(ws_url, p, queue)) for p in programs]
    try:
        while True:
            yield await queue.get()
    finally:
        for task in tasks:
            task.cancel()
