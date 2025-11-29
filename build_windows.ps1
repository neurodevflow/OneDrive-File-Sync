# Windows build (signed + icon + version info + optional UPX)
param()
$ErrorActionPreference='Stop'
$APP='OneDriveMigrateCLI'
$VENV='.venv'
$MAIN='onedrive_migrate_cli.py'
$VER='file_version_info.txt'
$ICON='assets/Defiants.ico'
py -3 -m venv $VENV
& "$VENV/Scripts/python.exe" -m pip install --upgrade pip
& "$VENV/Scripts/pip.exe" install msal requests pyinstaller
Remove-Item build -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item dist -Recurse -Force -ErrorAction SilentlyContinue
$args = @('-F',$MAIN,'--name',$APP,'--clean','--console','--version-file',$VER,'--icon',$ICON)
if ($env:UPX_DIR) { $args += @('--upx-dir', $env:UPX_DIR) }
& "$VENV/Scripts/pyinstaller.exe" @args
if (-not (Test-Path "dist/$APP.exe")) { throw 'Build failed' }
if (Get-Command signtool.exe -ErrorAction SilentlyContinue) {
  if ($env:SIGN_PFX) {
    $ts = $env:TIMESTAMP_URL; if (-not $ts) { $ts='http://timestamp.digicert.com' }
    if ($env:SIGN_PWD) {
      & signtool.exe sign /fd SHA256 /tr $ts /td SHA256 /f $env:SIGN_PFX /p $env:SIGN_PWD "dist/$APP.exe"
    } else {
      & signtool.exe sign /fd SHA256 /tr $ts /td SHA256 /f $env:SIGN_PFX "dist/$APP.exe"
    }
  }
}
Write-Host "[OK] Built dist/$APP.exe"
