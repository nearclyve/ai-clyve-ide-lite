# Build AIClyveIDELite.exe -- run this ON Windows
# Works with a system Python, the py launcher, or the Python bundled inside Laragon.
$ErrorActionPreference = "Stop"

function Test-Python($exe) {
  if (-not $exe) { return $false }
  if (-not (Test-Path $exe)) { return $false }
  # the Microsoft Store stub lives in WindowsApps and just opens the Store
  if ($exe -like "*WindowsApps*") { return $false }
  try {
    $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    return ($v -match '^\d+\.\d+$')
  } catch { return $false }
}

function Find-Python {
  $candidates = @()

  # 1. Laragon (python-3.x under bin\python, or a directly dropped python.exe)
  foreach ($root in @("C:\laragon\bin\python", "D:\laragon\bin\python", "C:\laragon\bin\php\..\python")) {
    if (Test-Path $root) {
      $candidates += (Get-ChildItem "$root\*\python.exe" -ErrorAction SilentlyContinue |
                      Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
      $candidates += "$root\python.exe"
    }
  }

  # 2. py launcher
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
      $exe = & py -3 -c "import sys;print(sys.executable)" 2>$null
      if ($exe) { $candidates += $exe.Trim() }
    } catch { }
  }

  # 3. python on PATH (Test-Python filters out the Store stub)
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $candidates += $cmd.Source }

  foreach ($c in $candidates) { if (Test-Python $c) { return $c } }
  return $null
}

Write-Host "== AI Clyve IDE build ==" -ForegroundColor Cyan

$py = Find-Python
if (-not $py) {
  Write-Host ""
  Write-Host "Python 3.10+ tidak ketemu." -ForegroundColor Red
  Write-Host "Kalau pakai Laragon, cari path-nya dulu:" -ForegroundColor Yellow
  Write-Host '  dir C:\laragon\bin\python' -ForegroundColor Yellow
  Write-Host "Lalu jalankan manual (ganti path sesuai versi):" -ForegroundColor Yellow
  Write-Host '  C:\laragon\bin\python\python-3.11\python.exe -m venv .venv' -ForegroundColor Yellow
  Write-Host '  .\.venv\Scripts\python.exe -m pip install pyinstaller' -ForegroundColor Yellow
  Write-Host '  .\.venv\Scripts\pyinstaller.exe --onefile --noconfirm --clean --name AIClyveIDELite --add-data "web;web" app.py' -ForegroundColor Yellow
  exit 1
}
Write-Host "Python  : $py" -ForegroundColor Green

# rebuild the venv when it exists but has no interpreter
if ((Test-Path ".venv") -and -not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "venv rusak, dibuat ulang..." -ForegroundColor Yellow
  Remove-Item ".venv" -Recurse -Force
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Membuat venv..." -ForegroundColor Cyan
  & $py -m venv .venv
  if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Gagal membuat venv. Coba: & '$py' -m ensurepip" -ForegroundColor Red
    exit 1
  }
}

$vpy = ".\.venv\Scripts\python.exe"
Write-Host "Install PyInstaller + PySide6..." -ForegroundColor Cyan
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install pyinstaller PySide6 pypdf --quiet

Write-Host "Membungkus app..." -ForegroundColor Cyan
if (-not (Test-Path "brand.json")) { Copy-Item "brand.example.json" "brand.json" }
& $vpy scripts/make_version_info.py
& ".\.venv\Scripts\pyinstaller.exe" --onedir --windowed --noconfirm --clean `
  --name AIClyveIDELite `
  --add-data "web;web" `
  --add-data "brand.json;." `
  --version-file "platform_win\version_info.txt" `
  --collect-submodules clyve_ide `
  --exclude-module tkinter `
  app.py

$appDir = "dist\AIClyveIDELite"
if (-not (Test-Path "$appDir\AIClyveIDELite.exe")) {
  Write-Host "Build gagal: $appDir\AIClyveIDELite.exe tidak ada. Cek error di atas." -ForegroundColor Red
  exit 1
}

$version = & $vpy -c "from clyve_ide.version import __version__; print(__version__)"
Write-Host ""
Write-Host "App  -> $appDir\AIClyveIDELite.exe (tanpa window console)" -ForegroundColor Green

# Installer: cari ISCC (Inno Setup 6). Kalau tidak ada, app tetap bisa dipakai portable.
$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  Write-Host "Membuat installer..." -ForegroundColor Cyan
  & $iscc "/DAppVersion=$version" "installer\AIClyveIDELite.iss"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Installer gagal. App portable tetap ada di $appDir" -ForegroundColor Red
    exit 1
  }
  Write-Host "Installer -> dist\AIClyveIDELite-Setup-$version.exe" -ForegroundColor Green
} else {
  Write-Host "Inno Setup tidak terpasang - installer dilewati." -ForegroundColor Yellow
  Write-Host "Install dulu: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
  Write-Host "Lalu jalankan ulang script ini." -ForegroundColor Yellow
}
