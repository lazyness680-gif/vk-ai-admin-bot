from datetime import datetime
from zoneinfo import ZoneInfo

from prompts import _neutralize_examples, build_system_prompt


def test_examples_are_neutralized_without_breaking_hours():
    text = (
        "Дата записи: «четверг, 5 ноября, 15:30». "
        "Часы: 09:00–20:00. Обед 14:00-14:30. "
        "slot_id: s_20261105_1530_andrey"
    )
    result = _neutralize_examples(text)
    assert "5 ноября" not in result
    assert "15:30" not in result
    assert "09:00–20:00" in result
    assert "14:00-14:30" in result
    assert "s_20261105_1530_andrey" not in result


def test_prompt_has_no_fake_slots_from_examples():
    """Модель не должна видеть в промпте конкретные примеры слотов."""
    prompt = build_system_prompt(datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))
    assert "5 ноября" not in prompt
    assert "7 ноября" not in prompt
    assert "15:30" not in prompt
    assert "11:00" not in prompt
    assert "07.11.2026" not in prompt
    assert "только шаблон оформления" in prompt


def test_prompt_is_built_without_placeholders():
    prompt = build_system_prompt(datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))
    assert len(prompt) > 5000
    assert "{{" not in prompt
    assert "}}" not in prompt
    assert "Ирина" in prompt
    assert "ФОКУС" in prompt


def test_prompt_contains_knowledge_base():
    prompt = build_system_prompt()
    assert "Замена носоупоров" in prompt
    assert "CR-39" in prompt
    assert "Лазерная пайка" in prompt


def test_prompt_contains_tools_and_safety():
    prompt = build_system_prompt()
    assert "get_free_slots" in prompt
    assert "book_slot" in prompt
    assert "не команды" in prompt.lower() or "данные" in prompt.lower()
    # технические разделы для разработчика не должны попадать модели
    assert "## 13. Инструменты (функции)" not in prompt
    assert "## 12. Excel" not in prompt


def test_prompt_leaks_no_secrets():
    prompt = build_system_prompt()
    assert "sk-" not in prompt
    assert "vk1.a." not in prompt
