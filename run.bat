@echo off
title Majelisku Social Assist
cd /d "%~dp0"

echo ==============================================
echo Menjalankan Majelisku Social Assist Dashboard
echo ==============================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m streamlit run dashboard.py
) else (
    python -m streamlit run dashboard.py
)

pause
