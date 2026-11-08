"""Живой тест диалога без ВКонтакте: настоящая модель, временная база.

    python scripts/live_test.py
    python scripts/live_test.py "Сколько стоит пайка титана?"

Сообщения не уходят в сообщество, заявки не попадают в data/leads.xlsx.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import CATALOG_PATH, MASTERS_PATH, Settings  # noqa: E402
from dialog import Dialog  # noqa: E402
from llm import LLM  # noqa: E402
from prompts import build_system_prompt  # noqa: E402
from storage import Storage  # noqa: E402
from tools import Toolbox  # noqa: E402


class ConsoleVK:
    def send(self, peer_id, text, keyboard=None):
        print(f"\nИрина: {text}")
        return 1

    def mark_read(self, peer_id):
        pass

    def set_typing(self, peer_id):
        pass

    def notify_admin(self, admin_id, text):
        print(f"\n[администратору] {text}")


DEFAULT_SCRIPT = [
    "Здравствуйте! Сколько стоит пайка титановой оправы?",
    "Дужка отломилась, фото могу прислать позже. А когда можно подъехать?",
    "Давайте в субботу до обеда",
    "На 09:00 подойдёт. Анна, +7 916 123-45-67, на обработку данных согласна",
    "Да, всё верно, записывайте",
]


def main() -> int:
    script: list[str] = []
    now_arg: str | None = None
    for arg in sys.argv[1:]:
        if arg.startswith("--now="):
            now_arg = arg.split("=", 1)[1]
        else:
            script.append(arg)
    script = script or DEFAULT_SCRIPT
    settings = Settings.load()
    # В сценарии сообщения идут без паузы — отключаем лимиты, иначе часть
    # реплик останется без ответа и тест покажет ложную картину.
    settings = replace(
        settings,
        rate_per_minute=10_000,
        rate_per_hour=100_000,
        max_daily_calls=100_000,
    )
    fixed_now = None
    if now_arg:
        fixed_now = datetime.fromisoformat(now_arg).replace(tzinfo=settings.tz)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = Storage(tmp_path / "bot.db", tmp_path / "leads.xlsx", tmp_path / "outbox.jsonl")
        vk = ConsoleVK()
        toolbox = Toolbox(
            settings=settings,
            storage=storage,
            catalog_path=CATALOG_PATH,
            masters_path=MASTERS_PATH,
        )
        original_execute = toolbox.execute

        def traced(name, args, *, user_id):
            result = original_execute(name, args, user_id=user_id)
            safe_args = {k: v for k, v in args.items() if k not in ("name", "phone", "comment", "summary")}
            if name == "get_free_slots":
                brief = {
                    "service": result.get("service"),
                    "count": result.get("count"),
                    "slots": [
                        (s["date"], s["time"], s["master"]) for s in result.get("slots", [])
                    ],
                    "error": result.get("error"),
                }
            elif name == "book_slot":
                brief = {k: result.get(k) for k in ("ok", "error", "date", "time", "master", "lead_id", "message")}
            elif name == "save_lead":
                brief = {k: result.get(k) for k in ("ok", "error", "lead_id", "phone_saved", "message")}
            else:
                brief = result
            print(f"  [tool] {name}({safe_args}) -> {brief}")
            return result

        toolbox.execute = traced
        if fixed_now is not None:
            toolbox._now = lambda: fixed_now
        llm = LLM(settings, storage)
        dialog = Dialog(
            settings=settings,
            storage=storage,
            vk=vk,
            toolbox=toolbox,
            llm=llm,
            prompt_builder=(lambda: build_system_prompt(fixed_now)) if fixed_now else build_system_prompt,
            human_delay=False,
        )

        if fixed_now is not None:
            print(f"Фиксированное время: {fixed_now:%d.%m.%Y %H:%M}")

        print(f"Модель: {settings.model}. Сообщений в сценарии: {len(script)}")
        for index, text in enumerate(script, start=1):
            print(f"\nКлиент: {text}")
            dialog.handle_message(
                {"out": 0, "from_id": 777, "peer_id": 777, "id": index, "text": text}
            )

        bookings = storage.conn.execute(
            "SELECT date, time, master, service, status FROM bookings"
        ).fetchall()
        leads = storage.conn.execute(
            "SELECT id, name, phone, status, service, date, time FROM leads"
        ).fetchall()

        print("\n--- Итог в базе ---")
        print("Записи:", [dict(row) for row in bookings] or "нет")
        print("Заявки:", [dict(row) for row in leads] or "нет")
        print(f"Вызовов модели: {storage.usage_today()}")
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
