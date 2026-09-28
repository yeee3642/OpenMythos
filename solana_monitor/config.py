"""Static configuration: program IDs, canonical mints and filter thresholds.

Every threshold can be overridden with a JSON file passed via ``--config``
(see ``FilterConfig.from_file``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_RPC_URL = os.environ.get(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)
DEFAULT_WS_URL = os.environ.get("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com")

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {WSOL_MINT: "SOL", USDC_MINT: "USDC", USDT_MINT: "USDT"}

# Well-known tokens that scammers imitate. A pool whose base token uses one of
# these symbols but a different mint is treated as an impersonation.
CANONICAL_MINTS = {
    "SOL": WSOL_MINT,
    "WSOL": WSOL_MINT,
    "USDC": USDC_MINT,
    "USDT": USDT_MINT,
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
}


@dataclass(frozen=True)
class DexProgram:
    label: str
    program_id: str
    # "Program log: <marker>" lines emitted by this program when a pool is created.
    create_markers: tuple[str, ...]


# Pool-creation programs watched by the listener (the Solana counterparts of
# PancakeSwap V2 PairCreated / V3 PoolCreated events).
DEX_PROGRAMS = (
    DexProgram(
        "raydium_amm_v4",
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
        ("initialize2",),
    ),
    DexProgram(
        "raydium_cpmm",
        "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
        ("Instruction: Initialize",),
    ),
    DexProgram(
        "raydium_clmm",
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
        ("Instruction: CreatePool",),
    ),
    DexProgram(
        "pumpfun",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        ("Instruction: Create", "Instruction: CreateV2"),
    ),
    DexProgram(
        "pumpswap",
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        ("Instruction: CreatePool",),
    ),
    DexProgram(
        "meteora_dlmm",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
        (
            "Instruction: InitializeLbPair",
            "Instruction: InitializeLbPair2",
            "Instruction: InitializeCustomizablePermissionlessLbPair",
        ),
    ),
    DexProgram(
        "orca_whirlpool",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        ("Instruction: InitializePool", "Instruction: InitializePoolV2"),
    ),
)

# Token-account owners that hold pool reserves or burned supply; excluded when
# measuring holder concentration.
POOL_AUTHORITIES = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4 authority
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM authority
    "1nc1nerator11111111111111111111111111111111",  # Solana incinerator (burn)
}

# Maps DexScreener dexId values to project names used in the case library.
DEX_ID_TO_CASE_PROJECTS = {
    "raydium": ("Raydium",),
    "orca": ("Orca",),
    "meteora": ("Meteora",),
    "pumpswap": ("Pump.fun",),
    "pumpfun": ("Pump.fun",),
    "aquifer": ("Aquifer",),
}


@dataclass
class FilterConfig:
    """Thresholds for the filters in ``filters.py``."""

    allowed_quote_symbols: tuple[str, ...] = ("SOL", "WSOL", "USDC", "USDT")
    min_liquidity_usd: float = 10_000
    max_fdv_to_liquidity: float = 50
    honeypot_min_buys_h1: int = 20
    max_transfer_fee_bps: int = 100
    flag_top10_holder_pct: float = 30
    drop_top10_holder_pct: float = 50
    recent_incident_days: int = 180
    # Launch-stage filters (listener only): minimum quote tokens deposited when
    # the pool is created, and how many pools one wallet may open per 24 hours.
    min_initial_quote: dict[str, float] = field(
        default_factory=lambda: {"SOL": 5.0, "USDC": 1_000.0, "USDT": 1_000.0}
    )
    max_creator_pools_24h: int = 3
    # Program IDs of deprecated pool programs (see raydium-legacy-amm-v3-2026).
    deprecated_programs: tuple[str, ...] = ()
    # Mints exempt from authority checks (e.g. USDC keeps a freeze authority).
    authority_allowlist: tuple[str, ...] = tuple(QUOTE_MINTS)
    canonical_mints: dict[str, str] = field(default_factory=lambda: CANONICAL_MINTS)

    @classmethod
    def from_file(cls, path: Path) -> FilterConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        for key, value in raw.items():
            if isinstance(value, list):
                raw[key] = tuple(value)
        return cls(**raw)
