<#
DefiantsOneClick.ps1 — All-in-one orchestrator
Builds OneDriveMigrateCLI.exe (PyInstaller) with version info + icon,
optionally UPX-compresses, optionally signs, optionally pushes to remote,
and can run the CLI.
#>
param(
  [string]$ClientId = "",
  [ValidateSet('plan','copy','move')] [string]$Mode = 'plan',
  [string]$SourceRoot = "",
  [string]$TargetRoot = "",
  [switch]$SkipIdenticals,
  [string]$Exts = "",
  [string]$ModifiedAfter = "",
  [double]$MinMB,
  [double]$MaxMB,
  [string]$ResumeCache = "",
  [string]$RemoteRepo = "",
  [string]$UpxDir = "",
  [string]$SignPfx = "",
  [string]$SignPwd = "",
  [string]$TimestampUrl = "http://timestamp.digicert.com",
  [switch]$BuildOnly
)
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
if ($UpxDir) { $args += @('--upx-dir', $UpxDir) }
& "$VENV/Scripts/pyinstaller.exe" @args
if (-not (Test-Path "dist/$APP.exe")) { throw 'Build failed' }
if (Get-Command signtool.exe -ErrorAction SilentlyContinue) {
  if ($SignPfx) {
    if (-not $TimestampUrl) { $TimestampUrl='http://timestamp.digicert.com' }
    if ($SignPwd) {
      & signtool.exe sign /fd SHA256 /tr $TimestampUrl /td SHA256 /f $SignPfx /p $SignPwd "dist/$APP.exe"
    } else {
      & signtool.exe sign /fd SHA256 /tr $TimestampUrl /td SHA256 /f $SignPfx "dist/$APP.exe"
    }
    & signtool.exe verify /v /pa "dist/$APP.exe" | Out-Null
  }
}
if ($RemoteRepo) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Warning 'git not found'; } else {
    git init; git add $MAIN $VER assets/Defiants.ico build_windows.bat build_windows.ps1 DefiantsOneClick.ps1 README_onedrive_migrate_cli.md .gitignore
    git commit -m "Defiants: initial commit"
    git branch -M main
    git remote add origin $RemoteRepo
    git push -u origin main
  }
}
if ($BuildOnly) { Write-Host '[done] build-only'; return }
if (-not $ClientId) { throw 'ClientId is required to run the CLI' }
$exe = Resolve-Path "dist/$APP.exe"
$args2 = @($Mode,'--client-id',$ClientId)
if ($SourceRoot) { $args2 += @('--source-root',$SourceRoot) }
if ($TargetRoot) { $args2 += @('--target-root',$TargetRoot) }
if ($SkipIdenticals) { $args2 += @('--skip-identicals') }
if ($Exts) { $args2 += @('--exts',$Exts) }
if ($ModifiedAfter) { $args2 += @('--modified-after',$ModifiedAfter) }
if ($MinMB) { $args2 += @('--min-mb',$MinMB) }
if ($MaxMB) { $args2 += @('--max-mb',$MaxMB) }
if ($ResumeCache) { $args2 += @('--resume-cache',$ResumeCache) }
Write-Host "[run]" $exe $args2
& $exe @args2
