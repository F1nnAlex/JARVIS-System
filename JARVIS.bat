@echo off
rem Double-click to start JARVIS on Windows (first run installs everything).
cd /d "%~dp0"
if not exist .venv (
    echo Setting up JARVIS for the first time...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m jarvis
