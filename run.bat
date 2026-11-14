@echo off
REM AI Clyve IDE - Windows launcher.
REM Pakai Python dari PATH, py launcher, atau Python bawaan Laragon.
cd /d "%~dp0"
set PYEXE=

if exist "C:\laragon\bin\python\python.exe" set PYEXE="C:\laragon\bin\python\python.exe"
for /d %%D in ("C:\laragon\bin\python\python-*") do if exist "%%D\python.exe" set PYEXE="%%D\python.exe"
if not defined PYEXE (where py >nul 2>nul && set PYEXE=py -3)
if not defined PYEXE (where python >nul 2>nul && set PYEXE=python)

if not defined PYEXE (
  echo Python tidak ketemu. Install dari python.org, atau pakai Python bawaan Laragon.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [setup] membuat virtualenv...
  %PYEXE% -m venv .venv
)

REM UI desktop butuh PySide6. Kalau gagal install, app jatuh ke UI web.
if not exist ".venv\.qt-installed" (
  ".venv\Scripts\python.exe" -m pip install --quiet PySide6 pypdf >nul 2>&1 && (
    echo. > ".venv\.qt-installed"
  )
)

".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
