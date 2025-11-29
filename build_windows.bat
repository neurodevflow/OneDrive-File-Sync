@echo off
setlocal ENABLEDELAYEDEXPANSION
set APP_NAME=OneDriveMigrateCLI
set VENV_DIR=.venv
set MAIN_PY=onedrive_migrate_cli.py
set VERSION_FILE=file_version_info.txt
set ICON=assets\Defiants.ico
py -3 -m venv %VENV_DIR%
call %VENV_DIR%\Scripts\python -m pip install --upgrade pip
call %VENV_DIR%\Scripts\pip install msal requests pyinstaller
if exist build rmdir /S /Q build
if exist dist rmdir /S /Q dist
set PYI_ARGS=-F %MAIN_PY% --name %APP_NAME% --clean --console --version-file %VERSION_FILE% --icon %ICON%
if defined UPX_DIR set PYI_ARGS=%PYI_ARGS% --upx-dir "%UPX_DIR%"
call %VENV_DIR%\Scripts\pyinstaller %PYI_ARGS%
if not exist dist\%APP_NAME%.exe (
  echo [ERROR] Build failed.
  exit /b 1
)
where signtool >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  if defined SIGN_PFX (
    if not defined TIMESTAMP_URL set TIMESTAMP_URL=http://timestamp.digicert.com
    if defined SIGN_PWD (
      signtool sign /fd SHA256 /tr %TIMESTAMP_URL% /td SHA256 /f "%SIGN_PFX%" /p "%SIGN_PWD%" dist\%APP_NAME%.exe
    ) else (
      signtool sign /fd SHA256 /tr %TIMESTAMP_URL% /td SHA256 /f "%SIGN_PFX%" dist\%APP_NAME%.exe
    )
  )
)
echo [OK] Built dist\%APP_NAME%.exe
