"""Точка входа.

    python run.py            — запуск бота (Long Poll)
    python run.py --check    — проверка настроек, VK и ProxyAPI
    python run.py --init     — создать рабочие файлы в data/
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import (
    CATALOG_PATH,
    DB_PATH,
    KB_PATH,
    LEADS_PATH,
    LOG_DIR,
    MASTERS_PATH,
    PROMPT_PATH,
    SALON_PATH,
    ConfigError,
    Settings,
    ensure_dirs,
)
from bootstrap import init_data
from safety import mask_secrets

log = logging.getLogger("bot")

_MUTEX_NAME = "Local\\FOKUS_VK_BOT_SINGLE_INSTANCE"
_mutex_handle = None


def acquire_single_instance() -> bool:
    """Один бот на сообщество: второй экземпляр создал бы гонку за слоты.

    На Windows используем именованный мьютекс (живёт, пока жив процесс).
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return True  # не смогли создать мьютекс — не мешаем запуску
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:  # noqa: BLE001
        log.warning("Не удалось проверить единственный экземпляр", exc_info=True)
        return True


def _write_startup_error(text: str) -> None:
    """Ошибки старта при запуске через pythonw не видно в консоли — пишем в файл."""
    try:
        ensure_dirs()
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        (LOG_DIR / "startup_error.log").write_text(f"{stamp}\n{text}\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mask_secrets(str(record.msg))
            if record.args:
                # Не-str аргументы (числа, коды ответов) не трогаем: иначе ломается %d.
                record.args = tuple(
                    mask_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        except Exception:  # noqa: BLE001
            pass
        return True


def setup_logging(level: str) -> None:
    ensure_dirs()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(SecretFilter())
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretFilter())
    root.addHandler(file_handler)

    # httpx на INFO пишет каждый HTTP-запрос — в рабочем логе это шум.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def build_app(settings: Settings):
    from dialog import Dialog
    from llm import LLM
    from prompts import build_system_prompt
    from storage import Storage
    from tools import Toolbox
    from vk_client import VK

    storage = Storage(DB_PATH, LEADS_PATH, LEADS_PATH.parent / "outbox.jsonl")
    storage.ensure_workbook()
    vk = VK(settings.vk_token, settings.vk_group_id)
    toolbox = Toolbox(
        settings=settings,
        storage=storage,
        catalog_path=CATALOG_PATH,
        masters_path=MASTERS_PATH,
    )
    llm = LLM(settings, storage)
    dialog = Dialog(
        settings=settings,
        storage=storage,
        vk=vk,
        toolbox=toolbox,
        llm=llm,
        prompt_builder=build_system_prompt,
    )
    return storage, vk, dialog


def _export_worker(storage) -> None:
    while True:
        time.sleep(60)
        try:
            storage.export_leads()
        except Exception as exc:  # noqa: BLE001
            log.warning("Повторная выгрузка не удалась: %s", mask_secrets(str(exc))[:150])


def run_bot(settings: Settings) -> int:
    if not acquire_single_instance():
        log.error(
            "Бот уже запущен другим процессом — второй экземпляр не нужен. "
            "Если это не так, закройте лишний процесс python/pythonw."
        )
        _write_startup_error("Второй экземпляр бота не запущен: первый уже работает.")
        return 3

    storage, vk, dialog = build_app(settings)
    log.info(
        "Бот запущен. Сообщество %s, модель %s, уведомления: %s",
        settings.vk_group_id,
        settings.model,
        settings.admin_vk_id if settings.admin_vk_id else "выключены",
    )

    threading.Thread(target=_export_worker, args=(storage,), daemon=True).start()

    try:
        for update in vk.listen(
            ts_getter=lambda: storage.get_kv("lp_ts"),
            ts_saver=lambda ts: storage.set_kv("lp_ts", ts),
        ):
            try:
                dialog.handle_update(update)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка обработки события")
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    finally:
        vk.close()
        storage.close()
    return 0


def run_checks(settings: Settings) -> int:
    print("1. Конфигурация")
    print(f"   VK_TOKEN:      задан, {len(settings.vk_token)} символов")
    print(f"   VK_GROUP_ID:   {settings.vk_group_id}")
    print(f"   PROXYAPI_KEY:  задан, {len(settings.proxy_key)} символов")
    print(f"   Модель:        {settings.model}")
    print(f"   Адрес API:     {settings.base_url}")

    print("2. Файлы")
    for label, path in [
        ("База знаний", KB_PATH),
        ("Промпт", PROMPT_PATH),
        ("Салон", SALON_PATH),
        ("Мастера", MASTERS_PATH),
        ("Каталог", CATALOG_PATH),
    ]:
        status = "ок" if path.exists() else "НЕ НАЙДЕН"
        print(f"   {label}: {path.name} — {status}")

    print("3. ВКонтакте")
    from vk_client import VK, VKError

    vk = VK(settings.vk_token, settings.vk_group_id)
    try:
        group = vk.call("groups.getById", group_id=settings.vk_group_id)
        if isinstance(group, list):
            info = group[0] if group else {}
        elif isinstance(group, dict):
            info = (group.get("groups") or [group])[0]
        else:
            info = {}
        name = info.get("name", "?") if isinstance(info, dict) else "?"
        print(f"   Сообщество: {name}")
        server = vk.get_long_poll_server()
        print(f"   Long Poll:  подключён ({server['server'][:38]}…)")
    except VKError as exc:
        print(f"   ОШИБКА: {mask_secrets(str(exc))}")
        print("   Проверьте VK_TOKEN, VK_GROUP_ID и включённый Long Poll API.")
        return 2
    finally:
        vk.close()

    print("4. ProxyAPI")
    from openai import OpenAI

    try:
        client = OpenAI(api_key=settings.proxy_key, base_url=settings.base_url, timeout=30.0)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "Ответь одним словом: работает"}],
            max_tokens=10,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip()
        print(f"   Ответ модели: {answer!r}")
        print("   Ключ и баланс в порядке.")
    except Exception as exc:  # noqa: BLE001
        print(f"   ОШИБКА: {type(exc).__name__}: {mask_secrets(str(exc))[:200]}")
        print("   Проверьте PROXYAPI_KEY и баланс в кабинете ProxyAPI.")
        return 3

    print("\nВсё готово. Запуск: python run.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Чат-бот салона оптики ФОКУС")
    parser.add_argument("--check", action="store_true", help="проверить ключи и подключения")
    parser.add_argument("--init", action="store_true", help="создать файлы в data/")
    args = parser.parse_args()

    ensure_dirs()

    if args.init:
        created = init_data()
        if created:
            print("Созданы файлы:")
            for path in created:
                print("  " + path)
        else:
            print("Файлы уже существуют, ничего не менял.")
        return 0

    try:
        settings = Settings.load()
    except ConfigError as exc:
        message = f"Ошибка конфигурации: {exc}"
        print(message, file=sys.stderr)
        print("Подсказка: скопируйте .env.example в .env и заполните ключи.", file=sys.stderr)
        _write_startup_error(message + "\nПодсказка: скопируйте .env.example в .env и заполните ключи.")
        return 1

    setup_logging(settings.log_level)
    init_data()

    if args.check:
        return run_checks(settings)

    return run_bot(settings)


if __name__ == "__main__":
    try:
        exit_code = main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        logging.getLogger("bot").exception("Необработанная ошибка")
        _write_startup_error(f"{type(exc).__name__}: {exc}")
        exit_code = 1
    raise SystemExit(exit_code)
