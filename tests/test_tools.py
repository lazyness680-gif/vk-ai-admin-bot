from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from config import Settings
from storage import Storage
from tools import Toolbox

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 3, 8, 0, tzinfo=TZ)  # вторник


def make_toolbox(tmp_path) -> Toolbox:
    settings = Settings(
        vk_token="vk1.a.test",
        vk_group_id=1,
        proxy_key="sk-test",
        base_url="https://api.proxyapi.ru/v1",
        model="openai/gpt-4o-mini",
        admin_vk_id=None,
        max_daily_calls=100,
        rate_per_minute=10,
        rate_per_hour=80,
        log_level="INFO",
        tz=TZ,
        salon={},
    )
    storage = Storage(tmp_path / "bot.db", tmp_path / "leads.xlsx", tmp_path / "outbox.jsonl")
    toolbox = Toolbox(
        settings=settings,
        storage=storage,
        catalog_path=tmp_path / "catalog.xlsx",
        masters_path=tmp_path / "masters.json",
    )
    toolbox._now = lambda: NOW
    return toolbox


def test_get_free_slots_returns_real_slots(tmp_path):
    toolbox = make_toolbox(tmp_path)
    result = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )
    assert result["count"] > 0
    slot = result["slots"][0]
    assert slot["date"] == "2026-11-03"
    assert slot["date_label"] == "вторник, 03.11.2026"
    assert slot["master"] == "Андрей"
    assert slot["duration_min"] == 15
    toolbox.storage.close()


def test_unknown_service_rejected(tmp_path):
    toolbox = make_toolbox(tmp_path)
    result = toolbox.execute("get_free_slots", {"service": "массаж"}, user_id=1)
    assert result["error"] == "unknown_service"
    toolbox.storage.close()


