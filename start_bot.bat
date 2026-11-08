@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FOKUS VK bot
python run.py
if errorlevel 1 pause
