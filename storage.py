"""Хранилище: SQLite для работы бота + выгрузка заявок в Excel для администратора."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from safety import excel_safe, normalize_phone

log = logging.getLogger(__name__)

HEADERS: list[tuple[str, int]] = [
    ("ID заявки", 14),
    ("Дата и время создания", 20),
    ("Имя", 18),
    ("Телефон", 18),
    ("Канал", 12),
    ("Тип обращения", 16),
    ("Услуга", 30),
    ("Товар / модель", 26),
    ("Цена товара, ₽", 14),
    ("Линзы", 30),
    ("Цена линз, ₽", 13),
    ("Работа, ₽", 12),
    ("Итого, ₽", 12),
    ("Дата записи", 13),
    ("Время", 9),
    ("Длительность, мин", 15),
    ("Мастер", 14),
    ("Статус", 16),
    ("Напоминание", 18),
    ("Комментарий", 40),
    ("Согласие на ПД", 14),
    ("Источник", 16),
    ("Ответственный", 16),
    ("Причина отказа", 26),
    ("Фото / ссылка", 30),
]

LEAD_FIELDS = [
    "id",
    "created_at",
    "name",
    "phone",
    "channel",
    "request_type",
    "service",
    "product",
    "product_price",
    "lenses",
    "lenses_price",
    "labor",
    "total",
    "date",
    "time",
    "duration",
    "master",
    "status",
    "reminder",
    "comment",
    "consent",
    "source",
    "owner",
    "cancel_reason",
    "photo",
]

STATUSES = [
    "Новая",
    "Подтверждена",
    "Напомнено",
    "Пришёл",
    "Не пришёл",
    "Завершена",
    "Отменена",
    "Перенесена",
    "Передана человеку",
    "Не отвечает",
    "Подумает",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS seen (
    msg_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    name TEXT,
    phone TEXT,
    channel TEXT,
    request_type TEXT,
    service TEXT,
    product TEXT,
    product_price REAL,
    lenses TEXT,
    lenses_price REAL,
    labor REAL,
    total REAL,
    date TEXT,
    time TEXT,
    duration INTEGER,
    master TEXT,
    status TEXT,
    reminder TEXT,
    comment TEXT,
    consent INTEGER,
    source TEXT,
    owner TEXT,
    cancel_reason TEXT,
    photo TEXT,
    exported INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    lead_id TEXT,
    user_id INTEGER,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    duration INTEGER NOT NULL,
    master TEXT NOT NULL,
    service TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(date, master);

CREATE TABLE IF NOT EXISTS holds (
    slot_id TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0
);
"""


