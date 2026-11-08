"""Инструменты модели: свободные слоты, запись, заявка, каталог, расчёт, передача."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from safety import is_valid_name, normalize_phone
from schedule import (
    SERVICES,
    date_label,
    free_slots,
    is_slot_free,
    load_masters,
    parse_slot_id,
    parse_user_date,
    resolve_service,
)

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": (
                "Возвращает реально свободные слоты записи. Единственный источник времени. "
                "Если клиент не назвал дату — запроси ближайшие 3 дня."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": (
                            "Услуга: nose_pads, screw, fallen_lens, adjust, thread, glue, "
                            "solder_steel, solder_titanium, lens_install, glasses_make, "
                            "eye_exam, frame_fitting, assessment"
                        ),
                    },
                    "date_from": {"type": "string", "description": "Дата начала, YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "Дата конца, YYYY-MM-DD"},
                    "when": {
                        "type": "string",
                        "description": (
                            "Дата словами клиента: «завтра», «в субботу», «1 ноября», "
                            "«следующая пятница». Передавай фразу как есть, не вычисляй дату сам."
                        ),
                    },
                    "master": {"type": "string", "description": "Имя мастера, если клиент выбрал"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_slot",
            "description": (
                "Создаёт запись. Вызывать только после подтверждения клиентом имени, услуги, "
                "даты, времени и мастера."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {
                        "type": "string",
                        "description": (
                            "Скопируй slot_id из ответа get_free_slots целиком. "
                            "Можно передать номер варианта (1–6) или время ЧЧ:ММ из списка."
                        ),
                    },
                    "date": {
                        "type": "string",
                        "description": "Если slot_id не скопировался — дата записи, YYYY-MM-DD",
                    },
                    "time": {
                        "type": "string",
                        "description": "Если slot_id не скопировался — время записи, ЧЧ:ММ",
                    },
                    "master": {
                        "type": "string",
                        "description": "Имя мастера из ответа get_free_slots, если клиент его выбрал",
                    },
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "service": {"type": "string"},
                    "pd_consent": {
                        "type": "boolean",
                        "description": "Клиент согласен на обработку персональных данных",
                    },
                    "comment": {"type": "string"},
                    "reschedule": {
                        "type": "boolean",
                        "description": (
                            "true — клиент подтвердил перенос уже существующей записи "
                            "на новое время. Без подтверждения не ставь."
                        ),
                    },
                },
                "required": ["name", "phone", "service", "pd_consent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": (
                "Сохраняет заявку в таблицу администратора. Вызывай всегда: и после записи, "
                "и если клиент пока не готов записываться."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "request_type": {
                        "type": "string",
                        "description": "frames | lenses | repair | booking | question",
                    },
                    "service": {"type": "string"},
                    "comment": {"type": "string"},
                    "status": {
                        "type": "string",
                        "description": "Новая | Подумает | Передана человеку | Не отвечает",
                    },
                    "pd_consent": {"type": "boolean"},
                    "total_estimate": {"type": "number"},
                },
                "required": ["request_type", "pd_consent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": "Ассортимент оправ: модель, материал, цена, наличие, ссылка на фото.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Материал, цвет или модель"},
                    "budget_max": {"type": "number", "description": "Максимальная цена, ₽"},
                    "limit": {"type": "integer", "description": "Сколько позиций вернуть, максимум 5"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc_order",
            "description": "Считает итоговую стоимость: оправа + линзы + работа.",
            "parameters": {
                "type": "object",
                "properties": {
                    "frame_price": {"type": "number"},
                    "lens_price": {"type": "number"},
                    "labor_price": {"type": "number"},
                },
                "required": ["frame_price", "lens_price", "labor_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_human",
            "description": "Передаёт диалог администратору: жалоба, сложный случай, просьба человека.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "client_request | complaint | complex | discount | other",
                    },
                    "summary": {"type": "string", "description": "Короткое описание для администратора"},
                },
                "required": ["reason", "summary"],
            },
        },
    },
]


_SLOT_HINT_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})[T ]+(\d{1,2}):(\d{2})(?:\s*[|,]\s*(.{2,40}))?$"
)
_DOTTED_HINT_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*[T ,]+\s*(\d{1,2}):(\d{2})(?:\s*[|,]\s*(.{2,40}))?$"
)


def _clean_master(raw: str | None) -> str:
    text = str(raw or "").strip()
    if text.lower().startswith("мастер"):
        text = text[6:].strip(" :,-")
    return text


def _parse_slot_hint(
    raw: str, today: date | None = None
) -> tuple[date, time, str | None] | None:
    """Разбирает «2026-11-03 10:00», «03.11.2026, 10:00», «03.11 в 10:00»."""
    text = str(raw or "").strip()
    if not text:
        return None
    today = today or date.today()

    match = _SLOT_HINT_RE.match(text)
    if match:
        year, month, day_n, hour, minute, master = match.groups()
        try:
            day = date(int(year), int(month), int(day_n))
            slot_time = time(int(hour), int(minute))
        except ValueError:
            return None
        return day, slot_time, _clean_master(master)

    match = _DOTTED_HINT_RE.match(text)
    if match:
        day_n, month, year_s, hour, minute, master = match.groups()
        year = int(year_s) if year_s else today.year
        if year < 100:
            year += 2000
        try:
            day = date(year, int(month), int(day_n))
            slot_time = time(int(hour), int(minute))
        except ValueError:
            return None
        if not year_s and day < today - timedelta(days=30):
            try:
                day = day.replace(year=day.year + 1)
            except ValueError:
                return None
        return day, slot_time, _clean_master(master)
    return None


_SAFE_ARG_FIELDS = (
    "service",
    "date_from",
    "date_to",
    "date",
    "time",
    "when",
    "master",
    "slot_id",
    "pd_consent",
    "frame_price",
    "lens_price",
    "labor_price",
    "request_type",
    "reason",
    "max_price",
)


def _log_tool_call(name: str, args: dict, result: dict, user_id: int) -> None:
    """Пишет в лог только безопасные поля: без имени, телефона и текста клиента."""
    safe_args = {key: args[key] for key in _SAFE_ARG_FIELDS if key in args}
    if name == "get_free_slots":
        first = (result.get("slots") or [None])[0]
        outcome = {
            "count": result.get("count"),
            "error": result.get("error"),
            "first": first,
        }
    else:
        outcome = {
            key: result[key]
            for key in ("ok", "error", "lead_id", "date", "time", "master", "count")
            if key in result
        }
    log.info("tool=%s user=%s args=%s result=%s", name, user_id, safe_args, outcome)


class Toolbox:
    def __init__(self, *, settings, storage, catalog_path: Path, masters_path: Path) -> None:
        self.settings = settings
        self.storage = storage
        self.catalog_path = Path(catalog_path)
        self.masters = load_masters(masters_path)
        self._catalog_cache: tuple[float, list[dict]] | None = None
        self._last_slots: dict[int, list[dict]] = {}
        # Колбэки ставит Dialog: уведомления администратору.
        self.on_handoff = None
        self.on_booking = None

    # ---------- dispatch ----------

    def execute(self, name: str, args: dict, *, user_id: int) -> dict:
        handlers = {
            "get_free_slots": self._get_free_slots,
            "book_slot": self._book_slot,
            "save_lead": self._save_lead,
            "get_catalog": self._get_catalog,
            "calc_order": self._calc_order,
            "handoff_to_human": self._handoff,
        }
        handler = handlers.get(name)
        if not handler:
            return {"error": "unknown_tool", "message": "Такого действия нет"}
        try:
            result = handler(args or {}, user_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка инструмента %s", name)
            result = {"error": "internal", "message": str(exc)[:200]}
        _log_tool_call(name, args or {}, result, user_id)
        return result

    # ---------- реализация ----------

    def _now(self) -> datetime:
        return datetime.now(self.settings.tz)

    def _get_free_slots(self, args: dict, user_id: int) -> dict:
        service = resolve_service(str(args.get("service", "")))
        if not service:
            return {
                "error": "unknown_service",
                "message": "Уточните услугу: ремонт, примерка, проверка зрения или изготовление очков",
            }
        now = self._now()
        today = now.date()

        def as_date(raw) -> date | None:
            text = str(raw or "").strip()
            return parse_user_date(text, today) if text else None

        when = args.get("when")
        date_from = as_date(when) or as_date(args.get("date_from"))
        if (when or args.get("date_from")) and date_from is None:
            return {
                "error": "bad_date",
                "message": (
                    "Не поняла дату. Передай фразу клиента как есть в when "
                    "(«завтра», «в субботу», «1 ноября») или дату YYYY-MM-DD в date_from."
                ),
            }
        date_to = as_date(args.get("date_to"))
        if args.get("date_to") and date_to is None:
            return {"error": "bad_date", "message": "Дата должна быть в формате YYYY-MM-DD"}
        date_from = date_from or today
        date_to = date_to or (date_from + timedelta(days=2))

        if date_from < now.date():
            date_from = now.date()
        bookings = self.storage.bookings_between(
            date_from.isoformat(), (date_to + timedelta(days=1)).isoformat()
        )
        slots = free_slots(
            masters=self.masters,
            service=service,
            date_from=date_from,
            date_to=date_to,
            bookings=bookings,
            holds=self.storage.active_holds(),
            now=now,
            master_name=args.get("master"),
            min_lead_min=60,
            horizon_days=30,
            limit=3,
        )
        if not slots:
            note = "Свободных слотов в эти дни нет. Не придумывай время, предложи другие дни."
        else:
            note = (
                "Покажи клиенту 2–3 варианта из списка. В каждом слоте есть date_label "
                "(например, «суббота, 07.11.2026») — копируй день недели и дату оттуда, "
                "сам день недели не вычисляй."
            )
        self._last_slots[user_id] = slots
        # Не копим память: храним списки только для последних 500 клиентов.
        while len(self._last_slots) > 500:
            self._last_slots.pop(next(iter(self._last_slots)))
        return {
            "service": service.title,
            "duration_min": service.duration,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "slots": slots,
            "count": len(slots),
            "note": note,
        }

    def _suggest_alternatives(self, service, day: date, user_id: int) -> list[dict]:
        """До трёх ближайших свободных слотов — чтобы бот не зацикливался на ошибке."""
        if service is None:
            return []
        now = self._now()
        date_from = max(day, now.date())
        date_to = date_from + timedelta(days=6)
        try:
            bookings = self.storage.bookings_between(
                date_from.isoformat(), (date_to + timedelta(days=1)).isoformat()
            )
            return free_slots(
                masters=self.masters,
                service=service,
                date_from=date_from,
                date_to=date_to,
                bookings=bookings,
                holds=self.storage.active_holds(),
                now=now,
                min_lead_min=60,
                horizon_days=30,
                limit=3,
            )
        except Exception:  # noqa: BLE001
            return []

    def _resolve_slot_arg(
        self,
        raw: str,
        user_id: int,
        *,
        service=None,
        date_hint: str | None = None,
        time_hint: str | None = None,
        master_hint: str | None = None,
    ) -> str:
        """Приводит аргумент модели к каноническому slot_id.

        Модель иногда присылает «1», «09:00», «2026-11-03 10:00» или
        «03.11.2026 в 10:00» вместо полного slot_id. Разрешаем только варианты
        из последнего ответа get_free_slots этого пользователя — свободное время
        всё равно перепроверяется перед записью.
        """
        raw = str(raw or "").strip()
        if parse_slot_id(raw):
            return raw

        slots = self._last_slots.get(user_id) or []

        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(slots):
                return slots[index]["slot_id"]

        time_match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", raw)
        if time_match:
            wanted = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
            if date_hint:
                for slot in slots:
                    if slot["date"] == date_hint.strip() and slot["time"] == wanted:
                        return slot["slot_id"]
            for slot in slots:
                if slot["time"] == wanted:
                    return slot["slot_id"]

        hint = _parse_slot_hint(raw)
        if hint is None and date_hint and time_hint:
            hint = _parse_slot_hint(f"{date_hint.strip()} {time_hint.strip()}")
        if hint is None:
            return raw

        day, slot_time, master = hint
        wanted_master = _clean_master(master or master_hint)
        wanted_key = wanted_master.strip().lower()
        wanted_time = f"{slot_time:%H:%M}"

        for slot in slots:
            if slot["date"] != day.isoformat() or slot["time"] != wanted_time:
                continue
            if not wanted_key or slot["master"].strip().lower() == wanted_key:
                return slot["slot_id"]

        # Список слотов мог не сохраниться (перезапуск бота) — проверяем дату
        # и время по реальному расписанию и подбираем подходящего мастера.
        if service is not None:
            candidates = [
                m
                for m in self.masters
                if service.skill in m.skills
                and (not wanted_key or m.name.strip().lower() == wanted_key)
            ]
            bookings = self.storage.bookings_on(day.isoformat())
            for master_obj in candidates:
                candidate = f"{day.isoformat()}T{wanted_time}|{master_obj.name}"
                if is_slot_free(
                    masters=self.masters,
                    service=service,
                    slot_id=candidate,
                    bookings=bookings,
                    now=self._now(),
                ):
                    return candidate
        return raw

    def _book_slot(self, args: dict, user_id: int) -> dict:
        name = str(args.get("name") or "").strip()
        phone_raw = str(args.get("phone") or "").strip()
        service = resolve_service(str(args.get("service", "")))
        consent = bool(args.get("pd_consent"))
        slot_id = self._resolve_slot_arg(
            str(args.get("slot_id") or ""),
            user_id,
            service=service,
            date_hint=str(args.get("date") or ""),
            time_hint=str(args.get("time") or ""),
            master_hint=str(args.get("master") or ""),
        )

        if not consent:
            return {
                "error": "consent_required",
                "message": (
                    "Согласие ещё не получено. Не говори про ошибку: одной короткой "
                    "фразой спроси «Согласны на обработку данных для записи?» и после "
                    "«да» повтори book_slot с теми же данными и pd_consent=true."
                ),
                "slot_id": slot_id,
            }
        if not is_valid_name(name):
            return {"error": "invalid_name", "message": "Уточните имя клиента"}
        phone = normalize_phone(phone_raw)
        if not phone:
            return {"error": "invalid_phone", "message": "Телефон не распознан, попроси номер ещё раз"}
        parsed = parse_slot_id(slot_id)
        if not service:
            return {
                "error": "unknown_service",
                "message": "Не поняла услугу. Уточни у клиента: ремонт, примерка, проверка зрения или изготовление.",
            }
        if not parsed:
            return {
                "error": "invalid_slot",
                "message": (
                    "Не поняла, на какое время записывать. Не извиняйся и не говори "
                    "про ошибку: возьми точный slot_id из get_free_slots или передай "
                    "date и time этого слота."
                ),
                "alternative_slots": self._suggest_alternatives(service, self._now().date(), user_id),
            }

        day, slot_time, master = parsed
        now = self._now()

        # У клиента может быть другая активная запись: не создаём вторую молча.
        existing = self.storage.find_upcoming_lead(user_id, now.date().isoformat())
        reschedule = bool(args.get("reschedule"))
        old_booking: dict | None = None

        if existing:
            same_slot = (
                str(existing["date"]) == day.isoformat()
                and str(existing["time"]) == f"{slot_time:%H:%M}"
                and str(existing["master"]) == master
            )
            if same_slot:
                return {
                    "ok": True,
                    "already_booked": True,
                    "same_slot": True,
                    "lead_id": existing["id"],
                    "date": day.isoformat(),
                    "date_label": date_label(day),
                    "time": existing["time"],
                    "master": existing["master"],
                    "service": existing["service"],
                    "message": "Клиент уже записан на это время. Подтверди запись словами, новую не создавай.",
                }
            old_day = date.fromisoformat(str(existing["date"]))
            if not reschedule:
                return {
                    "ok": False,
                    "already_booked": True,
                    "needs_reschedule": True,
                    "lead_id": existing["id"],
                    "date": existing["date"],
                    "date_label": date_label(old_day),
                    "time": existing["time"],
                    "master": existing["master"],
                    "service": existing["service"],
                    "message": (
                        f"У клиента уже есть запись: {date_label(old_day)} в "
                        f"{existing['time']}, мастер {existing['master']}. Спроси: "
                        "перенести её на новое время или записать дополнительно. "
                        "Если перенести — повтори book_slot с новыми данными и reschedule=true."
                    ),
                }
            # Перенос: сначала освобождаем старое время (новое может пересекаться),
            # при неудаче вернём его обратно.
            old_booking = {
                "date": str(existing["date"]),
                "time": str(existing["time"]),
                "duration": int(existing["duration"] or 30),
                "master": str(existing["master"]),
                "service": existing["service"],
                "user_id": user_id,
            }
            self.storage.cancel_booking(
                user_id, old_booking["date"], old_booking["time"], old_booking["master"]
            )

        def restore_old_booking() -> None:
            if old_booking and self.storage.create_booking(old_booking) is None:
                log.error("Не удалось вернуть прежнюю запись после неудачного переноса")

        bookings = self.storage.bookings_on(day.isoformat())
        if not is_slot_free(
            masters=self.masters, service=service, slot_id=slot_id, bookings=bookings, now=now
        ):
            restore_old_booking()
            return {
                "error": "slot_taken",
                "message": "Это время недоступно. Предложи клиенту вариант из alternative_slots.",
                "alternative_slots": self._suggest_alternatives(service, day, user_id),
            }

        booking_id = self.storage.create_booking(
            {
                "date": day.isoformat(),
                "time": f"{slot_time:%H:%M}",
                "duration": service.duration,
                "master": master,
                "service": service.title,
                "user_id": user_id,
            }
        )
        if not booking_id:
            restore_old_booking()
            return {
                "error": "slot_taken",
                "message": "Только что заняли. Предложи другое время из alternative_slots.",
                "alternative_slots": self._suggest_alternatives(service, day, user_id),
            }

        if old_booking and existing:
            self.storage.cancel_lead(
                str(existing["id"]),
                f"Перенесена на {day:%d.%m.%Y} {slot_time:%H:%M}",
            )

        lead_id = self.storage.create_lead(
            {
                "user_id": user_id,
                "name": name,
                "phone": phone,
                "channel": "vk",
                "request_type": "booking",
                "service": service.title,
                "date": day.isoformat(),
                "time": f"{slot_time:%H:%M}",
                "duration": service.duration,
                "master": master,
                "status": "Подтверждена",
                "comment": str(args.get("comment") or "")[:500] or None,
                "consent": consent,
                "source": "vk",
            }
        )
        reminder = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
        if callable(self.on_booking):
            try:
                self.on_booking(
                    f"{name}, {phone}\n{service.title}\n"
                    f"{day:%d.%m.%Y} в {slot_time:%H:%M}, мастер {master}\n"
                    f"Заявка {lead_id}\nКлиент: vk.com/id{user_id}"
                )
            except Exception:  # noqa: BLE001
                log.warning("Не удалось уведомить о записи")
        return {
            "ok": True,
            "booking_id": booking_id,
            "lead_id": lead_id,
            "date": day.isoformat(),
            "date_label": date_label(day),
            "time": f"{slot_time:%H:%M}",
            "master": master,
            "service": service.title,
            "duration_min": service.duration,
            "reminder_hint": reminder.strftime("%d.%m %H:%M"),
        }

    def _save_lead(self, args: dict, user_id: int) -> dict:
        # book_slot уже создал заявку — вторую строку в Excel не плодим.
        recent = self.storage.find_recent_lead(user_id, minutes=15)
        if recent:
            return {
                "ok": True,
                "lead_id": recent["id"],
                "deduplicated": True,
                "phone_saved": bool(recent["phone"]),
                "message": "Заявка уже сохранена ранее, повторно не создаю.",
            }

        consent = bool(args.get("pd_consent"))
        phone_raw = str(args.get("phone") or "").strip()
        phone = normalize_phone(phone_raw) if consent and phone_raw else None
        name = str(args.get("name") or "").strip()
        if name and not is_valid_name(name):
            name = name[:40]

        allowed_status = {"Новая", "Подумает", "Передана человеку", "Не отвечает"}
        status = str(args.get("status") or "Новая")
        if status not in allowed_status:
            status = "Новая"

        total = args.get("total_estimate")
        lead_id = self.storage.create_lead(
            {
                "user_id": user_id,
                "name": name or None,
                "phone": phone,
                "channel": "vk",
                "request_type": str(args.get("request_type") or "question")[:40],
                "service": str(args.get("service") or "")[:120] or None,
                "status": status,
                "comment": str(args.get("comment") or "")[:500] or None,
                "consent": consent,
                "source": "vk",
                "total": float(total) if isinstance(total, (int, float)) else None,
            }
        )
        return {"ok": True, "lead_id": lead_id, "phone_saved": bool(phone)}

    def _load_catalog(self) -> list[dict]:
        if not self.catalog_path.exists():
            return []
        mtime = self.catalog_path.stat().st_mtime
        if self._catalog_cache and self._catalog_cache[0] == mtime:
            return self._catalog_cache[1]
        try:
            wb = load_workbook(self.catalog_path, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            log.error("Каталог не открылся: %s", exc)
            return []
        rows: list[dict] = []
        try:
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            if not data:
                return []
            header = [str(c).strip().lower() if c else "" for c in data[0]]
            for row in data[1:]:
                item = {}
                for idx, value in enumerate(row):
                    if idx < len(header) and header[idx] and value is not None:
                        item[header[idx]] = value
                if item:
                    rows.append(item)
        finally:
            wb.close()
        self._catalog_cache = (mtime, rows)
        return rows

    def _get_catalog(self, args: dict, user_id: int) -> dict:
        items = self._load_catalog()
        if not items:
            return {
                "items": [],
                "note": "Каталог не подключён. Не выдумывай модели и цены — предложи подобрать вручную.",
            }

        query = str(args.get("query") or "").strip().lower()
        budget = args.get("budget_max")
        try:
            budget_val = float(budget) if budget is not None else None
        except (TypeError, ValueError):
            budget_val = None
        try:
            limit = max(1, min(5, int(args.get("limit") or 3)))
        except (TypeError, ValueError):
            limit = 3

        result: list[dict] = []
        for item in items:
            haystack = " ".join(str(v).lower() for v in item.values())
            if query and query not in haystack:
                continue
            price = item.get("цена") or item.get("price")
            try:
                price_val = float(price) if price is not None else None
            except (TypeError, ValueError):
                price_val = None
            if budget_val is not None and price_val is not None and price_val > budget_val:
                continue
            if str(item.get("наличие", "")).strip().lower() in ("нет", "0", "no"):
                continue
            result.append({k: v for k, v in item.items() if k in (
                "модель", "model", "материал", "material", "цвет", "color",
                "цена", "price", "наличие", "фото", "photo", "ссылка", "url"
            )})
            if len(result) >= limit:
                break
        return {"items": result, "count": len(result)}

    def _calc_order(self, args: dict, user_id: int) -> dict:
        def num(key: str) -> float:
            try:
                return max(0.0, float(args.get(key) or 0))
            except (TypeError, ValueError):
                return 0.0

        frame, lens, labor = num("frame_price"), num("lens_price"), num("labor_price")
        total = frame + lens + labor
        return {
            "frame_price": frame,
            "lens_price": lens,
            "labor_price": labor,
            "total": total,
            "text": f"{frame:,.0f} + {lens:,.0f} + {labor:,.0f} = {total:,.0f} ₽".replace(",", " "),
        }

    def _handoff(self, args: dict, user_id: int) -> dict:
        reason = str(args.get("reason") or "other")[:40]
        summary = str(args.get("summary") or "")[:400]
        lead_id = self.storage.create_lead(
            {
                "user_id": user_id,
                "channel": "vk",
                "request_type": "handoff",
                "service": f"Передача: {reason}",
                "status": "Передана человеку",
                "comment": summary,
                "consent": False,
                "source": "vk",
            }
        )
        if callable(self.on_handoff):
            try:
                self.on_handoff(summary or reason, user_id)
            except Exception:  # noqa: BLE001
                log.warning("Не удалось уведомить администратора о передаче")
        return {
            "ok": True,
            "lead_id": lead_id,
            "message": "Передала администратору. Он ответит в чате в рабочее время.",
        }