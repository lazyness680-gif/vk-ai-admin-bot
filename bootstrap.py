"""Создание рабочих файлов: salon.json, masters.json, catalog.xlsx, leads.xlsx."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from config import CATALOG_PATH, DATA_DIR, LEADS_PATH, MASTERS_PATH, SALON_PATH, DB_PATH
from schedule import _DEFAULT_MASTERS
from storage import Storage

SALON_TEMPLATE = {
    "name": "ФОКУС",
    "city": "ЗАПОЛНИТЕ",
    "address": "ЗАПОЛНИТЕ",
    "phone": "ЗАПОЛНИТЕ",
    "site": "ЗАПОЛНИТЕ",
    "instagram": "ЗАПОЛНИТЕ",
    "timezone": "Europe/Moscow",
    "hours_weekday": "09:00–20:00",
    "hours_sunday": "10:00–18:00",
    "lunch": "уточняется у мастера",
    "payment": "наличные и карта",
    "delivery": "по России 5–7 рабочих дней, самовывоз бесплатно",
    "admin_response_time": "15 минут",
    "catalog_url": "ЗАПОЛНИТЕ",
}

CATALOG_HEADERS = ["Модель", "Материал", "Цвет", "Цена", "Наличие", "Фото"]


def _write_json(path: Path, data: dict) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def init_data() -> list[str]:
    created: list[str] = []
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if _write_json(SALON_PATH, SALON_TEMPLATE):
        created.append(str(SALON_PATH))
    if _write_json(MASTERS_PATH, _DEFAULT_MASTERS):
        created.append(str(MASTERS_PATH))

    if not CATALOG_PATH.exists():
        wb = Workbook()
        ws = wb.active
        ws.title = "Каталог"
        ws.append(CATALOG_HEADERS)
        for idx, width in enumerate([28, 18, 14, 12, 12, 45], start=1):
            cell = ws.cell(row=1, column=idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            ws.column_dimensions[cell.column_letter].width = width
        ws.freeze_panes = "A2"
        wb.save(CATALOG_PATH)
        created.append(str(CATALOG_PATH))

    storage = Storage(DB_PATH, LEADS_PATH, DATA_DIR / "outbox.jsonl")
    try:
        storage.ensure_workbook()
        created.append(str(LEADS_PATH))
    finally:
        storage.close()

    return created
