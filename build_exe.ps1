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
& ".\.venv\Scripts\pyinstaller.exe" --onefile --noconfirm --clean `
  --name AIClyveIDELite `
  --add-data "web;web" `
  --add-data "brand.json;." `
  --collect-submodules clyve_ide `
  --exclude-module tkinter `
  app.py

Write-Host ""
if (Test-Path "dist\AIClyveIDELite.exe") {
  $mb = [math]::Round((Get-Item "dist\AIClyveIDELite.exe").Length / 1MB, 1)
  Write-Host "Selesai -> dist\AIClyveIDELite.exe ($mb MB)" -ForegroundColor Green
  Write-Host "Jalankan: .\dist\AIClyveIDELite.exe" -ForegroundColor Green
  Write-Host "Kalau Defender nge-flag: false positive PyInstaller onefile (unsigned)." -ForegroundColor Yellow
} else {
  Write-Host "Build gagal: dist\AIClyveIDELite.exe tidak ada. Cek error di atas." -ForegroundColor Red
  exit 1
}
