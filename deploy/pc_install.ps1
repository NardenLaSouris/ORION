# Installation de référence ORION du PC fixe (docs/v3/reference_unique.md) : l'agent Windows V3 (le seul agent ORION de
# ce PC) et ORION Control V3, dans C:\Users\theju\.orion, mis à jour depuis la branche v3.
#
#   powershell -ExecutionPolicy Bypass -File deploy\pc_install.ps1                  dernier commit de origin/v3
#   powershell -ExecutionPolicy Bypass -File deploy\pc_install.ps1 -Commit 6dd7411  commit V3 précis
#   powershell -ExecutionPolicy Bypass -File deploy\pc_install.ps1 -Rollback        revient à la version précédente
#   powershell -ExecutionPolicy Bypass -File deploy\pc_install.ps1 -Stop            arrête l'agent et ORION Control
#   ... -Autostart   installe aussi les raccourcis (démarrage de session et menu Démarrer) : un seul de chaque
#   ... -RetireV2    retire l'ancien agent V2 et l'ancien ORION Control (processus arrêtés, raccourcis mis de côté)
#
# Étapes d'une mise à jour : commit V3 vérifié (ancêtre de origin/v3), modifications affichées, copie du code dans
# releases\<commit>, dépendances si elles ont changé, contrôle des imports AVANT tout arrêt, arrêt propre, bascule,
# redémarrage, vérification locale (port) puis par le Core V3 (agent « en ligne »), retour automatique à la version
# précédente en cas d'échec. Journal : data\deploiements.log.
param([string]$Commit = "origin/v3", [switch]$Rollback, [switch]$Stop, [switch]$Autostart, [switch]$RetireV2)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Base = Join-Path $env:USERPROFILE ".orion"
$Releases = Join-Path $Base "releases"
$Data = Join-Path $Base "data"
$Venv = Join-Path $Base "venv"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"
$Python = Join-Path $Venv "Scripts\python.exe"
$BasePython = "C:\Python313\python.exe"
$Core = "http://192.168.1.91:8866"
$AgentPort = 8875
$Startup = [Environment]::GetFolderPath("Startup")
$Menu = [Environment]::GetFolderPath("Programs")
New-Item -ItemType Directory -Force $Releases, $Data | Out-Null

function Log([string]$Text) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text
    Write-Host $line
    Add-Content -Path (Join-Path $Data "deploiements.log") -Value $line -Encoding UTF8
}

function Get-Ours([string]$What) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
        $c = $_.CommandLine
        switch ($What) {
            "agent"     { $c -like "*pc_start.pyw*agent*" -or ($c -like "*orion.winagent*" -and $c -like "*\.orion\*") -or
                          $c -like "*orion.winagent*v3_agent.toml*" }
            "control"   { $c -like "*pc_start.pyw*control*" -or $c -like "*orion.control --profile v3*" }
            "v2agent"   { $c -like "*orion.winagent --log-file data\winagent.log*" -or $c -like "*jarvis.winagent*" }
            "v2control" { $c -like "*jarvis.control*" -or ($c -like "*orion.control*" -and $c -notlike "*v3*") }
        }
    }
}

function Stop-Ours([string]$What) {
    if ($What -eq "agent") {
        # superviseur : arrêt demandé par fichier (il arrête l'agent proprement), ancien dossier V3 compris
        foreach ($folder in @($Data, (Join-Path $env:LOCALAPPDATA "ORION V3 Agent"))) {
            if (Test-Path $folder) { Set-Content -Path (Join-Path $folder "supervise.stop") -Value "arrêt" }
        }
        for ($i = 0; $i -lt 30 -and (Get-Ours "agent"); $i++) { Start-Sleep -Milliseconds 500 }
    }
    Get-Ours $What | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($folder in @($Data, (Join-Path $env:LOCALAPPDATA "ORION V3 Agent"))) {
        Remove-Item -Path (Join-Path $folder "supervise.stop") -ErrorAction SilentlyContinue
    }
}

function New-Link([string]$Path, [string]$What, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $PythonW
    $link.Arguments = '"{0}" {1}' -f (Join-Path $Base "pc_start.pyw"), $What
    $link.WorkingDirectory = $Base
    $link.WindowStyle = 7
    $link.Description = $Description
    $link.Save()
}

function Start-Ours([string]$What) {
    # Lancé par l'Explorateur : hors de toute application isolée (fichiers et secrets au vrai emplacement).
    $lnk = Join-Path $Base ("ORION {0}.lnk" -f @{agent = "Agent"; control = "Control"}[$What])
    Start-Process explorer.exe -ArgumentList ('"{0}"' -f $lnk)
}

