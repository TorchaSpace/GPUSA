@echo off
REM Builds a standalone Branch POS app folder via PyInstaller.
REM
REM Always runs from the repository root (so `shared` and `database`
REM are importable per pos_app.spec's pathex) and always drops build
REM output inside THIS app's own folder - pos_app\dist\BranchPOS\ - not
REM the repo root, so admin_app's build never collides with this one.
REM
REM Uses `python -m PyInstaller` rather than the bare `pyinstaller`
REM command: with more than one Python installed, a `pyinstaller` found
REM on PATH can belong to a DIFFERENT interpreter than the one
REM `pip install -r requirements.txt` was run against - PyInstaller then
REM builds "successfully" but leaves PySide6 (or anything else from
REM requirements.txt) out of the bundle, and the .exe only fails once
REM launched, with a "No module named 'PySide6'" bootloader error and no
REM hint of the real cause. `python -m PyInstaller` always uses whichever
REM `python` is active on PATH - the same one pip installed into. The
REM check below fails loudly, before building, if that's not set up.

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
python -m PyInstaller "pos_app\pos_app.spec" --distpath "pos_app\dist" --workpath "pos_app\build" --noconfirm --clean
set BUILD_RESULT=%ERRORLEVEL%
popd

if %BUILD_RESULT% neq 0 (
    echo.
    echo Build FAILED - see PyInstaller output above.
    exit /b %BUILD_RESULT%
)

echo.
echo Build complete: pos_app\dist\BranchPOS\BranchPOS.exe
echo Next: compile pos_app\installer\pos_app_installer.iss with Inno Setup
echo to produce the installer in pos_app\installer_output\.
endlocal
