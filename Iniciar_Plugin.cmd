@echo off
cd /d "%~dp0"
if exist "dist\EMBoxVozAfectiva.exe" (
    start "" "dist\EMBoxVozAfectiva.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "iniciar.py"
    exit /b
)
echo Ejecute Preparar_Entorno.cmd para instalar las dependencias.
pause
