@echo off
setlocal
title TechRAG
cd /d "%~dp0.." || exit /b 1

set "TECHRAG_URL=http://127.0.0.1:5173/"
curl.exe --noproxy "*" --fail --silent --max-time 2 "%TECHRAG_URL%" 2>nul | findstr.exe /l /c:"<title>Technical RAG</title>" >nul
if not errorlevel 1 goto open

echo Starting TechRAG. Loading the local model may take a few minutes.
".venv\Scripts\python.exe" scripts\local_services.py start
if errorlevel 1 (
    echo TechRAG could not start. See the output above and the logs in data\run.
    pause
    exit /b 1
)

:open
start "" "%TECHRAG_URL%"
