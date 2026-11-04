# Passage de Frag Factory de l'instance d'ESSAI (données fictives, port 8752) à VOTRE instance (port 8742 : vos
# enregistrements et transcriptions). Votre décision : sans -Autoriser, le script montre seulement ce qu'il ferait.
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_reel.ps1             ce qui serait fait
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_reel.ps1 -Autoriser  le faire
#   powershell -ExecutionPolicy Bypass -File deploy\pc_fragfactory_reel.ps1 -Retour     revenir à l'instance d'essai
# Étapes : sauvegarde vérifiée de la base de votre instance (copie SQLite cohérente + contrôle d'intégrité), copie de
# sa configuration, liaison ORION activée dans sa configuration ([orion]), relance de votre instance avec le jeton de
# liaison de l'agent, agent visant le port 8742 (agent.local.toml), vérification par le Core V3. Rien n'est effacé.
param([switch]$Autoriser, [switch]$Retour)
$ErrorActionPreference = "Stop"
# Remplacé par deploy\pc_fragfactory_persistant.ps1 : une fois Frag Factory installé dans C:\Users\theju\FragFactory,
# ce script relancerait l'ANCIENNE installation sur ses anciennes données (deux bases divergentes) : refusé.
if (Test-Path (Join-Path $env:USERPROFILE "FragFactory\data\fragfactory.sqlite3")) {
    throw "Frag Factory est installé dans $env:USERPROFILE\FragFactory : utilisez deploy\pc_fragfactory_persistant.ps1 (-Retour pour revenir)."
}
# Frag Factory a été installé depuis l'application Claude : ses fichiers sont physiquement dans le stockage du paquet
# (LocalCache), que seule l'application voit à l'adresse habituelle. Le premier chemin qui existe est retenu.
$FFWork = "088b7887-957e-4849-9a79-bd6095b9d58e\5e9af580-f6cd-44f0-b10d-1e0f1c490b2b\scratch-2026-11-02-9bb6ed"
$FFRoot = @((Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\scratch-workspaces\$FFWork"),
            (Join-Path $env:APPDATA "Claude\scratch-workspaces\$FFWork")) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $FFRoot) { throw "Dossier de Frag Factory introuvable." }
$FFPython = "C:\Users\theju\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\FragFactory\venv\Scripts\python.exe"
$Base = Join-Path $env:USERPROFILE ".orion"
$Local = Join-Path $Base "agent.local.toml"
$FFConfig = Join-Path $FFRoot "config.toml"
$Port = 8742

function Restart-Agent {
    Set-Content -Path (Join-Path $Base "data\supervise.stop") -Value "relance"
    Start-Sleep -Seconds 8
    Remove-Item -Path (Join-Path $Base "data\supervise.stop") -ErrorAction SilentlyContinue
    Start-Process explorer.exe -ArgumentList ('"{0}"' -f (Join-Path $Base "ORION Agent.lnk"))
}