def test_book_slot_happy_path_and_conflict(tmp_path):
    toolbox = make_toolbox(tmp_path)
    slots = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"]
    slot = slots[0]
    booked = toolbox.execute(
        "book_slot",
        {
            "slot_id": slot["slot_id"],
            "name": "Анна",
            "phone": "8 916 123-45-67",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=1,
    )
    assert booked["ok"] is True
    assert booked["lead_id"].startswith("Ф-")
    assert booked["time"] == slot["time"]

    # тот же слот второму клиенту — отказ
    second = toolbox.execute(
        "book_slot",
        {
            "slot_id": slot["slot_id"],
            "name": "Игорь",
            "phone": "+79160000000",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=2,
    )
    assert second["error"] == "slot_taken"
    toolbox.storage.close()


def test_book_slot_validation(tmp_path):
    toolbox = make_toolbox(tmp_path)
    slot_id = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"][0]["slot_id"]

    no_consent = toolbox.execute(
        "book_slot",
        {"slot_id": slot_id, "name": "Анна", "phone": "+79161234567", "service": "nose_pads", "pd_consent": False},
        user_id=1,
    )
    assert no_consent["error"] == "consent_required"

    bad_phone = toolbox.execute(
        "book_slot",
        {"slot_id": slot_id, "name": "Анна", "phone": "123", "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert bad_phone["error"] == "invalid_phone"

    bad_name = toolbox.execute(
        "book_slot",
        {"slot_id": slot_id, "name": "X", "phone": "+79161234567", "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert bad_name["error"] == "invalid_name"

    fake_slot = toolbox.execute(
        "book_slot",
        {"slot_id": "2026-11-03T23:00|Андрей", "name": "Анна", "phone": "+79161234567",
         "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert fake_slot["error"] == "slot_taken"  # время вне графика — запись не создаётся

    malformed = toolbox.execute(
        "book_slot",
        {"slot_id": "2026-11-03|Андрей", "name": "Анна", "phone": "+79161234567",
         "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert malformed["error"] == "invalid_slot"
    toolbox.storage.close()


def test_save_lead_without_consent_drops_phone(tmp_path):
    toolbox = make_toolbox(tmp_path)
    result = toolbox.execute(
        "save_lead",
        {"name": "Игорь", "phone": "+79161234567", "request_type": "question", "pd_consent": False},
        user_id=5,
    )
    assert result["ok"] is True
    assert result["phone_saved"] is False
    row = toolbox.storage.conn.execute("SELECT phone, consent FROM leads").fetchone()
    assert row["phone"] is None
    assert row["consent"] == 0
    toolbox.storage.close()


def test_calc_order(tmp_path):
    toolbox = make_toolbox(tmp_path)
    result = toolbox.execute(
        "calc_order", {"frame_price": 12400, "lens_price": 4800, "labor_price": 1500}, user_id=1
    )
    assert result["total"] == 18700
    assert "18 700" in result["text"]
    toolbox.storage.close()


def test_book_slot_accepts_variant_number_and_time(tmp_path):
    """Модель иногда присылает номер варианта или время вместо полного slot_id."""
    toolbox = make_toolbox(tmp_path)
    slots = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"]

    by_number = toolbox.execute(
        "book_slot",
        {"slot_id": "1", "name": "Анна", "phone": "+79161234567",
         "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert by_number["ok"] is True
    assert by_number["time"] == slots[0]["time"]

    # второму клиенту нужен свой список слотов (fallback привязан к пользователю)
    toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=2
    )
    by_time = toolbox.execute(
        "book_slot",
        {"slot_id": slots[1]["time"], "name": "Игорь", "phone": "+79160000000",
         "service": "nose_pads", "pd_consent": True},
        user_id=2,
    )
    assert by_time["ok"] is True
    assert by_time["time"] == slots[1]["time"]
    toolbox.storage.close()


def test_booking_error_returns_alternatives(tmp_path):
    toolbox = make_toolbox(tmp_path)
    toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )
    result = toolbox.execute(
        "book_slot",
        {"slot_id": "2026-11-03T23:00|Андрей", "name": "Анна", "phone": "+79161234567",
         "service": "nose_pads", "pd_consent": True},
        user_id=1,
    )
    assert result["error"] == "slot_taken"
    assert result["alternative_slots"]
    assert len(result["alternative_slots"]) <= 3
    toolbox.storage.close()


def test_repeat_booking_is_not_a_conflict(tmp_path):
    toolbox = make_toolbox(tmp_path)
    slot = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"][0]
    payload = {
        "slot_id": slot["slot_id"], "name": "Анна", "phone": "+79161234567",
        "service": "nose_pads", "pd_consent": True,
    }
    first = toolbox.execute("book_slot", payload, user_id=1)
    assert first["ok"] is True

    repeat = toolbox.execute("book_slot", payload, user_id=1)
    assert repeat["ok"] is True
    assert repeat["already_booked"] is True
    assert repeat["same_slot"] is True
    assert repeat["lead_id"] == first["lead_id"]

    count = toolbox.storage.conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]
    assert count == 1
    toolbox.storage.close()


def test_save_lead_deduplicates(tmp_path):
    toolbox = make_toolbox(tmp_path)
    payload = {"name": "Анна", "phone": "+79161234567", "request_type": "repair", "pd_consent": True}
    first = toolbox.execute("save_lead", payload, user_id=1)
    second = toolbox.execute("save_lead", payload, user_id=1)
    assert first["lead_id"] == second["lead_id"]
    assert second["deduplicated"] is True
    count = toolbox.storage.conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    assert count == 1
    toolbox.storage.close()


def test_get_free_slots_accepts_phrase_in_when(tmp_path):
    """Модель передаёт «в субботу» как есть, дату считает сервер."""
    toolbox = make_toolbox(tmp_path)
    result = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "when": "в субботу"}, user_id=1
    )
    assert result["date_from"] == "2026-11-07"
    assert result["slots"]
    assert result["slots"][0]["date_label"].startswith("суббота, 07.11.2026")

    tomorrow = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "when": "завтра"}, user_id=1
    )
    assert tomorrow["date_from"] == "2026-11-04"

    bad = toolbox.execute("get_free_slots", {"service": "nose_pads", "when": "чепуха"}, user_id=1)
    assert bad["error"] == "bad_date"
    toolbox.storage.close()


def test_book_slot_accepts_date_and_time_args(tmp_path):
    """Если модель не скопировала slot_id, достаточно date + time из ответа слотов."""
    toolbox = make_toolbox(tmp_path)
    slot = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"][1]

    booked = toolbox.execute(
        "book_slot",
        {
            "date": slot["date"],
            "time": slot["time"],
            "name": "Анна",
            "phone": "+79161234567",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=1,
    )
    assert booked["ok"] is True
    assert booked["date"] == slot["date"]
    assert booked["time"] == slot["time"]
    assert booked["date_label"] == slot["date_label"]
    toolbox.storage.close()


def test_book_slot_date_time_without_cached_slots_picks_master(tmp_path):
    """После перезапуска кэш слотов пуст: дату и время всё равно принимаем."""
    toolbox = make_toolbox(tmp_path)
    toolbox._last_slots.clear()
    booked = toolbox.execute(
        "book_slot",
        {
            "date": "2026-11-03",
            "time": "10:00",
            "name": "Анна",
            "phone": "+79161234567",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=9,
    )
    assert booked["ok"] is True
    assert booked["date"] == "2026-11-03"
    assert booked["time"] == "10:00"
    assert booked["master"] == "Андрей"
    toolbox.storage.close()


def test_book_slot_accepts_lenient_slot_strings(tmp_path):
    """«2026-11-03 10:00» и «03.11.2026, 10:00» вместо канонического slot_id."""
    toolbox = make_toolbox(tmp_path)
    slots = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"]
    first = slots[0]

    spaced = toolbox.execute(
        "book_slot",
        {
            "slot_id": f"{first['date']} {first['time']}",
            "name": "Анна",
            "phone": "+79161234567",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=1,
    )
    assert spaced["ok"] is True
    assert spaced["time"] == first["time"]

    toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=2
    )
    target = slots[1]
    dotted = f"{target['date'][8:10]}.{target['date'][5:7]}.{target['date'][:4]}, {target['time']}"
    booked = toolbox.execute(
        "book_slot",
        {
            "slot_id": dotted,
            "name": "Игорь",
            "phone": "+79160000000",
            "service": "nose_pads",
            "pd_consent": True,
        },
        user_id=2,
    )
    assert booked["ok"] is True
    assert booked["time"] == target["time"]
    toolbox.storage.close()


def test_consent_error_explains_retry(tmp_path):
    toolbox = make_toolbox(tmp_path)
    slot = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"][0]
    result = toolbox.execute(
        "book_slot",
        {
            "slot_id": slot["slot_id"],
            "name": "Анна",
            "phone": "+79161234567",
            "service": "nose_pads",
            "pd_consent": False,
        },
        user_id=1,
    )
    assert result["error"] == "consent_required"
    assert "pd_consent=true" in result["message"]
    assert result["slot_id"] == slot["slot_id"]
    toolbox.storage.close()


def test_second_booking_needs_reschedule_confirmation(tmp_path):
    """Вторая запись клиенту не создаётся молча: сначала вопрос «перенести?»."""
    toolbox = make_toolbox(tmp_path)
    slots = toolbox.execute(
        "get_free_slots", {"service": "nose_pads", "date_from": "2026-11-03", "date_to": "2026-11-03"}, user_id=1
    )["slots"]
    first, second = slots[0], slots[1]
    base = {"name": "Анна", "phone": "+79161234567", "service": "nose_pads", "pd_consent": True}

    assert toolbox.execute(
        "book_slot", {**base, "slot_id": first["slot_id"]}, user_id=1
    )["ok"] is True

    blocked = toolbox.execute(
        "book_slot", {**base, "slot_id": second["slot_id"]}, user_id=1
    )
    assert blocked["ok"] is False
    assert blocked["needs_reschedule"] is True
    assert blocked["date"] == first["date"]
    assert blocked["time"] == first["time"]

    moved = toolbox.execute(
        "book_slot", {**base, "slot_id": second["slot_id"], "reschedule": True}, user_id=1
    )
    assert moved["ok"] is True
    assert moved["time"] == second["time"]

    booking_rows = toolbox.storage.conn.execute(
        "SELECT time, status FROM bookings ORDER BY time"
    ).fetchall()
    assert {row["status"] for row in booking_rows} == {"Подтверждена", "Отменена"}
    active_time = [row["time"] for row in booking_rows if row["status"] == "Подтверждена"]
    assert active_time == [second["time"]]

    lead_rows = toolbox.storage.conn.execute(
        "SELECT status, cancel_reason FROM leads ORDER BY created_at"
    ).fetchall()
    assert [row["status"] for row in lead_rows] == ["Отменена", "Подтверждена"]
    assert "Перенесена" in (lead_rows[0]["cancel_reason"] or "")
    toolbox.storage.close()


def test_handoff_creates_lead_and_calls_callback(tmp_path):
    toolbox = make_toolbox(tmp_path)
    calls = []
    toolbox.on_handoff = lambda summary, user_id: calls.append((summary, user_id))
    result = toolbox.execute(
        "handoff_to_human", {"reason": "complaint", "summary": "Жалоба на качество"}, user_id=9
    )
    assert result["ok"] is True
    assert calls == [("Жалоба на качество", 9)]
    status = toolbox.storage.conn.execute("SELECT status FROM leads").fetchone()["status"]
    assert status == "Передана человеку"
    toolbox.storage.close()
