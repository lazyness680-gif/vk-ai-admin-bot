"""Клиент ВКонтакте: методы API и Bots Long Poll."""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterator

import httpx

from safety import mask_secrets

log = logging.getLogger(__name__)

API_URL = "https://api.vk.com/method/{method}"
API_VERSION = "5.199"


class VKError(RuntimeError):
    def __init__(self, code: int, message: str, method: str) -> None:
        super().__init__(f"VK API {method}: [{code}] {message}")
        self.code = code
        self.method = method
        self.vk_message = message


class VK:
    def __init__(self, token: str, group_id: int, timeout: float = 35.0) -> None:
        self._token = token
        self.group_id = group_id
        self.http = httpx.Client(timeout=timeout, follow_redirects=False)
        self._names: dict[int, str] = {}
        # В сообществе может быть выключен режим «Чат-бот» — тогда ВК отклоняет
        # клавиатуру с ошибкой 912. Отвечаем без кнопок и раз в 10 минут пробуем снова.
        self._keyboard_ok = True
        self._keyboard_retry_at = 0.0

    def close(self) -> None:
        self.http.close()

    # ---------- базовый вызов ----------

    def call(self, method: str, **params: Any) -> Any:
        payload = {
            "access_token": self._token,
            "v": API_VERSION,
            **{k: v for k, v in params.items() if v is not None},
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.http.post(API_URL.format(method=method), data=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                last_error = VKError(0, f"сетевая ошибка: {type(exc).__name__}", method)
                time.sleep(1.5 * (attempt + 1))
                continue

            if "error" in data:
                error = data["error"]
                code = int(error.get("error_code", 0))
                message = str(error.get("error_msg", ""))[:200]
                if code == 6:  # too many requests
                    time.sleep(1.5 * (attempt + 1))
                    last_error = VKError(code, message, method)
                    continue
                if code in (5, 27, 28):
                    raise VKError(
                        code,
                        "Ключ доступа недействителен или истёк. Выпустите новый ключ сообщества "
                        "и обновите VK_TOKEN в .env",
                        method,
                    )
                raise VKError(code, message, method)

            return data.get("response")

        raise last_error or VKError(0, "неизвестная ошибка", method)

    # ---------- сообщения ----------

    def send(self, peer_id: int, text: str, keyboard: dict | None = None) -> int | None:
        params: dict[str, Any] = {
            "peer_id": peer_id,
            "random_id": random.randint(1, 2**31 - 1),
            "message": text[:4090],
            "dont_parse_links": 0,
        }
        if keyboard and (self._keyboard_ok or time.time() >= self._keyboard_retry_at):
            params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        try:
            response = self.call("messages.send", **params)
        except VKError as exc:
            if exc.code == 912 and "keyboard" in params:
                self._keyboard_ok = False
                self._keyboard_retry_at = time.time() + 600
                log.warning(
                    "ВК отклонил кнопки [912]. Включите в сообществе: "
                    "Управление → Сообщения → Настройки для бота → «Чат-бот». "
                    "Пока отвечаю без кнопок."
                )
                params.pop("keyboard", None)
                response = self.call("messages.send", **params)
            else:
                raise
        return response if isinstance(response, int) else None

    def set_typing(self, peer_id: int) -> None:
        try:
            self.call("messages.setActivity", peer_id=peer_id, group_id=self.group_id, type="typing")
        except VKError as exc:
            log.debug("typing не отправлен: %s", mask_secrets(str(exc)))

    def mark_read(self, peer_id: int) -> None:
        try:
            self.call("messages.markAsRead", peer_id=peer_id, group_id=self.group_id)
        except VKError as exc:
            log.debug("markAsRead: %s", mask_secrets(str(exc)))

    def user_name(self, user_id: int) -> str:
        if user_id in self._names:
            return self._names[user_id]
        try:
            response = self.call("users.get", user_ids=user_id, fields="first_name")
            name = response[0].get("first_name", "") if response else ""
        except (VKError, IndexError, KeyError, TypeError):
            name = ""
        name = name or "друг"
        self._names[user_id] = name
        return name

    def notify_admin(self, admin_id: int, text: str) -> None:
        try:
            self.send(admin_id, text)
        except VKError as exc:
            log.warning("Не удалось уведомить администратора: %s", mask_secrets(str(exc)))
        except httpx.HTTPError as exc:
            log.warning("Не удалось уведомить администратора: %s", type(exc).__name__)

    # ---------- long poll ----------

    def get_long_poll_server(self) -> dict:
        response = self.call("groups.getLongPollServer", group_id=self.group_id)
        if not isinstance(response, dict) or "server" not in response:
            raise VKError(0, "Long Poll не включён для сообщества", "groups.getLongPollServer")
        server = str(response["server"])
        if not server.startswith("http"):
            server = "https://" + server
        return {"server": server, "key": str(response["key"]), "ts": str(response["ts"])}

    def poll(self, server: str, key: str, ts: str, wait: int = 25) -> dict:
        response = self.http.get(
            server,
            params={"act": "a_check", "key": key, "ts": ts, "wait": wait},
            timeout=wait + 15,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise VKError(0, "некорректный ответ Long Poll", "poll")
        return data

    def listen(self, ts_getter, ts_saver, wait: int = 25) -> Iterator[dict]:
        """Бесконечный поток событий. Переподключается при failed=2/3 и сетевых сбоях."""
        session = self.get_long_poll_server()
        ts = ts_getter() or session["ts"]
        backoff = 1
        while True:
            try:
                data = self.poll(session["server"], session["key"], ts, wait)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Long Poll: сбой сети (%s), переподключение", type(exc).__name__)
                time.sleep(min(backoff, 10))
                backoff = min(backoff * 2, 30)
                try:
                    session = self.get_long_poll_server()
                except VKError:
                    time.sleep(5)
                continue

            failed = data.get("failed")
            if failed:
                failed = int(failed)
                if failed == 1:
                    ts = str(data.get("ts", ts))
                    ts_saver(ts)
                else:
                    log.warning("Long Poll: failed=%s, обновляю сессию", failed)
                    time.sleep(1)
                    session = self.get_long_poll_server()
                    ts = session["ts"]
                    ts_saver(ts)
                continue

            backoff = 1
            ts = str(data.get("ts", ts))
            ts_saver(ts)
            for update in data.get("updates") or []:
                if isinstance(update, dict):
                    yield update
