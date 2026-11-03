# Agent Windows V3 (Discord, Frag Factory) : lancé par son superviseur (une seule instance, relance automatique),
# sans fenêtre, depuis la copie V3 du dépôt. Ne touche jamais à l'agent de production (port 8765).
#   deploy\v3_agent.ps1          (re)lance
#   deploy\v3_agent.ps1 -Stop    arrête le superviseur et l'agent
param([switch]$Stop)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "ORION V3 Agent"
$Python = "C:\Users\theju\jarvis\.venv\Scripts\pythonw.exe"   # mêmes bibliothèques que l'agent de production
New-Item -ItemType Directory -Force $Data | Out-Null

function Get-V3Agent {
    Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
        Where-Object { $_.CommandLine -like "*orion.winagent*v3_agent.toml*" }
}

# Arrêt demandé au superviseur (il arrête proprement l'agent), puis fin forcée de ce qui resterait.
if (Get-V3Agent) {
    Set-Content -Path (Join-Path $Data "supervise.stop") -Value "arrêt demandé"
    for ($i = 0; $i -lt 30 -and (Get-V3Agent); $i++) { Start-Sleep -Milliseconds 500 }
    Get-V3Agent | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Remove-Item -Path (Join-Path $Data "supervise.stop") -ErrorAction SilentlyContinue
if ($Stop) { return }
# Start-Process ne met pas les arguments entre guillemets : chemins avec espaces (« ORION V3 Agent ») cités ici.
$Arguments = '-m orion.winagent.supervise --config "{0}" --env "{1}" --log-file "{2}"' -f `
    (Join-Path $Root "deploy\v3_agent.toml"), (Join-Path $Data ".env"), (Join-Path $Data "agent.log")
Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -ArgumentList $Arguments
