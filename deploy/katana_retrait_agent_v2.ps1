# Retrait de l'ancien agent V2 du Katana (docs/v3/reference_unique.md), sur l'accord du propriétaire du 4 novembre 2026.
# Ne touche ni à la transcription (orion-stt, tâche « ORION Transcription », jetons), ni à Ollama, ni au visage.
#   powershell -ExecutionPolicy Bypass -File katana_retrait_agent_v2.ps1           retrait (sauvegarde d'abord)
#   powershell -ExecutionPolicy Bypass -File katana_retrait_agent_v2.ps1 -Retour   remet le raccourci et relance l'agent
# Rien n'est effacé : le dossier C:\Users\theju\jarvis (code, venv, .env) reste en place ; la sauvegarde garde la
# configuration, le jeton et le raccourci de démarrage.
param([switch]$Retour)
$ErrorActionPreference = "Stop"
$Jarvis = "C:\Users\theju\jarvis"
$Backup = "C:\Users\theju\orion-retrait-agent-v2"
$Startup = [Environment]::GetFolderPath("Startup")
$Link = Join-Path $Startup "JARVIS Agent.lnk"

function Get-AgentV2 {
    # seulement les interpréteurs Python de l'agent (jamais une console qui contiendrait ces mots)
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$Jarvis\.venv*" -and $_.CommandLine -like "*winagent*" }
}

if ($Retour) {
    Copy-Item (Join-Path $Backup "JARVIS Agent.lnk") $Link
    Start-Process explorer.exe -ArgumentList ('"{0}"' -f $Link)
    "Agent V2 remis en place."
    return
}

New-Item -ItemType Directory -Force $Backup | Out-Null
foreach ($name in "windows_agent.toml", "windows_agent.local.toml", ".env") {
    if (Test-Path (Join-Path $Jarvis $name)) { Copy-Item (Join-Path $Jarvis $name) (Join-Path $Backup $name) -Force }
}
if (Test-Path $Link) { Copy-Item $Link (Join-Path $Backup "JARVIS Agent.lnk") -Force }
(Get-AgentV2 | ForEach-Object { "{0} {1}" -f $_.ProcessId, $_.CommandLine }) | Set-Content (Join-Path $Backup "processus.txt")
Set-Content (Join-Path $Backup "LISEZMOI.txt") -Encoding UTF8 -Value @(
    "Ancien agent ORION V2 du Katana, retiré le $(Get-Date -Format 'yyyy-MM-dd HH:mm').",
    "Code et venv : $Jarvis (intacts). Retour : powershell -ExecutionPolicy Bypass -File katana_retrait_agent_v2.ps1 -Retour")
Copy-Item $PSCommandPath (Join-Path $Backup "katana_retrait_agent_v2.ps1") -Force -ErrorAction SilentlyContinue

Get-AgentV2 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Test-Path $Link) { Remove-Item $Link }   # copie gardée dans la sauvegarde
Start-Sleep -Seconds 2
$left = @(Get-AgentV2).Count
$port = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue).Count
"Sauvegarde : $Backup ; agent V2 restant : $left ; port 8765 à l'écoute : $port"
