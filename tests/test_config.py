import re
from pathlib import Path

import pytest

from starstrata.config import ConfigError, load_config, parse_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _repo_config_text() -> str:
    return REPO_CONFIG.read_text(encoding="utf-8")


def _replace(text: str, key: str, value: str) -> str:
    """把某一行 `key = ...` 的值換成 value。"""
    new, count = re.subn(rf"(?m)^{re.escape(key)} = .*$", f"{key} = {value}", text)
    assert count == 1, f"設定檔中找不到唯一的 {key}"
    return new


def test_repo_config_is_valid() -> None:
    cfg = load_config(REPO_CONFIG)
    assert cfg.schedule.timezone.key == "Asia/Taipei"
    assert "python" in cfg.trending.languages
    assert cfg.trending.periods == ("daily", "weekly", "monthly")
    assert cfg.alerts.multiplier == 3.0
    assert cfg.site.locale == "zh-TW"


def test_integer_multiplier_is_accepted_as_float() -> None:
    cfg = parse_config(_replace(_repo_config_text(), "multiplier", "2"))
    assert cfg.alerts.multiplier == 2.0
    assert isinstance(cfg.alerts.multiplier, float)


def test_empty_language_list_means_overall_only() -> None:
    cfg = parse_config(_replace(_repo_config_text(), "languages", "[]"))
    assert cfg.trending.languages == ()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("min_stars", "true", "search.min_stars: 必須是整數"),
        ("min_stars", '"100"', "search.min_stars: 必須是整數"),
        ("min_stars", "-1", "search.min_stars: 必須是 0 以上"),
        ("limit", "1001", "search.limit: 必須是 1 到 1000"),
        ("daily_request_budget", "-1", "backfill.daily_request_budget: 必須是 0 以上"),
        ("multiplier", "0.5", "alerts.multiplier: 必須是 1.0 以上"),
        ("multiplier", "nan", "alerts.multiplier: 必須是有限的數字"),
        ("multiplier", "inf", "alerts.multiplier: 必須是有限的數字"),
        ("timezone", '"Mars/Base"', "schedule.timezone: 無效的時區"),
        ("periods", "[]", "trending.periods: 至少要有一個期間"),
        ("periods", '["daily", "yearly"]', "trending.periods: 無效的期間 yearly"),
        ("languages", '["python", "python"]', "trending.languages: 不可有重複項目"),
        ("languages", '["c/c++"]', "trending.languages: 無效的語言代碼"),
        ("languages", '[""]', "trending.languages: 必須是非空字串組成的陣列"),
        ("top_gainers", "51", "notify.top_gainers: 必須是 1 到 50"),
        ("site_url", '"http://example.com"', "notify.site_url: 必須是空字串或 https:// 開頭"),
        ("site_url", "1", "notify.site_url: 必須是字串"),
        ("llm_top_n", "21", "reports.llm_top_n: 必須是 0 到 20"),
        ("model", '""', "reports.model: 必須是非空字串"),
        ("provider", '"openai"', "reports.provider: 無效的值 'openai'"),
        ("model", '"../x"', "reports.model: 只能包含小寫英數字、點與連字號"),
        ("fallback_models", '["../x"]', "reports.fallback_models: 只能包含小寫英數字"),
        ("fallback_models", '["gemini-3.5-flash-lite"]', "reports.fallback_models: 不可包含"),
        ("fallback_models", '"x"', "reports.fallback_models: 必須是非空字串組成的陣列"),
        ("batch_size", "0", "translation.batch_size: 必須是 1 到 100"),
        ("request_interval_seconds", "-1", "translation.request_interval_seconds: 必須是 0.0 以上"),
        ("max_per_run", "5001", "translation.max_per_run: 必須是 0 到 5000"),
        ("trending_top", "26", "notify.trending_top: 必須是 1 到 25"),
        ("locale", '"ja"', "site.locale: 無效的值 'ja'，可用值為 zh-TW, en"),
        ("locale", '""', "site.locale: 必須是非空字串"),
    ],
)
def test_invalid_values_are_rejected(key: str, value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_config(_replace(_repo_config_text(), key, value))


def test_unknown_key_is_rejected() -> None:
    text = _repo_config_text().replace("[search]\n", "[search]\nmin_star = 1\n")
    with pytest.raises(ConfigError, match=re.escape("[search]: 未知的設定 min_star")):
        parse_config(text)


def test_unknown_section_is_rejected() -> None:
    with pytest.raises(ConfigError, match="未知的區段 extra"):
        parse_config(_repo_config_text() + "\n[extra]\nx = 1\n")


def test_missing_key_is_rejected() -> None:
    text = re.sub(r"(?m)^cooldown_days = .*\n", "", _repo_config_text())
    with pytest.raises(ConfigError, match=re.escape("alerts.cooldown_days: 缺少此設定")):
        parse_config(text)


def test_missing_section_is_rejected() -> None:
    text = re.sub(r"(?ms)^\[backfill\]\n.*?(?=^\[|\Z)", "", _repo_config_text())
    with pytest.raises(ConfigError, match=re.escape("[backfill]: 缺少此區段")):
        parse_config(text)


def test_section_must_be_table() -> None:
    text = re.sub(r"(?ms)^\[backfill\]\n.*?(?=^\[|\Z)", "", _repo_config_text())
    with pytest.raises(ConfigError, match=re.escape("[backfill]: 必須是表格")):
        parse_config("backfill = 1\n" + text)


def test_malformed_toml_is_rejected() -> None:
    with pytest.raises(ConfigError, match="TOML 格式錯誤"):
        parse_config("[schedule\n")


def test_empty_site_url_is_allowed() -> None:
    cfg = parse_config(_replace(_repo_config_text(), "site_url", '""'))
    assert cfg.notify.site_url == ""


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="無法讀取設定檔"):
        load_config(tmp_path / "nope.toml")


def test_fallback_models_only_for_gemini() -> None:
    text = _replace(_repo_config_text(), "provider", '"anthropic"')
    with pytest.raises(ConfigError, match=re.escape("reports.fallback_models: 只能用於 gemini")):
        parse_config(text)
    cfg = parse_config(_replace(text, "fallback_models", "[]"))
    assert cfg.reports.models == (cfg.reports.model,)


def test_english_locale_is_accepted() -> None:
    assert parse_config(_replace(_repo_config_text(), "locale", '"en"')).site.locale == "en"
