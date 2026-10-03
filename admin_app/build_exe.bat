@echo off
REM Builds a standalone Admin Dashboard app folder via PyInstaller.
REM Mirrors pos_app\build_exe.bat - see that file for the rationale.

setlocal
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..

pushd "%REPO_ROOT%"
pyinstaller "admin_app\admin_app.spec" --distpath "admin_app\dist" --workpath "admin_app\build" --noconfirm
set BUILD_RESULT=%ERRORLEVEL%
popd

if %BUILD_RESULT% neq 0 (
    echo.
    echo Build FAILED - see PyInstaller output above.
    exit /b %BUILD_RESULT%
)

echo.
echo Build complete: admin_app\dist\AdminDashboard\AdminDashboard.exe
echo Next: compile admin_app\installer\admin_app_installer.iss with Inno Setup
echo to produce the installer in admin_app\installer_output\.
endlocal
