"""Отправка сообщений: поведение при выключенном «Чат-боте» (ошибка 912)."""

from __future__ import annotations

import pytest

from vk_client import VK, VKError

KEYBOARD = {"one_time": False, "buttons": []}


def test_send_retries_without_keyboard_on_912(monkeypatch):
    vk = VK("vk1.a.test", 1)
    calls: list[dict] = []

    def fake_call(method, **params):
        calls.append(dict(params))
        if "keyboard" in params:
            raise VKError(912, "This is a chat bot feature, change this status in settings", method)
        return 123

    monkeypatch.setattr(vk, "call", fake_call)

    assert vk.send(42, "привет", keyboard=KEYBOARD) == 123
    assert "keyboard" in calls[0]
    assert "keyboard" not in calls[1]

    # Следующие 10 минут не тратим запрос на заведомо отклоняемую клавиатуру.
    assert vk.send(42, "ещё", keyboard=KEYBOARD) == 123
    assert "keyboard" not in calls[2]

    vk.http.close()


def test_keyboard_retried_after_timeout(monkeypatch):
    vk = VK("vk1.a.test", 1)
    calls: list[dict] = []

    def fake_call(method, **params):
        calls.append(dict(params))
        if "keyboard" in params:
            raise VKError(912, "chat bot feature", method)
        return 1

    monkeypatch.setattr(vk, "call", fake_call)
    vk.send(42, "раз", keyboard=KEYBOARD)

    monkeypatch.setattr("vk_client.time.time", lambda: vk._keyboard_retry_at + 1)
    vk.send(42, "два", keyboard=KEYBOARD)

    # После таймаута снова пробуем с клавиатурой (и опять откатываемся на текст).
    assert "keyboard" in calls[2]
    assert "keyboard" not in calls[3]
    vk.http.close()


def test_send_does_not_swallow_other_errors(monkeypatch):
    vk = VK("vk1.a.test", 1)

    def fake_call(method, **params):
        raise VKError(901, "Can't send messages to this user", method)

    monkeypatch.setattr(vk, "call", fake_call)

    with pytest.raises(VKError) as excinfo:
        vk.send(42, "привет", keyboard=KEYBOARD)
    assert excinfo.value.code == 901
    vk.http.close()
