@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name EMBoxVozAfectiva_0_1_1 --add-data "data;data" iniciar.py
if errorlevel 1 exit /b 1
echo Ejecutable generado en dist\EMBoxVozAfectiva_0_1_1.exe
pause
