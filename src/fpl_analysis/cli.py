"""Command-line entry point: ``fpl <command>`` (or ``python -m fpl_analysis <command>``).

    fpl refresh [--no-history] [--no-entry]   pull the API into a new dated snapshot
    fpl build   [--snapshot ID]               load the latest (or given) snapshot, run SQL models
    fpl report  [--snapshot ID] [--stdout]    render reports/GWxx.md from the built marts
    fpl run     [--no-history]                refresh + build + report (the weekly one-liner)
    fpl snapshots                             list snapshots on disk
    fpl history-rebuild                       replay every snapshot into the history schema
    fpl query "SELECT ..."                    ad-hoc SQL against the warehouse
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load_settings
from .ingest import create_snapshot, get_snapshot, latest_snapshot, list_snapshots
from .report import build_report
from .store import build, connect, query, rebuild_history


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fpl", description="FPL Player Analysis")
    p.add_argument("--version", action="version", version=f"fpl-player-analysis {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--settings", help="path to settings.toml (default: config/settings.toml)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("refresh", help="pull the API into a new snapshot")
    r.add_argument("--no-history", action="store_true",
                   help="skip the per-player element-summary calls (much faster, fewer signals)")
    r.add_argument("--no-entry", action="store_true", help="skip the manager entry endpoints")

    b = sub.add_parser("build", help="load a snapshot into DuckDB and run the SQL models")
    b.add_argument("--snapshot", help="snapshot id (default: latest)")

    rep = sub.add_parser("report", help="render the gameweek report")
    rep.add_argument("--snapshot", help="snapshot id (default: latest)")
    rep.add_argument("--stdout", action="store_true", help="print instead of writing reports/")

    run = sub.add_parser("run", help="refresh + build + report")
    run.add_argument("--no-history", action="store_true")
    run.add_argument("--no-entry", action="store_true")

    sub.add_parser("snapshots", help="list snapshots on disk")
    sub.add_parser("history-rebuild", help="replay all snapshots into the history schema")

    q = sub.add_parser("query", help="run SQL against the warehouse and print the result")
    q.add_argument("sql")
    q.add_argument("--limit", type=int, default=50)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = load_settings(args.settings)

    if args.command == "refresh":
        info = create_snapshot(settings, include_player_history=not args.no_history,
                               include_entry=not args.no_entry)
        print(f"snapshot {info.snapshot_id}: current GW {info.current_gameweek}, "
              f"next GW {info.next_gameweek} -> {info.path}")
        return 0

    if args.command == "build":
        snap = get_snapshot(settings, args.snapshot) if args.snapshot else latest_snapshot(settings)
        result = build(settings, snap)
        print(f"built {len(result['models_built'])} models from snapshot {result['snapshot_id']}; "
              f"raw rows: {result['raw_counts']}; history rows added: {result['history_rows_added']}")
        return 0

    if args.command == "report":
        snap = get_snapshot(settings, args.snapshot) if args.snapshot else latest_snapshot(settings)
        ctx = build_report(settings, snap, write=not args.stdout)
        if args.stdout:
            print(ctx.markdown)
        else:
            print(f"wrote {ctx.path}")
        return 0

    if args.command == "run":
        info = create_snapshot(settings, include_player_history=not args.no_history,
                               include_entry=not args.no_entry)
        result = build(settings, info)
        ctx = build_report(settings, info)
        print(f"snapshot {info.snapshot_id} -> {len(result['models_built'])} models -> {ctx.path}")
        return 0

    if args.command == "snapshots":
        snaps = list_snapshots(settings)
        if not snaps:
            print("no snapshots yet — run `fpl refresh`")
        for s in snaps:
            print(f"{s.snapshot_id}  GW{s.current_gameweek}->GW{s.next_gameweek}  "
                  f"history={'yes' if s.has_player_history else 'no'}  {s.path}")
        return 0

    if args.command == "history-rebuild":
        con = connect(settings)
        try:
            n = rebuild_history(con, settings)
        finally:
            con.close()
        print(f"history.player_snapshot rebuilt with {n} rows")
        return 0

    if args.command == "query":
        df = query(settings, args.sql)
        print(df.head(args.limit).to_markdown(index=False))
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
