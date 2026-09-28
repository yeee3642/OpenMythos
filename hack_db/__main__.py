"""Build the case-library databases.

Usage:
    python -m hack_db                     # every available chain + crypto_hacks.db
    python -m hack_db solana_hacks        # one chain only
    python -m hack_db --no-export         # skip CSV export
"""

from __future__ import annotations

import argparse

from .build import DATASETS, Dataset, build, build_combined, export_csv


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="hack_db", description=__doc__.splitlines()[0]
    )
    parser.add_argument("datasets", nargs="*", help=f"any of {', '.join(DATASETS)}")
    parser.add_argument("--no-export", action="store_true", help="skip CSV export")
    parser.add_argument(
        "--no-combined",
        action="store_true",
        help="skip the cross-chain crypto_hacks.db",
    )
    args = parser.parse_args(argv)
    unknown = set(args.datasets) - set(DATASETS)
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(sorted(unknown))}")

    names = args.datasets or [n for n in DATASETS if Dataset(n).available]
    built = []
    for name in names:
        ds = Dataset(name)
        conn = build(ds)
        if not args.no_export:
            export_csv(conn, ds.export_dir)
        count, total = conn.execute(
            "SELECT COUNT(*), SUM(COALESCE(loss_usd, 0)) FROM incidents"
        ).fetchone()
        conn.close()
        built.append(ds)
        print(
            f"built {ds.db_path.name}: {count} incidents, total loss ${total or 0:,.0f}"
        )

    if not args.no_combined and not args.datasets:
        conn = build_combined(built)
        count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        conn.close()
        print(f"built crypto_hacks.db: {count} incidents across {len(built)} chains")


if __name__ == "__main__":
    main()
