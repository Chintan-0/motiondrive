@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo MotionDrive Production Build Script
echo ============================================================

cd /d "%~dp0"

echo [1/6] Cleaning previous build outputs...
if exist "build\dist" rmdir /s /q "build\dist"
if exist "dist" rmdir /s /q "dist"

echo [2/6] Running unit tests...
.venv\Scripts\python.exe -m pytest tests -q
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Unit tests failed. Aborting build.
    exit /b 1
)

echo [3/6] Building PyInstaller executable...
powershell -ExecutionPolicy Bypass -File .\build\build.ps1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller build failed. Aborting.
    exit /b 1
)

echo [4/6] Building Inno Setup Installer...
powershell -ExecutionPolicy Bypass -File .\installer\build_installer.ps1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Installer build failed. Aborting.
    exit /b 1
)

echo [5/6] Organizing final output artifacts in dist\...
if not exist "dist" mkdir "dist"
if exist "build\dist\MotionDrive" (
    xcopy /E /I /Y "build\dist\MotionDrive" "dist\MotionDrive\"
)
if exist "installer\MotionDrive-Setup.exe" (
    copy /Y "installer\MotionDrive-Setup.exe" "dist\MotionDrive-Setup.exe"
)
if exist "build\MotionDrive-Portable.zip" (
    copy /Y "build\MotionDrive-Portable.zip" "dist\MotionDrive-Portable.zip"
)

echo ============================================================
echo [SUCCESS] Production Build Complete!
echo Outputs located in dist\:
echo   - dist\MotionDrive\MotionDrive.exe
echo   - dist\MotionDrive-Setup.exe
echo   - dist\MotionDrive-Portable.zip
echo ============================================================
