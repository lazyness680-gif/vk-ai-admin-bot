"""Расписание: мастера, услуги, расчёт свободных слотов."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SLOT_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})\|(.{2,40})$")

WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


@dataclass(frozen=True)
class Service:
    key: str
    title: str
    duration: int
    skill: str


SERVICES: dict[str, Service] = {
    "nose_pads": Service("nose_pads", "Замена носоупоров", 15, "repair"),
    "screw": Service("screw", "Замена винта / гайки / колпачка", 15, "repair"),
    "fallen_lens": Service("fallen_lens", "Установка выпавшей линзы", 20, "repair"),
    "adjust": Service("adjust", "Правка металлической оправы", 30, "repair"),
    "thread": Service("thread", "Нарезка резьбы под винт", 30, "repair"),
    "glue": Service("glue", "Склейка ацетатной дужки (приём)", 15, "repair"),
    "solder_steel": Service("solder_steel", "Лазерная пайка, сталь/латунь (приём)", 15, "repair"),
    "solder_titanium": Service("solder_titanium", "Лазерная пайка, титан (приём)", 15, "repair"),
    "lens_install": Service("lens_install", "Установка линз в свою оправу (приём)", 15, "repair"),
    "glasses_make": Service("glasses_make", "Изготовление очков (приём)", 15, "sales"),
    "eye_exam": Service("eye_exam", "Проверка зрения / подбор линз", 40, "optometry"),
    "frame_fitting": Service("frame_fitting", "Подбор и примерка оправы", 30, "sales"),
    "assessment": Service("assessment", "Осмотр и оценка ремонта", 20, "repair"),
}

# Синонимы, которые может прислать модель.
_SERVICE_ALIASES = {
    "носоупоры": "nose_pads",
    "замена носоупоров": "nose_pads",
    "винт": "screw",
    "линза": "fallen_lens",
    "выпавшая линза": "fallen_lens",
    "правка": "adjust",
    "правка оправы": "adjust",
    "поправить оправу": "adjust",
    "резьба": "thread",
    "нарезка резьбы": "thread",
    "склейка": "glue",
    "склеить": "glue",
    "пайка": "solder_steel",
    "пайка сталь": "solder_steel",
    "пайка стальная": "solder_steel",
    "сталь": "solder_steel",
    "латунь": "solder_steel",
    "пайка титан": "solder_titanium",
    "пайка титана": "solder_titanium",
    "титан": "solder_titanium",
    "пайка_титан": "solder_titanium",
    "установка линз": "lens_install",
    "вставить линзу": "lens_install",
    "установка_линз": "lens_install",
    "изготовление": "glasses_make",
    "изготовление очков": "glasses_make",
    "сделать очки": "glasses_make",
    "проверка зрения": "eye_exam",
    "проверить зрение": "eye_exam",
    "приём_врача": "eye_exam",
    "примерка": "frame_fitting",
    "примерка оправы": "frame_fitting",
    "подбор оправы": "frame_fitting",
    "подобрать оправу": "frame_fitting",
    "подобрать очки": "frame_fitting",
    "подбор очков": "frame_fitting",
    "осмотр": "assessment",
    "оценка ремонта": "assessment",
}

# Длинные синонимы проверяем первыми: «пайка титана» точнее, чем «пайка».
_ALIASES_BY_LENGTH = sorted(_SERVICE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)

_DEFAULT_MASTERS = {
    "masters": [
        {
            "name": "Андрей",
            "role": "мастер-оптик",
            "skills": ["repair"],
            "days": [1, 2, 3, 4, 5, 6],
            "start": "09:00",
            "end": "20:00",
            "lunch": ["14:00", "14:30"],
            "slot_min": 30,
        },
        {
            "name": "Елена",
            "role": "оптометрист",
            "skills": ["optometry"],
            "days": [1, 2, 3, 4, 5, 6],
            "start": "10:00",
            "end": "19:00",
            "lunch": ["13:00", "14:00"],
            "slot_min": 30,
        },
        {
            "name": "Марина",
            "role": "продавец-консультант",
            "skills": ["sales"],
            "days": [0, 1, 2, 3, 4, 5, 6],
            "start": "10:00",
            "end": "20:00",
            "lunch": ["14:00", "15:00"],
            "slot_min": 30,
        },
    ]
}


@dataclass
class Master:
    name: str
    role: str
    skills: list[str]
    days: list[int]
    start: time
    end: time
    lunch: tuple[time, time] | None
    slot_min: int = 30
    extra_days_off: list[date] = field(default_factory=list)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return fallback


def load_masters(path: Path, tz: ZoneInfo | None = None) -> list[Master]:
    data = _DEFAULT_MASTERS
    if Path(path).exists():
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("masters"), list):
                data = loaded
        except (OSError, ValueError):
            data = _DEFAULT_MASTERS

    masters: list[Master] = []
    for item in data.get("masters", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        lunch = item.get("lunch")
        lunch_pair = None
        if isinstance(lunch, list) and len(lunch) == 2:
            lunch_pair = (
                _parse_hhmm(lunch[0], time(14, 0)),
                _parse_hhmm(lunch[1], time(14, 30)),
            )
        try:
            days = sorted({int(d) for d in item.get("days", [0, 1, 2, 3, 4, 5, 6]) if 0 <= int(d) <= 6})
        except (TypeError, ValueError):
            days = [0, 1, 2, 3, 4, 5, 6]
        masters.append(
            Master(
                name=str(item["name"])[:40],
                role=str(item.get("role", ""))[:60],
                skills=[str(s) for s in item.get("skills", ["sales"])],
                days=days or [0, 1, 2, 3, 4, 5, 6],
                start=_parse_hhmm(item.get("start", "10:00"), time(10, 0)),
                end=_parse_hhmm(item.get("end", "20:00"), time(20, 0)),
                lunch=lunch_pair,
                slot_min=max(10, min(120, int(item.get("slot_min", 30) or 30))),
            )
        )
    return masters or load_masters(Path("__none__"), tz)


def masters_table(masters: list[Master]) -> str:
    if not masters:
        return "—"
    lines = []
    for m in masters:
        days = ", ".join(["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d] for d in m.days)
        lines.append(
            f"{m.name} ({m.role}): {m.start:%H:%M}–{m.end:%H:%M}, {days}; "
            f"умеет: {', '.join(m.skills)}"
        )
    return "; ".join(lines)


def resolve_service(raw: str) -> Service | None:
    if not raw:
        return None
    key = str(raw).strip().lower().replace("ё", "е")
    if key in SERVICES:
        return SERVICES[key]
    if key in _SERVICE_ALIASES:
        return SERVICES[_SERVICE_ALIASES[key]]
    for service in SERVICES.values():
        if service.title.lower().replace("ё", "е") == key:
            return service

    # «пайка титана», «подобрать оправу», «проверка зрения» и прочие свободные формулировки.
    for alias, target in _ALIASES_BY_LENGTH:
        if alias in key:
            return SERVICES[target]

    # Модель может прислать свободную формулировку («пайка титана», «замена носоупоров»).
    # Сравниваем по усечённым основам слов, чтобы «титана» совпадало с «титан».
    def stems(text: str) -> set[str]:
        words = re.findall(r"[а-яa-z0-9]+", text.lower().replace("ё", "е"))
        return {word[:5] for word in words if len(word) >= 3}

    query = stems(key)
    if not query:
        return None
    best: tuple[float, Service] | None = None
    for service in SERVICES.values():
        score = len(query & stems(service.title)) / len(query)
        if best is None or score > best[0]:
            best = (score, service)
    if best and best[0] >= 0.5:
        return best[1]
    return None


def _overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


def _to_time(value: str) -> time | None:
    try:
        hours, minutes = str(value).split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


def slot_id_for(day: date, slot_time: time, master: str) -> str:
    return f"{day.isoformat()}T{slot_time:%H:%M}|{master}"


def date_label(day: date) -> str:
    """Готовая подпись даты: «суббота, 07.11.2026».

    Модель не должна вычислять день недели сама — только копировать строку.
    """
    return f"{WEEKDAYS_RU[day.weekday()]}, {day:%d.%m.%Y}"


_WEEKDAY_WORDS = {
    "понедельник": 0,
    "понедельника": 0,
    "понедельнику": 0,
    "пн": 0,
    "вторник": 1,
    "вторника": 1,
    "вторнику": 1,
    "вт": 1,
    "среда": 2,
    "среду": 2,
    "среды": 2,
    "ср": 2,
    "четверг": 3,
    "четверга": 3,
    "четвергу": 3,
    "чт": 3,
    "пятница": 4,
    "пятницу": 4,
    "пятницы": 4,
    "пт": 4,
    "суббота": 5,
    "субботу": 5,
    "субботы": 5,
    "сб": 5,
    "воскресенье": 6,
    "воскресенья": 6,
    "воскресенью": 6,
    "вс": 6,
}

_MONTH_WORDS = {
    "январь": 1, "января": 1,
    "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7,
    "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11,
    "декабрь": 12, "декабря": 12,
}

_WEEKDAY_PATTERN = re.compile(
    r"(?<![а-яё])(" + "|".join(sorted(_WEEKDAY_WORDS, key=len, reverse=True)) + r")(?![а-яё])"
)


def parse_user_date(raw: str, today: date) -> date | None:
    """Понимает «завтра», «в субботу», «1 ноября», «03.11.2026», «следующая пятница».

    Нужна, чтобы модель не вычисляла дату и день недели сама — она ошибается.
    """
    text = str(raw or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    text = re.sub(r"^(на|в|во)\s+", "", text)
    text = re.sub(r"\s+(число|числа)$", "", text)

    if text == "сегодня":
        return today
    if text == "завтра":
        return today + timedelta(days=1)
    if text == "послезавтра":
        return today + timedelta(days=2)

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", text)
    if match:
        day_n, month, year_s = match.groups()
        year = int(year_s) if year_s else today.year
        if year < 100:
            year += 2000
        try:
            result = date(year, int(month), int(day_n))
        except ValueError:
            return None
        if not year_s and result < today - timedelta(days=30):
            result = result.replace(year=year + 1)
        return result

    match = re.search(r"(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?", text)
    if match:
        month = _MONTH_WORDS.get(match.group(2))
        if month:
            year = int(match.group(3)) if match.group(3) else today.year
            try:
                result = date(year, month, int(match.group(1)))
            except ValueError:
                return None
            if not match.group(3) and result < today - timedelta(days=30):
                result = result.replace(year=year + 1)
            return result

    if "следующ" in text and "недел" in text:
        return today + timedelta(days=7 - today.weekday())

    match = _WEEKDAY_PATTERN.search(text)
    if match:
        target = _WEEKDAY_WORDS[match.group(1)]
        if "следующ" in text:
            next_monday = today + timedelta(days=7 - today.weekday())
            return next_monday + timedelta(days=target)
        ahead = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    return None




def parse_slot_id(slot_id: str) -> tuple[date, time, str] | None:
    match = SLOT_ID_RE.match(str(slot_id or "").strip())
    if not match:
        return None
    day_s, time_s, master = match.groups()
    try:
        return date.fromisoformat(day_s), datetime.strptime(time_s, "%H:%M").time(), master
    except ValueError:
        return None


def free_slots(
    *,
    masters: list[Master],
    service: Service,
    date_from: date,
    date_to: date,
    bookings: list[dict],
    holds: dict[str, datetime],
    now: datetime,
    master_name: str | None = None,
    min_lead_min: int = 60,
    horizon_days: int = 30,
    limit: int = 12,
) -> list[dict]:
    """Возвращает только реально свободные слоты. Ни одного «придуманного» времени."""
    now = now.replace(tzinfo=None)  # расписание живёт в локальном времени без tz
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > 14:
        date_to = date_from + timedelta(days=14)
    if date_to > now.date() + timedelta(days=horizon_days):
        date_to = now.date() + timedelta(days=horizon_days)

    busy: dict[tuple[str, str], list[tuple[time, time]]] = {}
    for booking in bookings:
        if str(booking.get("status", "")).lower() in ("cancelled", "отменена", "cancel"):
            continue
        day_s = str(booking.get("date", ""))
        start = _to_time(booking.get("time", ""))
        if not day_s or start is None:
            continue
        duration = int(booking.get("duration") or 30)
        end_dt = datetime.combine(date.fromisoformat(day_s), start) + timedelta(minutes=duration)
        key = (day_s, str(booking.get("master", "")))
        busy.setdefault(key, []).append((start, end_dt.time()))

    result: list[dict] = []
    day = date_from
    while day <= date_to and len(result) < limit:
        for master in masters:
            if master_name and master.name.lower() != master_name.strip().lower():
                continue
            if service.skill not in master.skills:
                continue
            if day.weekday() not in master.days:
                continue
            cursor = datetime.combine(day, master.start)
            day_end = datetime.combine(day, master.end)
            while cursor + timedelta(minutes=service.duration) <= day_end:
                slot_start_dt = cursor
                slot_end_dt = cursor + timedelta(minutes=service.duration)
                slot_start = slot_start_dt.time()
                slot_end = slot_end_dt.time()
                cursor = cursor + timedelta(minutes=master.slot_min)

                if slot_start_dt < now + timedelta(minutes=min_lead_min):
                    continue
                if master.lunch and _overlaps(slot_start, slot_end, master.lunch[0], master.lunch[1]):
                    continue

                conflict = False
                for b_start, b_end in busy.get((day.isoformat(), master.name), []):
                    if _overlaps(slot_start, slot_end, b_start, b_end):
                        conflict = True
                        break
                if conflict:
                    continue

                sid = slot_id_for(day, slot_start, master.name)
                hold_until = holds.get(sid)
                if hold_until and hold_until.replace(tzinfo=None) > now:
                    continue

                result.append(
                    {
                        "slot_id": sid,
                        "date": day.isoformat(),
                        "date_label": date_label(day),
                        "time": f"{slot_start:%H:%M}",
                        "master": master.name,
                        "duration_min": service.duration,
                        "service": service.title,
                    }
                )
                if len(result) >= limit:
                    break
            if len(result) >= limit:
                break
        day += timedelta(days=1)

    result.sort(key=lambda s: (s["date"], s["time"]))
    return result


def is_slot_free(
    *,
    masters: list[Master],
    service: Service,
    slot_id: str,
    bookings: list[dict],
    now: datetime,
) -> bool:
    parsed = parse_slot_id(slot_id)
    if not parsed:
        return False
    now = now.replace(tzinfo=None)
    day, start, master_name = parsed
    master = next((m for m in masters if m.name == master_name), None)
    if not master or service.skill not in master.skills:
        return False
    if day.weekday() not in master.days:
        return False
    if day < now.date() or day > now.date() + timedelta(days=30):
        return False

    start_dt = datetime.combine(day, start)
    end_dt = start_dt + timedelta(minutes=service.duration)
    if start_dt < now + timedelta(minutes=1):
        return False
    if not (datetime.combine(day, master.start) <= start_dt and end_dt.time() <= master.end):
        return False

    if master.lunch and _overlaps(start, end_dt.time(), master.lunch[0], master.lunch[1]):
        return False

    for booking in bookings:
        if str(booking.get("status", "")).lower() in ("cancelled", "отменена", "cancel"):
            continue
        if str(booking.get("date")) != day.isoformat() or str(booking.get("master")) != master_name:
            continue
        b_start = _to_time(booking.get("time", ""))
        if b_start is None:
            continue
        b_end = (datetime.combine(day, b_start) + timedelta(minutes=int(booking.get("duration") or 30))).time()
        if _overlaps(start, end_dt.time(), b_start, b_end):
            return False
    return True
