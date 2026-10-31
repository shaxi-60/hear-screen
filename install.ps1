# 将已构建的目录版安装到当前用户，并在桌面/开始菜单创建入口。
# 用法：powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$src = Join-Path $root 'dist_package\听见画面'
if (-not (Test-Path (Join-Path $src '听见画面.exe'))) {
  throw "没有找到构建产物，请先运行 build.ps1"
}

$dest = Join-Path $env:LOCALAPPDATA 'Programs\听见画面'
$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'

Get-Process '听见画面' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

$backup = Join-Path $env:TEMP ('hearscreen_data_backup_' + [guid]::NewGuid().ToString('N'))
if (Test-Path (Join-Path $dest 'data')) {
  Copy-Item (Join-Path $dest 'data') $backup -Recurse -Force
}
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
Copy-Item $src $dest -Recurse -Force
if (Test-Path $backup) {
  if (Test-Path (Join-Path $dest 'data')) { Remove-Item (Join-Path $dest 'data') -Recurse -Force }
  Copy-Item $backup (Join-Path $dest 'data') -Recurse -Force
  Remove-Item $backup -Recurse -Force
}

$exe = Join-Path $dest '听见画面.exe'
$shell = New-Object -ComObject WScript.Shell
$lnkPath = Join-Path $desktop '听见画面.lnk'
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $dest
$lnk.IconLocation = "$exe,0"
$lnk.Description = '听见画面 - 视障人士 AI 视频解说'
$lnk.Save()

$sm = $shell.CreateShortcut((Join-Path $startMenu '听见画面.lnk'))
$sm.TargetPath = $exe
$sm.WorkingDirectory = $dest
$sm.IconLocation = "$exe,0"
$sm.Description = '听见画面 - 视障人士 AI 视频解说'
$sm.Save()

Write-Host "安装完成：$exe"
Write-Host "桌面快捷方式：$lnkPath"
exit 0
