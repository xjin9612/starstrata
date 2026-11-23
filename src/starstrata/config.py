"""讀取並驗證 config.toml。"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starstrata.gemini import MODEL_PATTERN

TRENDING_PERIODS = ("daily", "weekly", "monthly")

# Search API 每個查詢最多回傳 1000 筆。
SEARCH_MAX_RESULTS = 1_000


class ConfigError(ValueError):
    """設定檔內容不合法。訊息包含出錯的鍵路徑。"""


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: ZoneInfo


@dataclass(frozen=True)
class TrendingConfig:
    languages: tuple[str, ...]
    periods: tuple[str, ...]


@dataclass(frozen=True)
class SearchConfig:
    created_within_days: int
    min_stars: int
    limit: int


@dataclass(frozen=True)
class TrackingConfig:
    dormant_after_days: int
    dormant_daily_stars_below: int
    reactivate_weekly_stars: int
    dormant_snapshot_interval_days: int


@dataclass(frozen=True)
class AlertsConfig:
    baseline_days: int
    multiplier: float
    min_daily_stars: int
    cooldown_days: int


@dataclass(frozen=True)
class BackfillConfig:
    daily_request_budget: int


@dataclass(frozen=True)
class NotifyConfig:
    trending_top: int
    top_gainers: int
    # 空字串表示摘要不附 dashboard 連結。
    site_url: str


REPORT_PROVIDERS = ("gemini", "anthropic")


@dataclass(frozen=True)
class ReportsConfig:
    provider: str
    model: str
    # 只用於 gemini：model 重試後仍無法使用時依序改用。anthropic 由伺服器端 fallback 處理。
    fallback_models: tuple[str, ...]
    llm_top_n: int

    @property
    def models(self) -> tuple[str, ...]:
        return (self.model, *self.fallback_models)


@dataclass(frozen=True)
class TranslationConfig:
    """說明翻譯沿用 reports 的 provider 與 model。"""

    batch_size: int
    # 0 表示不翻譯。
    max_per_run: int
    # 兩次請求之間等待的秒數。
    request_interval_seconds: float


@dataclass(frozen=True)
class Config:
    schedule: ScheduleConfig
    trending: TrendingConfig
    search: SearchConfig
    tracking: TrackingConfig
    alerts: AlertsConfig
    backfill: BackfillConfig
    notify: NotifyConfig
    reports: ReportsConfig
    translation: TranslationConfig


class _Section:
    """逐鍵讀取一個 TOML 表，結束時拒絕未讀取的鍵。"""

    def __init__(self, root: dict[str, Any], name: str) -> None:
        if name not in root:
            raise ConfigError(f"[{name}]: 缺少此區段")
        value = root[name]
        if not isinstance(value, dict):
            raise ConfigError(f"[{name}]: 必須是表格")
        self._data: dict[str, Any] = value
        self._name = name
        self._read: set[str] = set()

    def _get(self, key: str) -> Any:
        if key not in self._data:
            raise ConfigError(f"{self._name}.{key}: 缺少此設定")
        self._read.add(key)
        return self._data[key]

    def _error(self, key: str, message: str) -> ConfigError:
        return ConfigError(f"{self._name}.{key}: {message}")

    def get_int(self, key: str, *, minimum: int, maximum: int | None = None) -> int:
        value = self._get(key)
        # bool 是 int 的子類別，必須另外排除。
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._error(key, "必須是整數")
        if value < minimum or (maximum is not None and value > maximum):
            bound = f"{minimum} 以上" if maximum is None else f"{minimum} 到 {maximum}"
            raise self._error(key, f"必須是 {bound}，目前是 {value}")
        return value

    def get_float(self, key: str, *, minimum: float) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self._error(key, "必須是數字")
        if not math.isfinite(value):
            raise self._error(key, "必須是有限的數字")
        if value < minimum:
            raise self._error(key, f"必須是 {minimum} 以上，目前是 {value}")
        return float(value)

    def get_str(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value:
            raise self._error(key, "必須是非空字串")
        return value

    def get_optional_str(self, key: str) -> str:
        """可為空字串的字串設定。"""
        value = self._get(key)
        if not isinstance(value, str):
            raise self._error(key, "必須是字串")
        return value

    def get_str_list(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise self._error(key, "必須是非空字串組成的陣列")
        if len(set(value)) != len(value):
            raise self._error(key, "不可有重複項目")
        return tuple(value)

    def finish(self) -> None:
        unknown = sorted(set(self._data) - self._read)
        if unknown:
            raise ConfigError(f"[{self._name}]: 未知的設定 {', '.join(unknown)}")


def _schedule(root: dict[str, Any]) -> ScheduleConfig:
    s = _Section(root, "schedule")
    name = s.get_str("timezone")
    try:
        tz = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"schedule.timezone: 無效的時區 {name!r}") from e
    s.finish()
    return ScheduleConfig(timezone=tz)


def _trending(root: dict[str, Any]) -> TrendingConfig:
    s = _Section(root, "trending")
    languages = s.get_str_list("languages")
    for lang in languages:
        if lang != lang.strip() or "/" in lang or any(c.isspace() for c in lang):
            raise ConfigError(f"trending.languages: 無效的語言代碼 {lang!r}")
    periods = s.get_str_list("periods")
    if not periods:
        raise ConfigError("trending.periods: 至少要有一個期間")
    invalid = [p for p in periods if p not in TRENDING_PERIODS]
    if invalid:
        raise ConfigError(
            f"trending.periods: 無效的期間 {', '.join(invalid)}，"
            f"可用值為 {', '.join(TRENDING_PERIODS)}"
        )
    s.finish()
    return TrendingConfig(languages=languages, periods=periods)


def _search(root: dict[str, Any]) -> SearchConfig:
    s = _Section(root, "search")
    cfg = SearchConfig(
        created_within_days=s.get_int("created_within_days", minimum=1),
        min_stars=s.get_int("min_stars", minimum=0),
        limit=s.get_int("limit", minimum=1, maximum=SEARCH_MAX_RESULTS),
    )
    s.finish()
    return cfg


def _tracking(root: dict[str, Any]) -> TrackingConfig:
    s = _Section(root, "tracking")
    cfg = TrackingConfig(
        dormant_after_days=s.get_int("dormant_after_days", minimum=1),
        dormant_daily_stars_below=s.get_int("dormant_daily_stars_below", minimum=0),
        reactivate_weekly_stars=s.get_int("reactivate_weekly_stars", minimum=1),
        dormant_snapshot_interval_days=s.get_int("dormant_snapshot_interval_days", minimum=1),
    )
    s.finish()
    return cfg


def _alerts(root: dict[str, Any]) -> AlertsConfig:
    s = _Section(root, "alerts")
    cfg = AlertsConfig(
        baseline_days=s.get_int("baseline_days", minimum=1),
        multiplier=s.get_float("multiplier", minimum=1.0),
        min_daily_stars=s.get_int("min_daily_stars", minimum=1),
        cooldown_days=s.get_int("cooldown_days", minimum=0),
    )
    s.finish()
    return cfg


def _backfill(root: dict[str, Any]) -> BackfillConfig:
    s = _Section(root, "backfill")
    cfg = BackfillConfig(
        daily_request_budget=s.get_int("daily_request_budget", minimum=0),
    )
    s.finish()
    return cfg


def _notify(root: dict[str, Any]) -> NotifyConfig:
    s = _Section(root, "notify")
    site_url = s.get_optional_str("site_url")
    if site_url and not site_url.startswith("https://"):
        raise ConfigError("notify.site_url: 必須是空字串或 https:// 開頭的網址")
    cfg = NotifyConfig(
        trending_top=s.get_int("trending_top", minimum=1, maximum=25),
        top_gainers=s.get_int("top_gainers", minimum=1, maximum=50),
        site_url=site_url,
    )
    s.finish()
    return cfg


def _reports(root: dict[str, Any]) -> ReportsConfig:
    s = _Section(root, "reports")
    provider = s.get_str("provider")
    if provider not in REPORT_PROVIDERS:
        raise ConfigError(
            f"reports.provider: 無效的值 {provider!r}，可用值為 {', '.join(REPORT_PROVIDERS)}"
        )
    model = s.get_str("model")
    if not MODEL_PATTERN.fullmatch(model):
        raise ConfigError(f"reports.model: 只能包含小寫英數字、點與連字號，目前是 {model!r}")
    fallback_models = s.get_str_list("fallback_models")
    for name in fallback_models:
        if not MODEL_PATTERN.fullmatch(name):
            raise ConfigError(
                f"reports.fallback_models: 只能包含小寫英數字、點與連字號，目前是 {name!r}"
            )
    if model in fallback_models:
        raise ConfigError("reports.fallback_models: 不可包含 reports.model")
    if fallback_models and provider != "gemini":
        raise ConfigError("reports.fallback_models: 只能用於 gemini，anthropic 請設為 []")
    cfg = ReportsConfig(
        provider=provider,
        model=model,
        fallback_models=fallback_models,
        llm_top_n=s.get_int("llm_top_n", minimum=0, maximum=20),
    )
    s.finish()
    return cfg


def _translation(root: dict[str, Any]) -> TranslationConfig:
    s = _Section(root, "translation")
    cfg = TranslationConfig(
        batch_size=s.get_int("batch_size", minimum=1, maximum=100),
        max_per_run=s.get_int("max_per_run", minimum=0, maximum=5_000),
        request_interval_seconds=s.get_float("request_interval_seconds", minimum=0.0),
    )
    s.finish()
    return cfg


_SECTIONS = (
    "schedule",
    "trending",
    "search",
    "tracking",
    "alerts",
    "backfill",
    "notify",
    "reports",
    "translation",
)


def parse_config(text: str) -> Config:
    """解析設定內容；任何不合法的值都會丟出 ConfigError。"""
    try:
        root = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML 格式錯誤：{e}") from e
    unknown = sorted(set(root) - set(_SECTIONS))
    if unknown:
        raise ConfigError(f"未知的區段 {', '.join(unknown)}")
    return Config(
        schedule=_schedule(root),
        trending=_trending(root),
        search=_search(root),
        tracking=_tracking(root),
        alerts=_alerts(root),
        backfill=_backfill(root),
        notify=_notify(root),
        reports=_reports(root),
        translation=_translation(root),
    )


def load_config(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"無法讀取設定檔 {path}：{e.strerror}") from e
    return parse_config(text)
