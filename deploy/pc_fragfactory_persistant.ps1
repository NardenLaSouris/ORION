# Frag Factory : de l'installation dans le stockage de l'application Claude (LocalCache) vers l'installation
# persistante C:\Users\theju\FragFactory (plan validé par le propriétaire le 4 novembre 2026).
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_persistant.ps1             ce qui serait fait
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_persistant.ps1 -Autoriser  migrer
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_persistant.ps1 -Retour     revenir à l'ancienne
# Prérequis (faits avant) : code cloné dans C:\Users\theju\FragFactory et .venv installé aux versions de l'ancien.
# Étapes : sauvegarde vérifiée (copie SQLite cohérente + comptes + config + bundle git) ; arrêt de l'instance du port
# 8742 ; COPIE des données (jamais de déplacement) ; SHA-256 identiques fichier par fichier, sinon l'ancienne instance
# est relancée et rien d'autre n'est fait ; dans la copie seulement : chemins réécrits, rendus en attente mis en pause,
# rendu automatique coupé ; superviseur et raccourci « Frag Factory » au démarrage ; vérification par le Core V3.
# L'ancienne installation n'est ni modifiée ni effacée.
param([switch]$Autoriser, [switch]$Retour)
$ErrorActionPreference = "Stop"
$FFWork = "088b7887-957e-4849-9a79-bd6095b9d58e\5e9af580-f6cd-44f0-b10d-1e0f1c490b2b\scratch-2026-11-02-9bb6ed"
$OldRoot = Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\scratch-workspaces\$FFWork"
$OldPython = Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\FragFactory\venv\Scripts\python.exe"
$New = Join-Path $env:USERPROFILE "FragFactory"
$NewPython = Join-Path $New ".venv\Scripts\python.exe"
$NewPythonW = Join-Path $New ".venv\Scripts\pythonw.exe"
$Base = Join-Path $env:USERPROFILE ".orion"
$Home_ = Join-Path $Base "fragfactory"
$Tool = Join-Path $PSScriptRoot "pc_fragfactory_migration.py"
$Link = Join-Path ([Environment]::GetFolderPath("Startup")) "Frag Factory.lnk"
$Port = 8742
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false  # sorties de Python (UTF-8) lues sans erreur d'accents

