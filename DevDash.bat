@echo off
rem ============================================================
rem  DevDash launcher - double-clic, aucune commande a taper.
rem  Prefers the repo virtualenv, falls back to the system "py -3".
rem  Extra args are forwarded:  DevDash.bat --browser
rem ============================================================
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m devdash gui %*
) else (
  py -3 -m devdash gui %*
)
