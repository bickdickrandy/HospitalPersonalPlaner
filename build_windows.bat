@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Tests ausfuehren ...
py -m unittest discover -s tests
if errorlevel 1 (
    echo Tests fehlgeschlagen - Build abgebrochen.
    pause
    exit /b 1
)

echo [2/3] Build-Werkzeug installieren ...
py -m pip install --upgrade -r requirements-build.txt
if errorlevel 1 exit /b 1

echo [3/3] Windows-App bauen ...
py -m PyInstaller --noconfirm --windowed --name "Klinik-Dienstplan" --onedir ^
    --hidden-import planner_core --hidden-import planner_theme --hidden-import planner_ui ^
    main.py
if errorlevel 1 exit /b 1

if not exist "dist\Klinik-Dienstplan\data" mkdir "dist\Klinik-Dienstplan\data"
echo.
echo Fertig. Kopiere den Ordner dist\Klinik-Dienstplan auf den USB-Stick.
echo Hinweis: Eine vorhandene Datenbank wird NICHT mitkopiert oder ueberschrieben.
echo Um bestehende Daten zu uebernehmen, data\dienstplan.db manuell in dist\Klinik-Dienstplan\data kopieren.
pause