function Test-Agent {
    for ($i = 0; $i -lt 40; $i++) {
        $listen = Get-NetTCPConnection -State Listen -LocalPort $AgentPort -ErrorAction SilentlyContinue
        if ($listen) {
            $owner = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $listen[0].OwningProcess)
            if ($owner.CommandLine -like "*orion.winagent*" -and $owner.CommandLine -like "*\.orion\*") { break }
        }
        Start-Sleep -Milliseconds 500
    }
    if ($i -ge 40) { return "l'agent n'écoute pas sur le port $AgentPort" }
    $tokenFile = Join-Path $env:APPDATA "ORION Control V3\token"
    if (-not (Test-Path $tokenFile)) { return "" }  # pas de jeton d'ORION Control : vérification locale seulement
    $headers = @{ Authorization = "Bearer " + (Get-Content $tokenFile -Raw).Trim() }
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $null = Invoke-RestMethod -Method Post -Uri "$Core/api/modules/discord/refresh" -Headers $headers -TimeoutSec 15
            $state = Invoke-RestMethod -Uri "$Core/api/modules/discord" -Headers $headers -TimeoutSec 15
            if ($state.agent.state -eq "en ligne") { return "" }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return "le Core V3 ne voit pas l'agent en ligne"
}

if ($Stop) { Stop-Ours "agent"; Stop-Ours "control"; Log "Arrêt de l'agent et d'ORION Control"; return }

# --- Version visée ------------------------------------------------------------------------------------------------
$Current = if (Test-Path (Join-Path $Base "current")) { (Get-Content (Join-Path $Base "current")).Trim() } else { "" }
$Previous = if (Test-Path (Join-Path $Base "previous")) { (Get-Content (Join-Path $Base "previous")).Trim() } else { "" }
if ($Rollback) {
    if (-not $Previous) { throw "Aucune version précédente." }
    $Sha = $Previous
} else {
    git -C $Repo fetch -q origin v3
    if ($LASTEXITCODE) { throw "git fetch impossible." }
    $Sha = (git -C $Repo rev-parse --short=12 "$Commit^{commit}").Trim()
    if ($LASTEXITCODE -or -not $Sha) { throw "Commit introuvable : $Commit" }
    git -C $Repo merge-base --is-ancestor $Sha origin/v3
    if ($LASTEXITCODE) { throw "$Sha n'est pas un commit de la branche v3 (origin/v3) : refusé." }
}
$Target = Join-Path $Releases $Sha
$subject = (git -C $Repo log -1 --format=%s $Sha)
if ($subject.Length -gt 70) { $subject = $subject.Substring(0, 70) + "…" }
Log ("Installation visée : {0} ({1}) ; en service : {2}" -f $Sha, $subject, $(if ($Current) { $Current } else { "aucune" }))
if ($Current -and $Current -ne $Sha) {
    Write-Host "Modifications pour le PC :"
    git -C $Repo diff --stat "$Current" "$Sha" -- orion/winagent orion/control orion/tools deploy/agent.toml deploy/pc_start.pyw requirements-pc.txt
}

# --- Code et dépendances (rien n'est encore arrêté) ----------------------------------------------------------------
if (-not (Test-Path $Target)) {
    $tar = Join-Path $Releases "$Sha.tar"
    git -C $Repo archive -o $tar $Sha orion deploy/agent.toml deploy/pc_start.pyw requirements-pc.txt
    if ($LASTEXITCODE) { throw "git archive impossible." }
    New-Item -ItemType Directory -Force $Target | Out-Null
    tar -xf $tar -C $Target
    Remove-Item $tar
}
if (-not (Test-Path $Python)) { & $BasePython -m venv $Venv; Log "Environnement Python créé : $Venv" }
$reqHash = (Get-FileHash (Join-Path $Target "requirements-pc.txt")).Hash
$reqDone = Join-Path $Venv "requirements-pc.sha256"
if (-not (Test-Path $reqDone) -or (Get-Content $reqDone).Trim() -ne $reqHash) {
    & $Python -m pip install -q --disable-pip-version-check -r (Join-Path $Target "requirements-pc.txt")
    if ($LASTEXITCODE) { throw "Installation des dépendances impossible." }
    Set-Content -Path $reqDone -Value $reqHash
    Log "Dépendances installées"
}
Push-Location $Target
try {
    & $Python -c "import orion.winagent.__main__, orion.winagent.supervise, orion.control.app"
    if ($LASTEXITCODE) { throw "Contrôle des imports en échec : version $Sha non installée, rien n'a été arrêté." }
} finally { Pop-Location }

