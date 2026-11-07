@echo off
rem Run SubTranslator straight from source (no exe build needed).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m subtitle_translator
