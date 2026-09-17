"""FPL Player Analysis — Premier League player analytics for weekly FPL decisions.

Layers (see docs/ARCHITECTURE.md):

    api        -> thin, polite client for the official FPL API
    ingest     -> writes immutable dated JSON snapshots under data/raw/
    store      -> loads a snapshot into DuckDB (schema `raw`) and runs the SQL models
    squad      -> squad-aware helpers (free-transfer estimate, picks)
    report     -> renders the Markdown gameweek report from the mart tables
    cli        -> `fpl refresh | build | report | run`
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fpl-player-analysis")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
