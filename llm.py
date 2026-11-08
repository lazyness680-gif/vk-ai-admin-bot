"""Клиент ProxyAPI (OpenAI-совместимый) с учётом бюджета и таймаутов."""

from __future__ import annotations

import logging

from openai import OpenAI

from config import Settings
from safety import mask_secrets

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings, storage) -> None:
        self.settings = settings
        self.storage = storage
        self.client = OpenAI(
            api_key=settings.proxy_key,
            base_url=settings.base_url,
            timeout=45.0,
            max_retries=2,
        )

    def _check_budget(self) -> None:
        if self.storage.usage_today() >= self.settings.max_daily_calls:
            raise BudgetExceeded(
                f"Достигнут дневной лимит запросов ({self.settings.max_daily_calls})."
            )

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        self._check_budget()
        self.storage.incr_usage()
        try:
            return self.client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
                temperature=0.6,
                max_tokens=500,
                presence_penalty=0.2,
                frequency_penalty=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            # В лог — только тип и безопасный текст, без ключей и тел запросов.
            safe = mask_secrets(str(exc))[:300]
            log.error("Ошибка LLM: %s: %s", type(exc).__name__, safe)
            raise