# --- Données (première installation : reprises de l'ancien dossier de l'agent V3, jamais effacées) ------------------
if (-not (Test-Path (Join-Path $Data ".env"))) {
    $old = Join-Path $env:LOCALAPPDATA "ORION V3 Agent"
    foreach ($name in ".env", "discord_token.bin", "discord_last_state.json") {
        if (Test-Path (Join-Path $old $name)) { Copy-Item (Join-Path $old $name) (Join-Path $Data $name) }
    }
    if (-not (Test-Path (Join-Path $Data ".env"))) { throw "Aucun secret de l'agent ($Data\.env) : installation arrêtée." }
    Log "Secrets et liaison Discord repris de $old (laissés en place)"
}
if (Test-Path (Join-Path $Base "agent.toml")) { Copy-Item (Join-Path $Base "agent.toml") (Join-Path $Base "agent.toml.precedent") -Force }
Copy-Item (Join-Path $Target "deploy\agent.toml") (Join-Path $Base "agent.toml") -Force
Copy-Item (Join-Path $Target "deploy\pc_start.pyw") (Join-Path $Base "pc_start.pyw") -Force
New-Link (Join-Path $Base "ORION Agent.lnk") "agent" "ORION : agent Windows V3 (port 8875)"
New-Link (Join-Path $Base "ORION Control.lnk") "control" "ORION Control V3"

# --- Bascule ----------------------------------------------------------------------------------------------------
if ($RetireV2) {
    Stop-Ours "v2agent"; Stop-Ours "v2control"
    Log "Ancien agent V2 et ancien ORION Control arrêtés"
}
Stop-Ours "agent"; Stop-Ours "control"
if ($Current -ne $Sha) {
    if ($Current) { Set-Content -Path (Join-Path $Base "previous") -Value $Current }
    Set-Content -Path (Join-Path $Base "current") -Value $Sha
}
Start-Ours "agent"
$problem = Test-Agent
if ($problem -and $Current -and $Current -ne $Sha) {
    Log "ÉCHEC de $Sha : $problem ; retour à $Current"
    Stop-Ours "agent"
    Set-Content -Path (Join-Path $Base "current") -Value $Current
    Set-Content -Path (Join-Path $Base "previous") -Value $Sha
    Copy-Item (Join-Path $Base "agent.toml.precedent") (Join-Path $Base "agent.toml") -Force
    Start-Ours "agent"; Start-Ours "control"
    throw "Mise à jour annulée : $problem. Version $Current remise en service."
}
if ($problem) { throw "Agent $Sha installé mais : $problem (voir $Data\agent.log)." }
Start-Ours "control"
Log "En service : $Sha (agent vérifié par le Core V3)"

# --- Démarrage automatique : un seul raccourci par programme --------------------------------------------------------
if ($Autostart -or $RetireV2) {
    $aside = Join-Path $Base "raccourcis-retires"
    New-Item -ItemType Directory -Force $aside | Out-Null
    foreach ($folder in @($Startup, $Menu)) {
        foreach ($name in "ORION Agent.lnk", "ORION Control.lnk", "ORION Agent V3.lnk", "ORION Control V3.lnk",
                          "JARVIS Agent.lnk", "JARVIS Control.lnk") {
            $path = Join-Path $folder $name
            if (Test-Path $path) {
                $kind = if ($folder -eq $Startup) { "demarrage" } else { "menu" }
                Move-Item $path (Join-Path $aside "$kind - $name") -Force
            }
        }
    }
    Copy-Item (Join-Path $Base "ORION Agent.lnk") (Join-Path $Startup "ORION Agent.lnk")
    Copy-Item (Join-Path $Base "ORION Control.lnk") (Join-Path $Startup "ORION Control.lnk")
    Copy-Item (Join-Path $Base "ORION Agent.lnk") (Join-Path $Menu "ORION Agent.lnk")
    Copy-Item (Join-Path $Base "ORION Control.lnk") (Join-Path $Menu "ORION Control.lnk")
    Log "Démarrage automatique : ORION Agent et ORION Control (anciens raccourcis dans $aside)"
}

# --- Ménage : les 3 dernières versions sont gardées (retour arrière) ------------------------------------------------
$keep = @($Sha, $Current, $Previous) | Where-Object { $_ }
Get-ChildItem $Releases -Directory | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 |
    Where-Object { $keep -notcontains $_.Name } | ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
