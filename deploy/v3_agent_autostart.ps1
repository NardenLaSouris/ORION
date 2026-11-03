# Démarrage automatique de l'agent Windows V3 à l'ouverture de session : raccourci « ORION Agent V3 » du dossier
# Démarrage (comme l'agent de production, « ORION Agent », qui n'est pas touché). Le superviseur garantit une seule
# instance. Retrait : deploy\v3_agent_autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "ORION V3 Agent"
$Link = Join-Path ([Environment]::GetFolderPath("Startup")) "ORION Agent V3.lnk"
if ($Remove) { Remove-Item $Link -ErrorAction SilentlyContinue; "Retiré : $Link"; return }
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Shortcut.TargetPath = "C:\Users\theju\jarvis\.venv\Scripts\pythonw.exe"
$Shortcut.Arguments = '-m orion.winagent.supervise --config "{0}" --env "{1}" --log-file "{2}"' -f `
    (Join-Path $Root "deploy\v3_agent.toml"), (Join-Path $Data ".env"), (Join-Path $Data "agent.log")
$Shortcut.WorkingDirectory = $Root
$Shortcut.WindowStyle = 7
$Shortcut.Description = "ORION V3 : agent Windows (Discord, Frag Factory), port 8875"
$Shortcut.Save()
"Créé : $Link"
