# 构建目录版 Windows 程序。
# 用法：powershell -ExecutionPolicy Bypass -File .\build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

python build_tools\make_icon.py

$args = @(
  '--noconfirm',
  '--clean',
  '--onedir',
  '--windowed',
  '--name', 'HearScreen',
  '--icon', "$root\assets\icon.ico",
  '--manifest', "$root\build_tools\app.manifest",
  '--version-file', "$root\build_tools\version_info.txt",
  '--add-data', "$root\assets;assets",
  '--hidden-import', 'win32com.client',
  '--hidden-import', 'pythoncom',
  '--hidden-import', 'pywintypes',
  '--distpath', "$root\dist_package",
  '--workpath', "$root\build_package",
  '--specpath', "$root\build_package",
  "$root\run.py"
)

New-Item -ItemType Directory -Force "$root\build_package" | Out-Null
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
python -m PyInstaller @args *> "$root\build_package\pyinstaller.log"
$buildCode = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
if ($buildCode -ne 0) {
  Get-Content "$root\build_package\pyinstaller.log" -Tail 40
  throw "PyInstaller 构建失败，退出码 $buildCode"
}

$src = "$root\dist_package\HearScreen"
$dst = "$root\dist_package\听见画面"
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Rename-Item $src $dst
Rename-Item "$dst\HearScreen.exe" '听见画面.exe'
Copy-Item "$root\README.md" "$dst\使用说明.md" -Force

$size = [math]::Round((Get-ChildItem $dst -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Write-Host "构建完成：$dst\听见画面.exe（约 $size MB）"
Write-Host "如需安装到本机并创建桌面启动器，请运行 install.ps1"
exit 0
