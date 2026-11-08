@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю InderTube на http://127.0.0.1:5000
python app.py
pause