def _display_dt(value: str) -> str:
    """ISO → «дд.мм.гггг чч:мм» для колонки Excel. Не ISO — оставляем как есть."""
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def _normalize_date(value: object) -> str | None:
    """В базе дата всегда ISO (YYYY-MM-DD), чтобы поиск записей работал."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _display_date(value: object) -> object:
    """ISO → «дд.мм.гггг» для колонки Excel."""
    if not value:
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


class Storage:
    def __init__(self, db_path: Path, leads_path: Path, outbox_path: Path) -> None:
        self.db_path = Path(db_path)
        self.leads_path = Path(leads_path)
        self.outbox_path = Path(outbox_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._outbox_ids: set[str] = set()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.isolation_level = None  # ручное управление транзакциями
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- служебное ----------

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def mark_seen(self, msg_id: str) -> bool:
        """True, если сообщение новое. Защита от повторной обработки после рестарта."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO seen(msg_id, created_at) VALUES (?, ?)",
                (str(msg_id), self._now()),
            )
            return cur.rowcount == 1

    def incr_usage(self, day: str | None = None) -> int:
        day = day or date.today().isoformat()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO usage(day, calls) VALUES (?, 1) "
                "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
                (day,),
            )
            row = self.conn.execute("SELECT calls FROM usage WHERE day = ?", (day,)).fetchone()
        return int(row["calls"]) if row else 0

    def usage_today(self) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT calls FROM usage WHERE day = ?", (date.today().isoformat(),)
            ).fetchone()
        return int(row["calls"]) if row else 0

    # ---------- история диалога ----------

    def history(self, user_id: int, limit: int = 16, ttl_hours: int = 12) -> list[dict]:
        cutoff = (datetime.now() - timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages "
                "WHERE user_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
                (user_id, cutoff, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def add_message(self, user_id: int, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            return
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO messages(user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (user_id, role, content[:4000], self._now()),
            )

    def reset_history(self, user_id: int) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))

    # ---------- заявки ----------

    def next_lead_id(self) -> str:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT value FROM kv WHERE key = 'lead_seq'").fetchone()
            seq = int(row["value"]) + 1 if row else 1
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES ('lead_seq', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(seq),),
            )
        return f"Ф-{seq:06d}"

    def create_lead(self, data: dict[str, Any]) -> str:
        lead_id = data.get("id") or self.next_lead_id()
        consent = bool(data.get("consent"))
        # Без согласия телефон не сохраняем вообще — даже если он пришёл в данных.
        phone = normalize_phone(data.get("phone") or "") if consent and data.get("phone") else None
        values = {
            "id": lead_id,
            "created_at": data.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            "user_id": data.get("user_id"),
            "name": (data.get("name") or "")[:60] or None,
            "phone": phone,
            "channel": data.get("channel") or "vk",
            "request_type": data.get("request_type") or "consultation",
            "service": data.get("service"),
            "product": data.get("product"),
            "product_price": data.get("product_price"),
            "lenses": data.get("lenses"),
            "lenses_price": data.get("lenses_price"),
            "labor": data.get("labor"),
            "total": data.get("total"),
            "date": _normalize_date(data.get("date")),
            "time": data.get("time"),
            "duration": data.get("duration"),
            "master": data.get("master"),
            "status": data.get("status") or "Новая",
            "reminder": data.get("reminder"),
            "comment": data.get("comment"),
            "consent": 1 if consent else 0,
            "source": data.get("source") or "vk",
            "owner": data.get("owner"),
            "cancel_reason": data.get("cancel_reason"),
            "photo": data.get("photo"),
        }
        # Колонки — фиксированный список, значения — только через параметры.
        # user_id в Excel не выгружается, но нужен для поиска заявок клиента.
        columns_list = [*LEAD_FIELDS, "user_id"]
        columns = ", ".join(columns_list)
        placeholders = ", ".join(["?"] * len(columns_list))
        query = "INSERT INTO leads(" + columns + ") VALUES (" + placeholders + ")"
        with self._lock, self.conn:
            self.conn.execute(
                query, tuple(values[field_name] for field_name in columns_list)
            )
        self.export_leads()
        return lead_id

    def find_recent_lead(self, user_id: int, minutes: int = 15) -> sqlite3.Row | None:
        """Последняя заявка клиента за N минут — защита от дублей в Excel."""
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM leads WHERE user_id = ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, cutoff),
            ).fetchone()

    def find_active_lead(self, user_id: int, day: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM leads WHERE user_id = ? AND date = ? AND status NOT IN "
                "('Отменена', 'Завершена', 'Не пришёл') ORDER BY created_at DESC LIMIT 1",
                (user_id, day),
            ).fetchone()

    def find_upcoming_lead(self, user_id: int, from_day: str | None = None) -> sqlite3.Row | None:
        """Ближайшая активная запись клиента — чтобы не создать вторую молча."""
        day = from_day or datetime.now().date().isoformat()
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM leads WHERE user_id = ? AND date >= ? AND status NOT IN "
                "('Отменена', 'Завершена', 'Не пришёл') ORDER BY date, time LIMIT 1",
                (user_id, day),
            ).fetchone()

    def cancel_lead(self, lead_id: str, reason: str = "Перенос записи") -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE leads SET status = 'Отменена', cancel_reason = ? WHERE id = ?",
                (reason[:200], lead_id),
            )
        self.export_leads()

    def cancel_booking(self, user_id: int, day: str, slot_time: str, master: str) -> bool:
        """Освобождает время при переносе. Возвращает True, если запись была отменена."""
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE bookings SET status = 'Отменена' WHERE user_id = ? AND date = ? "
                "AND time = ? AND master = ? AND status NOT IN ('Отменена', 'cancelled')",
                (user_id, day, slot_time, master),
            )
            return cursor.rowcount > 0

    def pending_leads(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM leads WHERE exported = 0 ORDER BY created_at"
            ).fetchall()

    def mark_exported(self, lead_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE leads SET exported = 1 WHERE id = ?", (lead_id,))

    # ---------- записи ----------

    def bookings_between(self, date_from: str, date_to: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT date, time, duration, master, status FROM bookings "
                "WHERE date BETWEEN ? AND ?",
                (date_from, date_to),
            ).fetchall()
        return [dict(r) for r in rows]

    def bookings_on(self, day: str) -> list[dict]:
        return self.bookings_between(day, day)

    def create_booking(self, data: dict[str, Any]) -> str | None:
        """Атомарно проверяет пересечение и создаёт запись. None — слот уже занят."""
        day = data["date"]
        start = datetime.strptime(f"{day} {data['time']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=int(data["duration"]))
        with self._lock, self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self.conn.execute(
                    "SELECT time, duration FROM bookings WHERE date = ? AND master = ? "
                    "AND status NOT IN ('Отменена', 'cancelled')",
                    (day, data["master"]),
                ).fetchall()
                for row in rows:
                    b_start = datetime.strptime(f"{day} {row['time']}", "%Y-%m-%d %H:%M")
                    b_end = b_start + timedelta(minutes=int(row["duration"]))
                    if start < b_end and b_start < end:
                        self.conn.rollback()
                        return None
                stamp = int(datetime.now().timestamp() * 1000)
                booking_id = f"B-{stamp}"
                # Две записи подряд могут попасть в одну миллисекунду — ID должен быть уникальным.
                suffix = 0
                while self.conn.execute(
                    "SELECT 1 FROM bookings WHERE id = ?", (booking_id,)
                ).fetchone():
                    suffix += 1
                    booking_id = f"B-{stamp}-{suffix}"
                self.conn.execute(
                    "INSERT INTO bookings(id, lead_id, user_id, date, time, duration, master, "
                    "service, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Подтверждена', ?)",
                    (
                        booking_id,
                        data.get("lead_id"),
                        data.get("user_id"),
                        day,
                        data["time"],
                        int(data["duration"]),
                        data["master"],
                        data.get("service"),
                        self._now(),
                    ),
                )
                self.conn.commit()
                return booking_id
            except Exception:
                self.conn.rollback()
                raise

    # ---------- служебные ключи ----------

    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def set_hold(self, slot_id: str, minutes: int = 15) -> None:
        expires = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO holds(slot_id, expires_at) VALUES (?, ?) "
                "ON CONFLICT(slot_id) DO UPDATE SET expires_at = excluded.expires_at",
                (slot_id, expires),
            )

    def active_holds(self) -> dict[str, datetime]:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM holds WHERE expires_at < ?", (now,))
            rows = self.conn.execute("SELECT slot_id, expires_at FROM holds").fetchall()
        return {r["slot_id"]: datetime.fromisoformat(r["expires_at"]) for r in rows}

    # ---------- Excel ----------

    def ensure_workbook(self) -> None:
        if self.leads_path.exists():
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "Заявки"
        ws.append([title for title, _ in HEADERS])
        header_fill = PatternFill("solid", fgColor="1F4E78")
        for idx, (title, width) in enumerate(HEADERS, start=1):
            cell = ws.cell(row=1, column=idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[cell.column_letter].width = width
        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 32
        dv = DataValidation(type="list", formula1=f'"{",".join(STATUSES)}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add("R2:R10000")
        self.leads_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(self.leads_path)

    def export_leads(self) -> int:
        """Переносит невыгруженные заявки в Excel. При занятом файле — в outbox."""
        pending = self.pending_leads()
        if not pending:
            return 0
        try:
            self.ensure_workbook()
            wb = load_workbook(self.leads_path)
            ws = wb["Заявки"] if "Заявки" in wb.sheetnames else wb.active
            for row in pending:
                values = []
                for field_name in LEAD_FIELDS:
                    value = row[field_name]
                    if field_name == "consent":
                        value = "да" if value else "нет"
                    elif field_name == "created_at" and value:
                        value = _display_dt(str(value))
                    elif field_name == "date":
                        value = _display_date(value)
                    values.append(excel_safe(value))
                ws.append(values)
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
            wb.save(self.leads_path)
            for row in pending:
                self.mark_exported(row["id"])
            return len(pending)
        except PermissionError:
            log.warning("Файл leads.xlsx занят (открыт в Excel?). Заявки сохранены в outbox.")
            self._write_outbox(pending)
            return 0
        except Exception as exc:  # noqa: BLE001
            log.error("Ошибка выгрузки в Excel: %s", exc)
            self._write_outbox(pending)
            return 0

    def _write_outbox(self, rows: list[sqlite3.Row]) -> None:
        try:
            fresh = [row for row in rows if row["id"] not in self._outbox_ids]
            if not fresh:
                return
            with self.outbox_path.open("a", encoding="utf-8") as fh:
                for row in fresh:
                    fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    self._outbox_ids.add(row["id"])
        except OSError as exc:
            log.error("Не удалось записать outbox: %s", exc)
