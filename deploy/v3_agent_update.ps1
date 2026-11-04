# Met à jour la copie de l'agent Windows V3 (C:\Users\theju\orion-v3-agent) sur un commit de la branche v3, puis
# relance l'agent. L'agent ne tourne jamais depuis la copie de travail : une modification en cours n'est prise
# qu'une fois commitée et déployée ici. Ne touche pas à l'agent de production.
#   deploy\v3_agent_update.ps1 [-Commit <hash>]     (par défaut : le commit courant de la branche v3)
param([string]$Commit = "v3")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Agent = "C:\Users\theju\orion-v3-agent"
$Target = (git -C $Repo rev-parse --verify "$Commit^{commit}").Trim()
if (-not (Test-Path (Join-Path $Agent ".git"))) {
    git -C $Repo worktree add --detach $Agent $Target | Out-Null
} else {
    if (git -C $Agent status --porcelain) { throw "Modifications locales dans $Agent : rien n'est changé." }
    git -C $Agent checkout -q --detach $Target
}
"Agent V3 : copie sur $($Target.Substring(0, 7))"
& (Join-Path $Agent "deploy\v3_agent.ps1")
& (Join-Path $Agent "deploy\v3_agent_autostart.ps1")
