@echo off
REM UNIVERSAL AUTOREG v2 - Windows launcher
set PYTHONPATH=
set PYTHONIOENCODING=utf-8
if not exist data mkdir data
if not exist logs mkdir logs
python dashboard.py
pause
