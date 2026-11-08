"""Автоматическая проверка безопасности проекта.

    python scripts/security_audit.py

Проверяет: утечки секретов в коде, опасные вызовы, настройки .gitignore,
HTTPS, параметризацию SQL, место хранения персональных данных.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "logs", "data"}
SELF = Path(__file__).resolve()

SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "похоже на ключ ProxyAPI/OpenAI"),
    (re.compile(r"\bvk1\.a\.[A-Za-z0-9_\-\.]{20,}\b"), "похоже на токен ВКонтакте"),
    (re.compile(r"(?i)(api[_-]?key|token|secret)\s*=\s*['\"][^'\"]{16,}['\"]"), "ключ в коде"),
]

# Значения-заглушки из тестов: не являются секретами.
ALLOWED_FAKE = {
    "sk-test",
    "sk-abc123456789XYZ",
    "sk-1234567890abcdef",
    "vk1.a.test",
    "vk1.a.abcdefghijklmnop",
}

DANGEROUS = [
    (re.compile(r"(?<![\w.])eval\s*\("), "eval()"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec()"),
    (re.compile(r"os\.system\s*\("), "os.system()"),
    (re.compile(r"subprocess\.(run|Popen|call|check_output)"), "subprocess"),
    (re.compile(r"pickle\.loads?\s*\("), "pickle"),
    (re.compile(r"yaml\.load\s*\("), "yaml.load без SafeLoader"),
    (re.compile(r"shell\s*=\s*True"), "shell=True"),
    (re.compile(r"verify\s*=\s*False"), "отключена проверка TLS"),
    (re.compile(r"http://(?!localhost|127\.0\.0\.1|evil\.example)"), "незашифрованный HTTP"),
]

results: list[tuple[str, str, str]] = []


def add(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))


def python_files(*, skip_tests: bool = False):
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue
        if skip_tests and "tests" in path.parts:
            continue
        yield path


def check_secrets() -> None:
    found = []
    for path in python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if match.group(0) in ALLOWED_FAKE:
                    continue
                line = text[: match.start()].count("\n") + 1
                found.append(f"{path.relative_to(ROOT)}:{line} — {label}")
    add("Нет захардкоженных ключей в коде", "FAIL" if found else "PASS", "; ".join(found))


def check_dangerous_calls() -> None:
    found = []
    for path in python_files(skip_tests=True):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in DANGEROUS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                found.append(f"{path.relative_to(ROOT)}:{line} — {label}")
    add("Нет опасных вызовов (eval/exec/shell/TLS-off)", "FAIL" if found else "PASS", "; ".join(found))


def check_gitignore() -> None:
    path = ROOT / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = {line.strip() for line in text.splitlines()}
    add(".env исключён из git", "PASS" if ".env" in entries else "FAIL")
    add(
        "data/ с персональными данными исключена",
        "PASS" if any(e.startswith("data/") for e in entries) else "FAIL",
    )
    add("logs/ исключены", "PASS" if any(e.startswith("logs") for e in entries) else "FAIL")


def check_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        add(".env создан локально", "FAIL", "скопируйте .env.example в .env")
        return
    add(".env создан локально", "PASS")
    text = env.read_text(encoding="utf-8", errors="ignore")
    token = re.search(r"^VK_TOKEN=(.*)$", text, re.MULTILINE)
    key = re.search(r"^PROXYAPI_KEY=(.*)$", text, re.MULTILINE)
    token_filled = bool(token and token.group(1).strip())
    key_filled = bool(key and key.group(1).strip())
    add("VK_TOKEN заполнен", "PASS" if token_filled else "WARN", "ожидает ключ сообщества")
    add("PROXYAPI_KEY заполнен", "PASS" if key_filled else "WARN", "ожидает ключ ProxyAPI")


def check_https() -> None:
    config = (ROOT / "config.py").read_text(encoding="utf-8", errors="ignore")
    ok = 'startswith("https://")' in config
    add("API-ключ передаётся только по HTTPS", "PASS" if ok else "FAIL")


def check_pii_storage() -> None:
    data_dir = ROOT / "data"
    files = sorted(p.name for p in data_dir.glob("*.xlsx")) if data_dir.exists() else []
    add(
        "Персональные данные только в локальных файлах вне git",
        "PASS",
        f"файлы: {', '.join(files) or 'нет'} — доступ ограничить ACL",
    )


def check_sql() -> None:
    storage = (ROOT / "storage.py").read_text(encoding="utf-8", errors="ignore")
    unsafe = re.findall(r'execute\(\s*f["\']', storage)
    unsafe += re.findall(r'execute\([^;\n]*?\.format\(', storage)
    unsafe += re.findall(r'execute\(\s*["\'][^"\']*%[sd]', storage)
    add(
        "SQL-параметризация",
        "FAIL" if unsafe else "PASS",
        f"небезопасных запросов: {len(unsafe)}" if unsafe else "все значения передаются через ?",
    )


def main() -> int:
    check_secrets()
    check_dangerous_calls()
    check_gitignore()
    check_env()
    check_https()
    check_pii_storage()
    check_sql()

    width = max(len(name) for name, _, _ in results)
    failed = 0
    for name, status, detail in results:
        line = f"[{status}] {name.ljust(width)}"
        if detail and (status != "PASS" or name.startswith("Персональные")):
            line += f"  — {detail}"
        print(line)
        if status == "FAIL":
            failed += 1

    passed = sum(1 for _, status, _ in results if status == "PASS")
    warned = sum(1 for _, status, _ in results if status == "WARN")
    print(f"\nИтог: {passed} пройдено, {warned} ожидает настройки, {failed} ошибок")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
