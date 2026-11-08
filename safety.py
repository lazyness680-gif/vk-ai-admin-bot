"""Безопасность: очистка ввода, маскирование секретов, лимиты, валидация."""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass

MAX_INPUT_CHARS = 2000
MAX_NAME_CHARS = 40

# Секреты, которые нельзя показывать в логах и ответах модели.
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bvk1\.a\.[A-Za-z0-9_\-\.]{10,}\b"),
    re.compile(r"\b(?:VK_TOKEN|PROXYAPI_KEY|OPENAI_API_KEY)\s*=\s*\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{10,}"),
]

# Намёки на попытку подменить инструкции. Это не блокировка, а сигнал:
# текст клиента в любом случае передаётся модели как данные, а не как команды.
_INJECTION_HINTS = [
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "системный промпт",
    "ты теперь",
    "представь что ты",
    "забудь инструкции",
    "забудь все инструкции",
    "new instructions",
    "developer mode",
    "jailbreak",
    "acting as",
    "you are now",
    "prompt injection",
    "выведи инструкции",
    "покажи промпт",
    "repeat your instructions",
]

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_PHONE_CLEAN_RE = re.compile(r"[^\d+]")
_NAME_OK_RE = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\s\-']{1,39}$")


def mask_secrets(text: str) -> str:
    """Убирает ключи из строки перед логированием/отправкой."""
    if not text:
        return text
    result = str(text)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[СКРЫТО]", result)
    return result


def contains_secret(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def sanitize_input(text: str, *, max_chars: int = MAX_INPUT_CHARS) -> str:
    """Нормализует текст клиента и обрезает по длине.

    Удаляет управляющие и невидимые символы (ими обходят фильтры и ломают вывод),
    нормализует Unicode, схлопывает лишние переводы строк.
    """
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", str(text))
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"
    return cleaned


def looks_like_injection(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _INJECTION_HINTS)


def sanitize_output(text: str, *, max_chars: int = 3800) -> str:
    """Страховка на ответ модели: убрать секреты и ограничить длину (лимит VK — 4096)."""
    if not text:
        return ""
    safe = mask_secrets(text)
    safe = _CONTROL_RE.sub(" ", safe).strip()
    if len(safe) > max_chars:
        safe = safe[:max_chars].rsplit(" ", 1)[0] + "…"
    return safe


def normalize_phone(raw: str) -> str | None:
    """Приводит телефон к +7XXXXXXXXXX. Возвращает None, если номер невалидный."""
    if not raw:
        return None
    digits = _PHONE_CLEAN_RE.sub("", str(raw))
    digits = digits.replace("+", "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    if not digits.isdigit() or len(digits) != 11:
        return None
    return f"+{digits}"


def is_valid_name(name: str) -> bool:
    name = (name or "").strip()
    if not 2 <= len(name) <= MAX_NAME_CHARS:
        return False
    return bool(_NAME_OK_RE.match(name))


def excel_safe(value: object) -> object:
    """Защита от CSV/Excel-инъекций: `=`, `+`, `-`, `@` в начале ячейки.

    Excel воспринимает такие значения как формулы, поэтому префиксуем апострофом.
    Исключение — телефоны и числа вида `+7 916 ...`: это не формула.
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped[:1] in ("=", "@") or value[:1] in ("\t", "\r"):
        return "'" + value
    if stripped[:1] in ("+", "-"):
        if re.fullmatch(r"[+-][\d\s()\-]+", stripped):
            return value
        return "'" + value
    return value


@dataclass
class RateDecision:
    allowed: bool
    retry_after: int = 0
    reason: str = ""


class RateLimiter:
    """Скользящее окно: N сообщений в минуту и M в час на пользователя."""

    def __init__(self, per_minute: int = 10, per_hour: int = 80) -> None:
        self.per_minute = max(1, per_minute)
        self.per_hour = max(1, per_hour)
        self._minute: dict[int, deque[float]] = defaultdict(deque)
        self._hour: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, now: float | None = None) -> RateDecision:
        now = time.monotonic() if now is None else now
        minute = self._minute[user_id]
        hour = self._hour[user_id]
        while minute and now - minute[0] > 60:
            minute.popleft()
        while hour and now - hour[0] > 3600:
            hour.popleft()

        if len(minute) >= self.per_minute:
            return RateDecision(False, int(60 - (now - minute[0])) + 1, "minute")
        if len(hour) >= self.per_hour:
            return RateDecision(False, int(3600 - (now - hour[0])) + 1, "hour")

        minute.append(now)
        hour.append(now)
        return RateDecision(True)

    def reset(self, user_id: int) -> None:
        self._minute.pop(user_id, None)
        self._hour.pop(user_id, None)


def chunk_text(text: str, limit: int = 3900) -> list[str]:
    """Режет длинный ответ по абзацам/предложениям под лимит VK."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit * 0.5:
            cut = text.rfind(". ", 0, limit)
        if cut < limit * 0.5:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts
