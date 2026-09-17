from fpl_analysis.config import Settings, load_settings


def test_settings_load_and_validate(settings: Settings):
    assert settings.entry_id == 234865
    assert abs(settings.weight_form + settings.weight_season_ppg + settings.weight_xgi - 1) < 1e-9
    assert set(settings.fdr_multiplier) == {1, 2, 3, 4, 5}
    assert settings.sql_dir.name == "sql"


def test_env_override(monkeypatch, project_root):
    monkeypatch.setenv("FPL_ANALYSIS_HORIZON_GAMEWEEKS", "3")
    s = load_settings(project_root / "config" / "settings.toml")
    assert s.horizon_gameweeks == 3
