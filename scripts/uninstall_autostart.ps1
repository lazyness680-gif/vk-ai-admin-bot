# Убрать бота из автозапуска.
# powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1

$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "FOKUS VK bot.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Автозапуск отключён: $shortcutPath" -ForegroundColor Green
} else {
    Write-Host "Ярлык автозапуска не найден. Возможно, он уже удалён."
}

Write-Host "Уже запущенный бот продолжит работать. Остановить: scripts\stop_bot.bat"
