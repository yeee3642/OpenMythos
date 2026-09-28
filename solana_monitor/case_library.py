"""Solana case library access (see monitor_common.case_library)."""

from monitor_common.case_library import (  # noqa: F401  (re-exported)
    PROTOCOL_RISK_CATEGORIES,
    CaseLibrary,
    case_db_path,
)

DEFAULT_CASE_DB = case_db_path("solana_hacks")