function Stop-Instance {
    # Seulement un processus « fragfactory serve » à l'écoute sur 8742 (et son lanceur de venv).
    foreach ($c in @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
        $p = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $c.OwningProcess)
        if ($p.CommandLine -like "*fragfactory serve*") {
            $parent = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $p.ParentProcessId)
            if ($parent.CommandLine -like "*fragfactory serve*") { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    for ($i = 0; $i -lt 15 -and (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Le port $Port reste occupé." }
}

function Start-Old {
    # Ancienne instance, lancée comme le faisait deploy\pc_fragfactory_reel.ps1 (même jeton de liaison).
    $Line = Select-String -Path (Join-Path $Base "data\.env") -Pattern '^FRAGFACTORY_LINK_TOKEN=(.+)$'
    $env:ORION_LINK_TOKEN = $Line.Matches[0].Groups[1].Value
    $env:FRAGFACTORY_CONFIG = Join-Path $OldRoot "config.toml"
    Start-Process -FilePath $OldPython -WorkingDirectory $OldRoot -WindowStyle Hidden -ArgumentList "-m fragfactory serve" `
        -RedirectStandardError (Join-Path $Home_ ("ancienne-serve-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"))
    $env:ORION_LINK_TOKEN = $null; $env:FRAGFACTORY_CONFIG = $null
}

function Stop-Supervisor {
    if (-not (Test-Path $Home_)) { return }
    Set-Content -Path (Join-Path $Home_ "stop") -Value "arrêt"
    for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
}

function Wait-Core {
    $Token = Join-Path $env:APPDATA "ORION Control V3\token"
    $Headers = @{ Authorization = "Bearer " + (Get-Content $Token -Raw).Trim() }
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Seconds 5
        try {
            $State = Invoke-RestMethod -Uri "http://192.168.1.91:8866/api/modules/fragfactory" -Headers $Headers -TimeoutSec 15
            if ($State.connected) { return $true }
        } catch { }
    }
    return $false
}

New-Item -ItemType Directory -Force $Home_ | Out-Null

if ($Retour) {
    # Retour à l'ancienne installation : la nouvelle est arrêtée (rien n'est effacé), raccourci mis de côté.
    Stop-Supervisor
    Stop-Instance
    if (Test-Path $Link) { Move-Item $Link (Join-Path $Home_ ("Frag Factory.lnk.retour-" + (Get-Date -Format "yyyyMMdd-HHmmss"))) }
    Start-Old
    Write-Host "Ancienne instance relancée (port $Port). ATTENTION : ce qui a été fait dans la nouvelle installation"
    Write-Host "(validations, rendus) n'y figure pas ; la nouvelle installation reste intacte dans $New."
    if (Wait-Core) { Write-Host "Le Core V3 voit Frag Factory relié." } else { Write-Host "Le Core V3 ne voit pas encore Frag Factory." }
    return
}

# --- 0. Contrôles préalables -------------------------------------------------------------------------------------
foreach ($p in $OldRoot, $OldPython, $NewPython, $NewPythonW, (Join-Path $New "fragfactory\__main__.py"), $Tool) {
    if (-not (Test-Path $p)) { throw "Introuvable : $p" }
}
if (Test-Path (Join-Path $New "data")) { throw "$New\data existe déjà : rien n'est écrasé (migration déjà faite ?)." }
$Free = (Get-PSDrive C).Free / 1GB
Write-Host ("Ancienne installation : {0}" -f $OldRoot)
Write-Host ("Nouvelle installation : {0} (C: libre {1:N1} Go)" -f $New, $Free)
Write-Host "Ce qui sera fait : sauvegarde vérifiée, arrêt de l'instance du port $Port, copie des données et de la"
Write-Host "configuration, vérification SHA-256, corrections dans la copie, superviseur et raccourci au démarrage."
if (-not $Autoriser) { Write-Host "Rien n'a été fait (ajoutez -Autoriser)."; return }
if ($Free -lt 2) { throw "Moins de 2 Go libres sur C: : arrêt." }

# --- 1. Sauvegarde vérifiée --------------------------------------------------------------------------------------
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $Base "sauvegardes\fragfactory-avant-migration-$Stamp"
New-Item -ItemType Directory -Force $Backup | Out-Null
Copy-Item (Join-Path $OldRoot "config.toml") $Backup
Get-ChildItem (Join-Path $OldRoot "data") -File -Filter *.json | Copy-Item -Destination $Backup
# git écrit sa progression sur stderr : passé par cmd (sous Windows PowerShell 5.1, stderr + Stop = exception).
cmd /c ('git -C "{0}" bundle create "{1}" --all >nul 2>nul' -f $OldRoot, (Join-Path $Backup "fragfactory-code.bundle"))
if ($LASTEXITCODE) { throw "Bundle git du code en échec." }
$Before = & $NewPython -I $Tool backup (Join-Path $OldRoot "data\fragfactory.sqlite3") (Join-Path $Backup "fragfactory.sqlite3")
if ($LASTEXITCODE) { throw "Sauvegarde non vérifiée : rien d'autre n'est fait ($Backup)." }
Set-Content -Path (Join-Path $Backup "comptes-avant.json") -Value $Before -Encoding UTF8
Write-Host "1. Sauvegarde vérifiée : $Backup"

# --- 2. Arrêt de l'instance actuelle -----------------------------------------------------------------------------
Stop-Instance
Write-Host "2. Instance du port $Port arrêtée"

# --- 3. Copie et vérification ------------------------------------------------------------------------------------
try {
    & $NewPython -I $Tool manifest (Join-Path $OldRoot "data") (Join-Path $Backup "manifeste-source.json")
    if ($LASTEXITCODE) { throw "Manifeste de l'ancienne installation en échec." }
    robocopy (Join-Path $OldRoot "data") (Join-Path $New "data") /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copie robocopy en échec (code $LASTEXITCODE)." }
    foreach ($f in Get-ChildItem $OldRoot -File -Filter "config.toml*") { Copy-Item $f.FullName $New }
    & $NewPython -I $Tool manifest (Join-Path $New "data") (Join-Path $Backup "manifeste-copie.json")
    & $NewPython -I $Tool compare (Join-Path $Backup "manifeste-source.json") (Join-Path $Backup "manifeste-copie.json")
    if ($LASTEXITCODE) { throw "La copie diffère de l'original." }
    Write-Host "3. Données copiées, SHA-256 identiques"

    # --- 4. Corrections dans la copie seulement ------------------------------------------------------------------
    $Fix = & $NewPython -I $Tool fix $New $OldRoot $New
    if ($LASTEXITCODE) { throw "Corrections de la copie en échec : $Fix" }
    Set-Content -Path (Join-Path $Backup "corrections.json") -Value $Fix -Encoding UTF8
    $After = & $NewPython -I $Tool inventory (Join-Path $New "data\fragfactory.sqlite3")
    Set-Content -Path (Join-Path $Backup "comptes-apres.json") -Value $After -Encoding UTF8
    Write-Host "4. Corrections : $Fix"
} catch {
    Write-Host "ÉCHEC : $($_.Exception.Message) ; l'ancienne instance est relancée, la copie partielle reste dans $New\data."
    Start-Old
    throw
}

# --- 5. Superviseur et démarrage ---------------------------------------------------------------------------------
Copy-Item (Join-Path $PSScriptRoot "pc_fragfactory_start.pyw") (Join-Path $Home_ "start.pyw") -Force
$Shell = New-Object -ComObject WScript.Shell
$Sc = $Shell.CreateShortcut($Link)
$Sc.TargetPath = $NewPythonW
$Sc.Arguments = '"{0}"' -f (Join-Path $Home_ "start.pyw")
$Sc.WorkingDirectory = $New
$Sc.Description = "Frag Factory (port $Port), lancé et surveillé pour ORION"
$Sc.Save()
Remove-Item (Join-Path $Home_ "stop") -ErrorAction SilentlyContinue
Start-Process explorer.exe -ArgumentList ('"{0}"' -f $Link)
Write-Host "5. Frag Factory lancé depuis $New (raccourci « Frag Factory » au démarrage)"

# --- 6. Vérification ---------------------------------------------------------------------------------------------
if (Wait-Core) { Write-Host "6. Frag Factory relié à ORION V3 depuis l'installation persistante. Sauvegarde : $Backup" }
else { throw "Le Core V3 ne voit pas Frag Factory. Retour : deploy\pc_fragfactory_persistant.ps1 -Retour (sauvegarde : $Backup)" }
