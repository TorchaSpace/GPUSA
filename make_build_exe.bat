@echo off
REM Compiles build_setup.py itself into build.exe, so future builds are
REM just double-clicking build.exe instead of typing `python build_setup.py`.
REM
REM Run this ONCE, and again any time build_setup.py changes - build.exe
REM is generated output, not something to hand-edit or expect to stay in
REM sync with build_setup.py on its own.
REM
REM build.exe is a CONVENIENCE WRAPPER ONLY, not a standalone tool: it
REM still has to be run from inside this same repo checkout, with this
REM same venv active, because build_setup.py shells out to `python -m
REM PyInstaller` at runtime to compile admin_app/main.py etc. - see
REM build_setup.py's own module docstring and its _pyinstaller_invocation()
REM for exactly how it locates that `python`. (Its OUTPUT, setup.exe, is
REM the one that's fully standalone - see installer.py's docstring.)
REM
REM Always run from the repository root (so `import shared` resolves) and
REM always builds build.exe directly INTO the repo root (--distpath .) -
REM build.exe's own frozen-aware REPO_ROOT resolution (see build_setup.py)
REM assumes it's sitting right next to admin_app/, pos_app/, depot_app/,
REM shared/, database/, and installer.py, not off in some dist/ subfolder.
REM
REM Uses `python -m PyInstaller` rather than the bare `pyinstaller`
REM command for the same reason every other build script here does - see
REM pos_app/build_exe.bat's header comment for the full story.

setlocal
set SCRIPT_DIR=%~dp0

echo Checking build environment...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller is not importable from the active `python`.
    echo Activate the venv you built this project's dependencies into, then run:
    echo     pip install -r requirements.txt
    echo from the repository root, and re-run this script.
    exit /b 1
)

pushd "%SCRIPT_DIR%"
python -m PyInstaller "build_setup.py" --onefile --name build --distpath "." --workpath "_build_tool_build" --specpath "_build_tool_build" --noconfirm --clean
set BUILD_RESULT=%ERRORLEVEL%
popd

if %BUILD_RESULT% neq 0 (
    echo.
    echo Build FAILED - see PyInstaller output above.
    exit /b %BUILD_RESULT%
)

rmdir /s /q "%SCRIPT_DIR%_build_tool_build" 2>nul

echo.
echo Build complete: build.exe (repo root)
echo From now on, double-click build.exe instead of running
echo `python build_setup.py` - they do exactly the same thing. Re-run this
echo script any time build_setup.py itself changes.
endlocal
