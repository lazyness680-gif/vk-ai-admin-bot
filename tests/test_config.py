"""Разбор настроек: VK ID администратора в разных форматах."""

from __future__ import annotations

import pytest

from config import _parse_vk_id


@pytest.mark.parametrize(
    "raw",
    [
        "187815390",
        "id187815390",
        "ID187815390",
        "@id187815390",
        "https://vk.com/id187815390",
        "https://vk.com/id187815390?from=search",
        "vk.com/id187815390",
    ],
)
def test_parse_vk_id_accepts_common_formats(raw):
    assert _parse_vk_id(raw) == 187815390


@pytest.mark.parametrize("raw", ["", "   ", "abc", "vk.com/fokus", "id"])
def test_parse_vk_id_rejects_garbage(raw):
    assert _parse_vk_id(raw) is None
