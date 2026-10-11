@echo off
cd /d "%~dp0.."
venv\Scripts\python.exe scripts\scheduled_status_recalc.py
