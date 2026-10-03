@echo off
REM Builds a standalone Depot app folder via PyInstaller.
REM Mirrors pos_app\build_exe.bat - see that file's header comment for
REM why this uses `python -m PyInstaller` and pre-checks PySide6.

setlocal
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..

echo Checking build environment...
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: PySide6 is not importable from the active `python`.
    echo Activate the venv you built this project's dependencies into, then run:
    echo     pip install -r requirements.txt
    echo from the repository root, and re-run this script.
    exit /b 1
)

pushd "%REPO_ROOT%"
python -m PyInstaller "depot_app\depot_app.spec" --distpath "depot_app\dist" --workpath "depot_app\build" --noconfirm --clean
set BUILD_RESULT=%ERRORLEVEL%
popd

if %BUILD_RESULT% neq 0 (
    echo.
    echo Build FAILED - see PyInstaller output above.
    exit /b %BUILD_RESULT%
)

echo.
echo Build complete: depot_app\dist\DepotApp\DepotApp.exe
echo Next: compile depot_app\installer\depot_app_installer.iss with Inno Setup
echo to produce the installer in depot_app\installer_output\.
endlocal
