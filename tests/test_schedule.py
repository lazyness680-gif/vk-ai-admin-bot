from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from schedule import (
    Master,
    SERVICES,
    date_label,
    free_slots,
    is_slot_free,
    parse_slot_id,
    parse_user_date,
    resolve_service,
    slot_id_for,
)

TZ = ZoneInfo("Europe/Moscow")


def make_master(**kwargs) -> Master:
    defaults = dict(
        name="Андрей",
        role="мастер-оптик",
        skills=["repair"],
        days=[0, 1, 2, 3, 4, 5, 6],
        start=time(9, 0),
        end=time(18, 0),
        lunch=(time(14, 0), time(14, 30)),
        slot_min=30,
    )
    defaults.update(kwargs)
    return Master(**defaults)


def test_resolve_service():
    assert resolve_service("nose_pads").key == "nose_pads"
    assert resolve_service("Замена носоупоров").key == "nose_pads"
    assert resolve_service("пайка_титан").key == "solder_titanium"
    # свободные формулировки модели
    assert resolve_service("Лазерная пайка титана").key == "solder_titanium"
    assert resolve_service("пайка стальной дужки").key == "solder_steel"
    assert resolve_service("проверка зрения").key == "eye_exam"
    assert resolve_service("подобрать оправу").key == "frame_fitting"
    assert resolve_service("чепуха") is None
    assert resolve_service("массаж") is None


def test_parse_slot_id_roundtrip():
    sid = slot_id_for(date(2026, 11, 5), time(15, 30), "Андрей")
    assert sid == "2026-11-05T15:30|Андрей"
    parsed = parse_slot_id(sid)
    assert parsed == (date(2026, 11, 5), time(15, 30), "Андрей")
    assert parse_slot_id("2026-11-05T15:30") is None
    assert parse_slot_id("2026-13-45T99:99|X") is None
    assert parse_slot_id("") is None
    assert parse_slot_id("../../etc/passwd") is None


def test_date_label_is_ready_to_copy():
    assert date_label(date(2026, 11, 5)) == "четверг, 05.11.2026"
    assert date_label(date(2026, 11, 7)) == "суббота, 07.11.2026"
    now = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
    slots = free_slots(
        masters=[make_master()],
        service=SERVICES["nose_pads"],
        date_from=date(2026, 11, 7),
        date_to=date(2026, 11, 7),
        bookings=[],
        holds={},
        now=now,
        min_lead_min=60,
    )
    assert slots
    assert all(s["date_label"] == "суббота, 07.11.2026" for s in slots)


def test_parse_user_date_understands_live_speech():
    # 31.10.2026 — суббота
    today = date(2026, 10, 31)
    assert parse_user_date("сегодня", today) == date(2026, 10, 31)
    assert parse_user_date("завтра", today) == date(2026, 11, 1)
    assert parse_user_date("послезавтра", today) == date(2026, 11, 2)
    assert parse_user_date("в субботу", today) == date(2026, 11, 7)
    assert parse_user_date("в следующую субботу", today) == date(2026, 11, 7)
    assert parse_user_date("в пятницу", today) == date(2026, 11, 6)
    assert parse_user_date("следующая пятница", today) == date(2026, 11, 6)
    assert parse_user_date("1 ноября", today) == date(2026, 11, 1)
    assert parse_user_date("на 5 ноября", today) == date(2026, 11, 5)
    assert parse_user_date("3.11", today) == date(2026, 11, 3)
    assert parse_user_date("03.11.2026", today) == date(2026, 11, 3)
    assert parse_user_date("2026-11-07", today) == date(2026, 11, 7)
    assert parse_user_date("1 ноября в 10:00", today) == date(2026, 11, 1)
    assert parse_user_date("чепуха", today) is None


def test_free_slots_skips_lunch_and_past():
    now = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)
    slots = free_slots(
        masters=[make_master()],
        service=SERVICES["nose_pads"],
        date_from=date(2026, 11, 5),
        date_to=date(2026, 11, 5),
        bookings=[],
        holds={},
        now=now,
        min_lead_min=60,
    )
    times = [s["time"] for s in slots]
    assert "14:00" not in times  # попадает в обед
    assert "13:30" in times
    assert "09:00" not in times  # уже прошло / меньше часа
    assert "11:00" in times
    assert all(s["master"] == "Андрей" for s in slots)


def test_free_slots_respects_bookings():
    now = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
    bookings = [{"date": "2026-11-05", "time": "11:00", "duration": 30, "master": "Андрей", "status": "Подтверждена"}]
    slots = free_slots(
        masters=[make_master()],
        service=SERVICES["nose_pads"],
        date_from=date(2026, 11, 5),
        date_to=date(2026, 11, 5),
        bookings=bookings,
        holds={},
        now=now,
    )
    assert "11:00" not in [s["time"] for s in slots]
    assert "11:30" in [s["time"] for s in slots]


def test_free_slots_skips_other_skill_and_day_off():
    now = datetime(2026, 11, 6, 8, 0, tzinfo=TZ)  # пятница
    master = make_master(days=[0], skills=["repair"])  # только понедельник
    slots = free_slots(
        masters=[master],
        service=SERVICES["nose_pads"],
        date_from=date(2026, 11, 6),
        date_to=date(2026, 11, 8),
        bookings=[],
        holds={},
        now=now,
    )
    assert slots == []
    # проверка зрения умеет только оптометрист
    assert free_slots(
        masters=[master],
        service=SERVICES["eye_exam"],
        date_from=date(2026, 11, 6),
        date_to=date(2026, 11, 8),
        bookings=[],
        holds={},
        now=now,
    ) == []


def test_holds_hide_slot():
    now = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
    sid = "2026-11-05T11:00|Андрей"
    slots = free_slots(
        masters=[make_master()],
        service=SERVICES["nose_pads"],
        date_from=date(2026, 11, 5),
        date_to=date(2026, 11, 5),
        bookings=[],
        holds={sid: datetime(2026, 11, 5, 9, 0, tzinfo=TZ)},
        now=now,
    )
    assert "11:00" not in [s["time"] for s in slots]


def test_is_slot_free_detects_conflict_and_limits():
    now = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
    master = make_master()
    assert is_slot_free(
        masters=[master], service=SERVICES["nose_pads"],
        slot_id="2026-11-05T11:00|Андрей", bookings=[], now=now,
    )
    assert not is_slot_free(
        masters=[master], service=SERVICES["nose_pads"],
        slot_id="2026-11-05T14:00|Андрей", bookings=[], now=now,  # обед
    )
    assert not is_slot_free(
        masters=[master], service=SERVICES["nose_pads"],
        slot_id="2026-11-05T11:00|Чужой", bookings=[], now=now,
    )
    assert not is_slot_free(
        masters=[master], service=SERVICES["nose_pads"],
        slot_id="2026-11-05T11:00|Андрей",
        bookings=[{"date": "2026-11-05", "time": "10:50", "duration": 30, "master": "Андрей", "status": "Подтверждена"}],
        now=now,
    )
