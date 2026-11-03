# Lance l'agent Windows V3 (Discord) sans fenêtre, depuis la copie V3 du dépôt. Ne touche pas à l'agent de
# production (port 8765). Arrêt : deploy\v3_agent.ps1 -Stop
param([switch]$Stop)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "ORION V3 Agent"
$Python = "C:\Users\theju\jarvis\.venv\Scripts\pythonw.exe"   # mêmes bibliothèques que l'agent de production
$Mine = Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
    Where-Object { $_.CommandLine -like "*orion.winagent*v3_agent.toml*" }
if ($Mine) {
    $Mine | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}
if ($Stop) { return }
New-Item -ItemType Directory -Force $Data | Out-Null
# Start-Process ne met pas les arguments entre guillemets : chemins avec espaces (« ORION V3 Agent ») cités ici.
$Arguments = '-m orion.winagent --config "{0}" --env "{1}" --log-file "{2}"' -f `
    (Join-Path $Root "deploy\v3_agent.toml"), (Join-Path $Data ".env"), (Join-Path $Data "agent.log")
Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -ArgumentList $Arguments
