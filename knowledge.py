"""Загрузка базы знаний из Excel в текст для системного промпта."""

from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import load_workbook

log = logging.getLogger(__name__)

_SKIP_SHEETS = set()


def load_knowledge(path: Path) -> str:
    """Читает все листы xlsx и превращает их в компактный markdown-текст.

    Кэш по mtime: файл перечитывается только при изменении.
    """
    path = Path(path)
    if not path.exists():
        log.warning("База знаний не найдена: %s", path)
        return ""

    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось открыть базу знаний: %s", exc)
        return ""

    blocks: list[str] = []
    try:
        for sheet in wb.worksheets:
            if sheet.title in _SKIP_SHEETS:
                continue
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c).strip() for c in rows[0] if c is not None and str(c).strip()]
            lines: list[str] = [f"### {sheet.title}"]
            if header:
                lines.append("Колонки: " + " | ".join(header))
            for row in rows[1:]:
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append("- " + " | ".join(cells))
            if len(lines) > 1:
                blocks.append("\n".join(lines))
    finally:
        wb.close()

    return "\n\n".join(blocks)
