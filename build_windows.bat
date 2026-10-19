@echo off
setlocal
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm --windowed --name "Klinik-Dienstplan" --onedir main.py
if errorlevel 1 exit /b 1
if not exist "dist\Klinik-Dienstplan\data" mkdir "dist\Klinik-Dienstplan\data"
echo Fertig. Kopiere den Ordner dist\Klinik-Dienstplan auf den USB-Stick.
pause
