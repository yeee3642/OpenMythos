"""Build solana_hacks.db and its CSV exports (kept for backward compatibility).

Equivalent to ``python -m hack_db solana_hacks``; see hack_db/ for the code.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hack_db.__main__ import main

if __name__ == "__main__":
    main(["solana_hacks", *sys.argv[1:]])
