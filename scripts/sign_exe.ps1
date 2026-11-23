# Optional: code-sign the built devdash.exe with signtool (Windows SDK).
#
# WHY: an unsigned exe triggers Windows SmartScreen ("Windows protected your PC").
# A *trusted* certificate (e.g. a code-signing cert you buy) removes the warning
# for everyone. A self-signed cert does NOT remove SmartScreen warnings.
#
# USAGE (from the repo root, after building):
#   .\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe"                 # auto-pick cert from your store
#   .\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe" -Subject "CN=Your Name"
#   .\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe" -PfxPath "cert.pfx" -PfxPassword (ConvertTo-SecureString "pass" -AsPlainText -Force)
#
# signtool ships with the Windows SDK; this script locates it automatically.

param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$Subject,
    [string]$PfxPath,
    [SecureString]$PfxPassword
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ExePath)) { throw "exe not found: $ExePath (build first: py -3 scripts/build_exe.py)" }

# locate signtool.exe in the Windows Kits
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $signtool) { throw "signtool.exe not found — install the Windows SDK, or sign from a Developer PowerShell" }

$args = @("sign", "/fd", "SHA256", "/td", "SHA256",
          "/tr", "http://timestamp.digicert.com", "/v", $ExePath)
if ($PfxPath) {
    $args += @("/f", $PfxPath)
    if ($PfxPassword) { $args += @("/p", (New-Object System.Net.NetworkCredential('', $PfxPassword)).Password) }
} elseif ($Subject) {
    $args += @("/n", $Subject)
} else {
    $args += @("/a")  # best cert from the local store
}

& $signtool @args
if ($LASTEXITCODE -eq 0) { Write-Host "Signed: $ExePath" } else { throw "signtool failed (exit $LASTEXITCODE)" }
