# Автозапуск бота ФОКУС при входе в Windows.
# Запускать без прав администратора:  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

function Find-Pythonw {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        $candidate = Join-Path (Split-Path -Parent $py.Source) "pythonw.exe"
        if (Test-Path $candidate) { return $candidate }
    }

    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"
    if (Test-Path $fallback) { return $fallback }

    throw "Не найден pythonw.exe. Установите Python или добавьте его в PATH."
}

if (-not (Test-Path (Join-Path $root "run.py"))) {
    throw "В папке $root нет run.py. Скрипт должен лежать в scripts внутри проекта."
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Warning "Файл .env не найден. Бот не запустится, пока в нём нет ключей."
}

$pythonw = Find-Pythonw
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "FOKUS VK bot.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $root "run.py") + '"'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = "Чат-бот салона оптики ФОКУС (ВКонтакте)"
$shortcut.Save()

if (-not (Test-Path $shortcutPath)) {
    throw "Не удалось создать ярлык: $shortcutPath"
}

Write-Host "Автозапуск включён." -ForegroundColor Green
Write-Host "Ярлык: $shortcutPath"
Write-Host "Программа: $pythonw"
Write-Host "Рабочая папка: $root"
Write-Host ""
Write-Host "Бот будет стартовать при каждом входе в Windows (после входа в систему)."
Write-Host "Запустить прямо сейчас:  Start-Process '$shortcutPath'"
