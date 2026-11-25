@echo off
rem ============================================================
rem  DevDash - installation en un double-clic, aucune commande.
rem  Cree un raccourci "DevDash" sur le Bureau (icone de l'app)
rem  pointant vers DevDash.bat dans ce dossier.
rem  (.bat/.lnk ne sont pas concernes par le blocage AppLocker des .exe non signes.)
rem ============================================================
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\DevDash.lnk'); $lnk.TargetPath = '%ROOT%DevDash.bat'; $lnk.WorkingDirectory = '%ROOT%'; $lnk.IconLocation = '%ROOT%devdash\gui\icon.ico'; $lnk.WindowStyle = 1; $lnk.Description = 'DevDash - tableau de bord dev local'; $lnk.Save(); Write-Output 'Raccourci DevDash cree sur le Bureau - double-clique dessus pour lancer.'"
