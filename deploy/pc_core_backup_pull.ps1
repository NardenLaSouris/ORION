# Copie hebdomadaire de la sauvegarde du Core V3 sur le PC (docs/v3/environnements.md, « Sauvegarde de la nuit »).
#
#   powershell -ExecutionPolicy Bypass -File deploy\pc_core_backup_pull.ps1             copie la dernière hebdomadaire
#   powershell -ExecutionPolicy Bypass -File deploy\pc_core_backup_pull.ps1 -Installer  tâche planifiée (dimanche 12:30)
#   powershell -ExecutionPolicy Bypass -File deploy\pc_core_backup_pull.ps1 -Retirer    retire la tâche (copies gardées)
#
# - Lecture seule côté Core : récupère ~/sauvegardes/orion-v3/hebdomadaires/<dernière> par scp (clé SSH du PC, jamais
#   de mot de passe : BatchMode), dans %USERPROFILE%\.orion\sauvegardes\core-v3.
# - Vérifiée ici : chaque fichier du manifeste recontrôlé (SHA-256 et taille) ; une copie incomplète ou abîmée est
#   écartée (dossier .partiel supprimé), jamais rangée avec les bonnes.
# - 4 copies gardées sur le PC. La base personnelle reste chiffrée (clé F4 jamais copiée) ; aucun contenu n'est écrit
#   dans le journal, seulement des noms de dossier et des comptes.
# - Charge négligeable (quelques Mo), tâche sans fenêtre (conhost --headless), priorité basse : sans effet sur les jeux.
# -Source et -Vers : essai seulement (autre dossier du Core, autre dossier du PC).
param([switch]$Installer, [switch]$Retirer, [string]$Source = 'sauvegardes/orion-v3/hebdomadaires', [string]$Vers = '')
$ErrorActionPreference = 'Stop'
$Core = 'user@192.168.1.91'
$Remote = $Source
$Dest = if ($Vers) { $Vers } else { Join-Path $env:USERPROFILE '.orion\sauvegardes\core-v3' }
$Keep = 4
$TaskName = 'ORION sauvegarde du Core'
$Installed = Join-Path $env:USERPROFILE '.orion\sauvegardes\copie-core.ps1'
$Log = Join-Path $Dest 'copie.log'
$Pattern = '^\d{4}-\d{2}-\d{2}T\d{6}$'

function Write-Log([string]$Text) {
    New-Item -ItemType Directory -Force $Dest | Out-Null
    $line = '{0} {1}' -f (Get-Date -Format 's'), $Text
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Output $Text
}

if ($Retirer) {
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop } catch {}
    Remove-Item -Force -ErrorAction SilentlyContinue $Installed
    Write-Output "Tâche « $TaskName » retirée ; copies gardées dans $Dest."
    exit 0
}

if ($Installer) {
    New-Item -ItemType Directory -Force (Split-Path $Installed) | Out-Null
    Copy-Item -Force $PSCommandPath $Installed
    $argsLine = '--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $Installed
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $argsLine
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '12:30'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -Priority 7
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'Copie vérifiée de la sauvegarde hebdomadaire du Core ORION V3 (lecture seule côté Core).' `
        -Force | Out-Null
    Write-Output "Tâche « $TaskName » installée : chaque dimanche à 12:30 (rattrapée au démarrage si le PC était éteint)."
    Write-Output "Script installé : $Installed ; copies dans $Dest."
    exit 0
}

$ssh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
$names = & ssh.exe @ssh $Core "ls -1 $Remote 2>/dev/null; true"
if ($LASTEXITCODE -ne 0) { Write-Log "Core injoignable ; rien copié."; exit 1 }
$latest = @($names | Where-Object { $_ -match $Pattern } | Sort-Object)[-1]
if (-not $latest) { Write-Log "Aucune sauvegarde hebdomadaire sur le Core pour l'instant ; rien copié."; exit 0 }

$final = Join-Path $Dest $latest
if (Test-Path (Join-Path $final 'manifest.json')) { Write-Log "$latest déjà copiée ; rien à faire."; exit 0 }

# -r : sous-dossier config/ des sauvegardes (configuration du Core, depuis le 6 novembre 2026).
$partial = "$final.partiel"
if (Test-Path $partial) { Remove-Item -Recurse -Force $partial }
New-Item -ItemType Directory -Force $partial | Out-Null
& scp.exe -q -p -r @ssh "${Core}:$Remote/$latest/*" "$partial\"
if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $partial; Write-Log "Copie de $latest interrompue ; écartée."; exit 1 }

$problems = @()
try {
    $manifest = Get-Content -Raw -Encoding utf8 (Join-Path $partial 'manifest.json') | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $file = Join-Path $partial $entry.Name
        if (-not (Test-Path $file)) { $problems += "$($entry.Name) : absent"; continue }
        if ((Get-Item $file).Length -ne [int64]$entry.Value.bytes) { $problems += "$($entry.Name) : taille différente"; continue }
        if ((Get-FileHash -Algorithm SHA256 $file).Hash.ToLower() -ne $entry.Value.sha256) { $problems += "$($entry.Name) : résumé différent" }
    }
    $count = @($manifest.files.PSObject.Properties).Count
} catch {
    $problems += 'manifeste absent ou illisible'
}
if ($problems.Count -gt 0) {
    Remove-Item -Recurse -Force $partial
    Write-Log ("Copie de $latest écartée : " + ($problems -join '; '))
    exit 1
}
Rename-Item $partial $latest

$kept = @(Get-ChildItem -Directory $Dest | Where-Object { $_.Name -match $Pattern -and (Test-Path (Join-Path $_.FullName 'manifest.json')) } | Sort-Object Name)
if ($kept.Count -gt $Keep) { $kept[0..($kept.Count - $Keep - 1)] | ForEach-Object { Remove-Item -Recurse -Force $_.FullName } }
Write-Log ("$latest copiée et vérifiée ($count fichiers) ; {0} copie(s) gardée(s)." -f [Math]::Min($kept.Count, $Keep))
