@echo off
title Cong Cu Tao Telegram String Session
color 0A
echo Dang tien hanh chay script python...
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe generate_session.py
) else (
    python generate_session.py
)
echo.
pause
