@echo off
cd /d "%~dp0"
python -X utf8 tools\dashboard.py --root . --serve
if errorlevel 1 pause