if ($Retour) {
    if (Test-Path $Local) { Move-Item $Local ($Local + ".retour-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
    Restart-Agent
    Write-Host "Agent revenu à l'instance d'essai (port 8752). Votre instance garde la liaison ORION dans sa configuration."
    return
}

$DataDir = Join-Path $FFRoot "data"
$Db = Join-Path $DataDir "fragfactory.sqlite3"
Write-Host "Votre instance : $FFRoot (port $Port)"
Write-Host ("Base : {0} ({1:N1} Mo)" -f $Db, ((Get-Item $Db).Length / 1MB))
Write-Host "Ce qui sera fait : sauvegarde vérifiée de la base et de config.toml dans $Base\sauvegardes ; [orion] activé"
Write-Host "dans config.toml ; votre instance relancée avec le jeton de liaison ; agent sur le port $Port."
if (-not $Autoriser) { Write-Host "Rien n'a été fait (ajoutez -Autoriser)."; return }

# 1. Sauvegarde cohérente (API de sauvegarde SQLite, même pendant le service) puis contrôle d'intégrité.
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $Base "sauvegardes\fragfactory-reel-$Stamp"
New-Item -ItemType Directory -Force $Backup | Out-Null
Copy-Item $FFConfig (Join-Path $Backup "config.toml")
Get-ChildItem $DataDir -File -Filter *.json | Copy-Item -Destination $Backup
$env:FF_SRC = $Db; $env:FF_DST = Join-Path $Backup "fragfactory.sqlite3"
# Programme écrit dans un fichier : passé par -c, PowerShell 5.1 retire les guillemets doubles (« SyntaxError »).
$Check = Join-Path $Backup "verifier.py"
[IO.File]::WriteAllText($Check, @"
import os, sqlite3
src, dst = sqlite3.connect(os.environ['FF_SRC']), sqlite3.connect(os.environ['FF_DST'])
src.backup(dst)
ok = dst.execute('PRAGMA integrity_check').fetchone()[0]
tables = dst.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
dst.close()
print('integrite :', ok, '; tables :', tables)
raise SystemExit(0 if ok == 'ok' and tables else 1)
"@, (New-Object Text.UTF8Encoding $false))
& $FFPython -I $Check
$Verified = $LASTEXITCODE
Remove-Item $Check
if ($Verified) { throw "Sauvegarde non vérifiée : rien d'autre n'est fait ($Backup)." }

# 2. Liaison ORION dans la configuration de votre instance (ajoutée seulement si absente).
$Text = [IO.File]::ReadAllText($FFConfig)
if ($Text -notmatch '(?m)^\[orion\]') {
    $Text = $Text.TrimEnd() + "`n`n[orion]`nenabled = true`npublic_url = `"http://127.0.0.1:$Port`"`n"
    [IO.File]::WriteAllText($FFConfig, $Text, (New-Object Text.UTF8Encoding $false))
}

# 3. Votre instance relancée avec le jeton de liaison de l'agent (le même que celui de l'instance d'essai).
$Line = Select-String -Path (Join-Path $Base "data\.env") -Pattern '^FRAGFACTORY_LINK_TOKEN=(.+)$'
if (-not $Line) { throw "FRAGFACTORY_LINK_TOKEN absent de $Base\data\.env." }
$Listen = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
foreach ($c in $Listen) {
    $p = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $c.OwningProcess)
    if ($p.CommandLine -like "*fragfactory serve*") {
        Stop-Process -Id $p.ParentProcessId -Force -ErrorAction SilentlyContinue
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
$env:ORION_LINK_TOKEN = $Line.Matches[0].Groups[1].Value
$env:FRAGFACTORY_CONFIG = $FFConfig
$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath $FFPython -WorkingDirectory $FFRoot -WindowStyle Hidden -ArgumentList "-m fragfactory serve" `
    -RedirectStandardError (Join-Path $Backup "serve.log")
$env:ORION_LINK_TOKEN = $null

# 4. Agent vers votre instance, puis vérification par le Core V3.
[IO.File]::WriteAllText($Local, "# Frag Factory : votre instance (deploy\pc_fragfactory_reel.ps1, $Stamp)`n[fragfactory]`nurl = `"http://127.0.0.1:$Port`"`n",
    (New-Object Text.UTF8Encoding $false))
Restart-Agent
$Token = Join-Path $env:APPDATA "ORION Control V3\token"
$Headers = @{ Authorization = "Bearer " + (Get-Content $Token -Raw).Trim() }
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 5
    try {
        $State = Invoke-RestMethod -Uri "http://192.168.1.91:8866/api/modules/fragfactory" -Headers $Headers -TimeoutSec 15
        if ($State.connected) { Write-Host "Frag Factory relié à ORION V3 : votre instance. Sauvegarde : $Backup"; return }
    } catch { }
}
throw "Le Core V3 ne voit pas votre instance. Retour : deploy\pc_fragfactory_reel.ps1 -Retour (sauvegarde : $Backup)"
