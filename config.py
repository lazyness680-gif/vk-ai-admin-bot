"""Конфигурация бота. Все секреты — только из .env, ничего не хардкодим."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

KB_PATH = ROOT / "База знаний ФОКУС.xlsx"
PROMPT_PATH = ROOT / "Системный промпт Ирина (ФОКУС).md"
SALON_PATH = DATA_DIR / "salon.json"
MASTERS_PATH = DATA_DIR / "masters.json"
CATALOG_PATH = DATA_DIR / "catalog.xlsx"
LEADS_PATH = DATA_DIR / "leads.xlsx"
DB_PATH = DATA_DIR / "bot.db"


def _parse_vk_id(raw: str) -> int | None:
    """Принимает «187815390», «id187815390», «@id187815390» и ссылку vk.com/id..."""
    text = raw.strip().lower()
    if "vk.com/" in text:
        text = text.split("vk.com/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    text = text.lstrip("@")
    if text.startswith("id"):
        text = text[2:]
    return int(text) if text.isdigit() else None


class ConfigError(RuntimeError):
    """Понятная человеку ошибка конфигурации."""


def _get(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if value is None:
        value = ""
    value = value.strip()
    if required and not value:
        raise ConfigError(
            f"Не заполнена переменная {name}. Откройте файл .env и укажите значение "
            f"(образец — .env.example)."
        )
    return value


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, получено: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    vk_token: str
    vk_group_id: int
    proxy_key: str
    base_url: str
    model: str
    admin_vk_id: int | None
    max_daily_calls: int
    rate_per_minute: int
    rate_per_hour: int
    log_level: str
    tz: ZoneInfo
    salon: dict

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)

        token = _get("VK_TOKEN", required=True)
        if not token:
            raise ConfigError("VK_TOKEN пуст")

        group_raw = _get("VK_GROUP_ID", required=True)
        try:
            group_id = int(group_raw)
        except ValueError as exc:
            raise ConfigError(
                "VK_GROUP_ID должен быть числом без минуса, например 123456789"
            ) from exc
        if group_id <= 0:
            raise ConfigError("VK_GROUP_ID должен быть положительным числом (без минуса)")

        key = _get("PROXYAPI_KEY", required=True)
        if not key.startswith("sk-"):
            raise ConfigError(
                "PROXYAPI_KEY должен начинаться с 'sk-'. Проверьте ключ в кабинете ProxyAPI."
            )

        base_url = _get("LLM_BASE_URL", "https://api.proxyapi.ru/v1").rstrip("/")
        if not base_url.startswith("https://"):
            raise ConfigError("LLM_BASE_URL должен начинаться с https:// (передача ключа по HTTP запрещена)")

        admin_raw = _get("ADMIN_VK_ID")
        admin_id = _parse_vk_id(admin_raw)

        salon = {}
        if SALON_PATH.exists():
            import json

            try:
                salon = json.loads(SALON_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigError(f"Не удалось прочитать data/salon.json: {exc}") from exc

        tz_name = salon.get("timezone") or "Europe/Moscow"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 — любой сбой zoneinfo = откат на Москву
            tz = ZoneInfo("Europe/Moscow")

        return cls(
            vk_token=token,
            vk_group_id=group_id,
            proxy_key=key,
            base_url=base_url,
            model=_get("LLM_MODEL", "openai/gpt-4o-mini"),
            admin_vk_id=admin_id,
            max_daily_calls=_get_int("MAX_DAILY_LLM_CALLS", 2000),
            rate_per_minute=_get_int("RATE_LIMIT_PER_MINUTE", 10),
            rate_per_hour=_get_int("RATE_LIMIT_PER_HOUR", 80),
            log_level=_get("LOG_LEVEL", "INFO").upper(),
            tz=tz,
            salon=salon,
        )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
