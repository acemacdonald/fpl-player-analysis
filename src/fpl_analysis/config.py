"""Settings loading.

Settings live in ``config/settings.toml`` (checked in, no secrets — the FPL API needs none).
Any value can be overridden with an environment variable ``FPL_<SECTION>_<KEY>``, which is
what you would use in CI or if you ever run this for a second manager.

Why TOML and not YAML/.env: TOML parses with the standard library on 3.11+ (``tomllib``),
supports typed values (ints stay ints), and nests cleanly for the FDR multiplier table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


def find_project_root(start: Path | None = None) -> Path:
    """Walk upwards until we find ``pyproject.toml`` — works from notebooks, tests and the CLI."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    # Fallback: the package's own location (src/fpl_analysis/config.py -> repo root)
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    entry_id: int
    free_transfers_override: int
    horizon_gameweeks: int
    differential_max_ownership: float
    min_chance_of_playing: int
    min_minutes_for_rates: int
    shrinkage_games: float
    weight_form: float
    weight_season_ppg: float
    weight_xgi: float
    fdr_multiplier: dict[int, float]
    report_timezone: str
    top_n_per_position: int
    captaincy_n: int
    transfer_suggestions_n: int
    chips_budget_override_m: float
    chips_bench_weight_freehit: float
    chips_bench_weight_wildcard: float
    chips_exclude_injury_risk: bool
    raw_dir: Path
    duckdb_path: Path
    reports_dir: Path
    sql_dir: Path
    squad_override_path: Path
    base_url: str
    timeout_seconds: int
    max_retries: int
    per_player_sleep_seconds: float
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def validate(self) -> None:
        total = self.weight_form + self.weight_season_ppg + self.weight_xgi
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"analysis weights must sum to 1.0, got {total:.3f}")
        if self.horizon_gameweeks < 1:
            raise ValueError("horizon_gameweeks must be >= 1")
        if self.shrinkage_games < 0:
            raise ValueError("shrinkage_games must be >= 0 (0 disables shrinkage)")
        if set(self.fdr_multiplier) != {1, 2, 3, 4, 5}:
            raise ValueError("fdr_multiplier must define keys 1..5")
        for w in (self.chips_bench_weight_freehit, self.chips_bench_weight_wildcard):
            if not 0.0 <= w <= 1.0:
                raise ValueError("chips bench weights must be between 0 and 1")


def _env_override(section: str, key: str, default: Any) -> Any:
    env_key = f"FPL_{section}_{key}".upper()
    if env_key not in os.environ:
        return default
    value = os.environ[env_key]
    if isinstance(default, bool):
        return value.lower() in {"1", "true", "yes"}
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from TOML, apply env overrides, resolve paths against the project root."""
    root = find_project_root()
    settings_path = Path(path) if path else root / "config" / "settings.toml"
    with open(settings_path, "rb") as fh:
        cfg = tomllib.load(fh)

    def get(section: str, key: str, default: Any = None) -> Any:
        """TOML value with env override; ``default`` lets a newer key be absent from an older file."""
        try:
            base = cfg[section][key]
        except KeyError:
            if default is None:
                raise
            base = default
        return _env_override(section, key, base)

    fdr = {int(k): float(v) for k, v in cfg["analysis"]["fdr_multiplier"].items()}

    settings = Settings(
        project_root=root,
        entry_id=int(get("manager", "entry_id")),
        free_transfers_override=int(get("manager", "free_transfers_override")),
        horizon_gameweeks=int(get("analysis", "horizon_gameweeks")),
        differential_max_ownership=float(get("analysis", "differential_max_ownership")),
        min_chance_of_playing=int(get("analysis", "min_chance_of_playing")),
        min_minutes_for_rates=int(get("analysis", "min_minutes_for_rates")),
        shrinkage_games=float(get("analysis", "shrinkage_games", 3.0)),
        weight_form=float(get("analysis", "weight_form")),
        weight_season_ppg=float(get("analysis", "weight_season_ppg")),
        weight_xgi=float(get("analysis", "weight_xgi")),
        fdr_multiplier=fdr,
        report_timezone=str(get("report", "timezone")),
        top_n_per_position=int(get("report", "top_n_per_position")),
        captaincy_n=int(get("report", "captaincy_n")),
        transfer_suggestions_n=int(get("report", "transfer_suggestions_n")),
        chips_budget_override_m=float(get("chips", "budget_override_m")),
        chips_bench_weight_freehit=float(get("chips", "bench_weight_freehit")),
        chips_bench_weight_wildcard=float(get("chips", "bench_weight_wildcard")),
        chips_exclude_injury_risk=bool(get("chips", "exclude_injury_risk")),
        raw_dir=root / get("paths", "raw_dir"),
        duckdb_path=root / get("paths", "duckdb_path"),
        reports_dir=root / get("paths", "reports_dir"),
        sql_dir=root / get("paths", "sql_dir"),
        squad_override_path=root / get("paths", "squad_override", "config/squad_override.toml"),
        base_url=str(get("api", "base_url")).rstrip("/"),
        timeout_seconds=int(get("api", "timeout_seconds")),
        max_retries=int(get("api", "max_retries")),
        per_player_sleep_seconds=float(get("api", "per_player_sleep_seconds")),
        raw=cfg,
    )
    settings.validate()
    return settings
