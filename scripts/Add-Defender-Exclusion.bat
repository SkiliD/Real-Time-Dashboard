@echo off
rem ============================================================
rem  DevDash - exclusion Windows Defender, en un double-clic.
rem  A lancer UNE FOIS en tant qu'administrateur (clic droit ->
rem  "Executer en tant qu'administrateur") si Defender signale l'app
rem  (typique des .exe PyInstaller non signes).
rem  Exclut le dossier du projet et dist\ (build .exe).
rem ============================================================
setlocal
set "ROOT=%~dp0..\"
echo Exclusion Windows Defender pour:
echo   %ROOT%
echo   %ROOT%dist
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-MpPreference -ExclusionPath '%ROOT%'; Add-MpPreference -ExclusionPath '%ROOT%dist'; Write-Output 'OK - exclusions ajoutees. Relance DevDash.' } catch { Write-Output 'ERREUR: relance ce .bat en administrateur (clic droit -> Executer en tant qu''administrateur).' }"
pause
