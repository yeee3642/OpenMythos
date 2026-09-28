"""Chain settings (Ethereum, BSC) and filter thresholds for the EVM pool monitor.

All addresses below pass their EIP-55 checksum (tests verify this).
Thresholds can be overridden with a JSON file passed via ``--config``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

# keccak256 of the factory event signatures.
PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
# PairCreated(address indexed token0, address indexed token1, address pair, uint256)
POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
# PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee,
#             int24 tickSpacing, address pool)

SELECTOR_BALANCE_OF = "0x70a08231"  # balanceOf(address)
SELECTOR_DECIMALS = "0x313ce567"  # decimals()

BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


@dataclass(frozen=True)
class Factory:
    label: str
    address: str
    kind: str  # "v2" (PairCreated) or "v3" (PoolCreated)
    dex_id: str  # DexScreener dexId, used for case-library lookups


@dataclass(frozen=True)
class Chain:
    key: str
    name_zh: str
    chain_id: int
    dexscreener_id: str
    goplus_chain_id: str
    ave_chain: str
    case_dataset: str
    rpc_env: str
    ws_env: str
    default_rpc: str
    # Quote tokens accepted by the filters: checksum address -> symbol.
    quote_tokens: dict[str, str]
    # Symbols scammers imitate -> canonical checksum address.
    canonical_tokens: dict[str, str]
    factories: tuple[Factory, ...]

    @property
    def rpc_url(self) -> str:
        return os.environ.get(self.rpc_env, self.default_rpc)

    @property
    def ws_url(self) -> str | None:
        return os.environ.get(self.ws_env)

    def quote_symbol(self, address: str) -> str:
        return {a.lower(): s for a, s in self.quote_tokens.items()}.get(
            address.lower(), ""
        )


ETHEREUM = Chain(
    key="ethereum",
    name_zh="以太坊",
    chain_id=1,
    dexscreener_id="ethereum",
    goplus_chain_id="1",
    ave_chain="eth",
    case_dataset="ethereum_hacks",
    rpc_env="ETH_RPC_URL",
    ws_env="ETH_WS_URL",
    default_rpc="https://ethereum-rpc.publicnode.com",
    quote_tokens={
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "WETH",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "USDC",
        "0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT",
        "0x6B175474E89094C44Da98b954EedeAC495271d0F": "DAI",
    },
    canonical_tokens={
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "ETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "PEPE": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "SHIB": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    },
    factories=(
        Factory(
            "uniswap_v2", "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f", "v2", "uniswap"
        ),
        Factory(
            "uniswap_v3", "0x1F98431c8aD98523631AE4a59f267346ea31F984", "v3", "uniswap"
        ),
        Factory(
            "sushiswap_v2",
            "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
            "v2",
            "sushiswap",
        ),
    ),
)

BSC = Chain(
    key="bsc",
    name_zh="BNB Smart Chain",
    chain_id=56,
    dexscreener_id="bsc",
    goplus_chain_id="56",
    ave_chain="bsc",
    case_dataset="bsc_hacks",
    rpc_env="BSC_RPC_URL",
    ws_env="BSC_WS_URL",
    default_rpc="https://bsc-dataseed.bnbchain.org",
    quote_tokens={
        "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c": "WBNB",
        "0x55d398326f99059fF775485246999027B3197955": "USDT",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d": "USDC",
        "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56": "BUSD",
    },
    canonical_tokens={
        "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
        "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
        "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    },
    factories=(
        Factory(
            "pancakeswap_v2",
            "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
            "v2",
            "pancakeswap",
        ),
        Factory(
            "pancakeswap_v3",
            "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
            "v3",
            "pancakeswap",
        ),
    ),
)

CHAINS = {c.key: c for c in (ETHEREUM, BSC)}

# DexScreener dexId -> project names used in the case libraries.
DEX_ID_TO_CASE_PROJECTS = {
    "uniswap": ("Uniswap",),
    "sushiswap": ("SushiSwap", "Sushi"),
    "pancakeswap": ("PancakeSwap",),
}


@dataclass
class FilterConfig:
    """Thresholds for evm_monitor.filters."""

    min_liquidity_usd: float = 10_000
    max_fdv_to_liquidity: float = 50
    honeypot_min_buys_h1: int = 20
    max_buy_tax: float = 0.10  # fraction, as reported by GoPlus
    max_sell_tax: float = 0.10
    min_lp_locked_pct: float = 50  # LP share that must be locked or burned
    flag_top10_holder_pct: float = 30
    drop_top10_holder_pct: float = 50
    flag_creator_pct: float = 10
    recent_incident_days: int = 180
    # Launch-stage filters (listener only).
    min_initial_quote: dict[str, float] = field(
        default_factory=lambda: {
            "WETH": 1.0,
            "WBNB": 3.0,
            "USDC": 2_000.0,
            "USDT": 2_000.0,
            "DAI": 2_000.0,
            "BUSD": 2_000.0,
        }
    )
    max_creator_pools_24h: int = 3

    @classmethod
    def from_file(cls, path: Path) -> FilterConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**raw)
