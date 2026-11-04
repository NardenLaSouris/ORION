# Contrôle de l'installation ORION du PC fixe, avant et après un redémarrage (docs/v3/reference_unique.md) :
#   powershell -ExecutionPolicy Bypass -File C:\Users\theju\orion-v3\deploy\pc_controle_demarrage.ps1
# Vérifie : un seul superviseur, un seul agent (V3), un seul ORION Control ; aucun ancien agent V2 ni ancien ORION
# Control ; un seul raccourci de démarrage par programme ; port 8875 tenu par l'agent de l'installation de référence,
# rien sur 8765 ; le Core V3 voit l'agent ; Discord et Frag Factory joignables par l'agent. Code 0 si tout est bon.
$ErrorActionPreference = "Stop"
$Base = Join-Path $env:USERPROFILE ".orion"
$Fails = 0
function Check([string]$Label, [bool]$Ok, [string]$Detail = "") {
    $mark = if ($Ok) { "OK  " } else { $script:Fails++; "ÉCHEC" }
    Write-Host ("{0} {1}{2}" -f $mark, $Label, $(if ($Detail) { " : $Detail" } else { "" }))
}

# Le lanceur pythonw d'un venv relance l'interpréteur : un programme = un lanceur et son enfant ; seuls les processus
# dont le parent n'a pas la même ligne de commande sont comptés.
$All = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'")
function Count-Top([scriptblock]$Match) {
    $mine = @($All | Where-Object $Match)
    @($mine | Where-Object { $p = $_; -not ($mine | Where-Object { $_.ProcessId -eq $p.ParentProcessId }) }).Count
}
$Supervisors = Count-Top { $_.CommandLine -like "*pc_start.pyw*agent*" -or $_.CommandLine -like "*orion.winagent.supervise*" }
$Agents = Count-Top { $_.CommandLine -like "*-m orion.winagent *" -and $_.CommandLine -notlike "*supervise*" }
$Controls = Count-Top { $_.CommandLine -like "*pc_start.pyw*control*" -or $_.CommandLine -like "*orion.control*" -or $_.CommandLine -like "*jarvis.control*" }
$Old = Count-Top { $_.CommandLine -like "*\jarvis\.venv*" -and ($_.CommandLine -like "*winagent*" -or $_.CommandLine -like "*control*") }
Check "un seul superviseur" ($Supervisors -eq 1) "$Supervisors"
Check "un seul agent" ($Agents -eq 1) "$Agents"
Check "un seul ORION Control" ($Controls -eq 1) "$Controls"
Check "aucun ancien agent ni ancien ORION Control (V2)" ($Old -eq 0) "$Old"
$Ref = @($All | Where-Object { $_.CommandLine -like "*-m orion.winagent *" -and $_.CommandLine -notlike "*\.orion\*" }).Count
Check "l'agent vient de l'installation de référence (C:\Users\theju\.orion)" ($Ref -eq 0) "$Ref hors installation"

$Startup = [Environment]::GetFolderPath("Startup")
$Links = @(Get-ChildItem $Startup -Filter *.lnk | Where-Object { $_.Name -match "ORION|JARVIS" } | Select-Object -ExpandProperty Name)
Check "démarrage : exactement « ORION Agent » et « ORION Control »" (($Links | Sort-Object) -join "|" -eq "ORION Agent.lnk|ORION Control.lnk") ($Links -join ", ")
$Shell = New-Object -ComObject WScript.Shell
foreach ($name in $Links) {
    $target = $Shell.CreateShortcut((Join-Path $Startup $name))
    Check "« $name » lance l'installation de référence" ($target.TargetPath -like "$Base\venv\*" -and $target.Arguments -like "*pc_start.pyw*") $target.Arguments
}

$Listen8875 = Get-NetTCPConnection -State Listen -LocalPort 8875 -ErrorAction SilentlyContinue
$Owner = if ($Listen8875) { ($All | Where-Object { $_.ProcessId -eq $Listen8875[0].OwningProcess }).CommandLine } else { "" }
Check "port 8875 tenu par l'agent de référence" ($Owner -like "*\.orion\*") $(if ($Listen8875) { "processus $($Listen8875[0].OwningProcess)" } else { "personne" })
Check "rien sur le port 8765 (ancien agent V2)" (-not (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue))
Check "version en service" (Test-Path (Join-Path $Base ("releases\" + (Get-Content (Join-Path $Base "current")).Trim()))) (Get-Content (Join-Path $Base "current"))

$TokenFile = Join-Path $env:APPDATA "ORION Control V3\token"
if (Test-Path $TokenFile) {
    $Headers = @{ Authorization = "Bearer " + (Get-Content $TokenFile -Raw).Trim() }
    try {
        $null = Invoke-RestMethod -Method Post -Uri "http://192.168.1.91:8866/api/modules/discord/refresh" -Headers $Headers -TimeoutSec 15
        $D = Invoke-RestMethod -Uri "http://192.168.1.91:8866/api/modules/discord" -Headers $Headers -TimeoutSec 15
        Check "le Core V3 voit l'agent" ($D.agent.state -eq "en ligne") $D.agent.state
        Check "Discord relié par l'agent" ($D.auth -eq "lié") ("{0}, {1}" -f $D.discord, $D.auth)
        $F = Invoke-RestMethod -Uri "http://192.168.1.91:8866/api/modules/fragfactory" -Headers $Headers -TimeoutSec 15
        # Frag Factory (instance d'essai) n'a pas de démarrage automatique : avertissement seulement après un
        # redémarrage (relance : deploy\pc_fragfactory_essai.ps1).
        if ($F.connected) { Check "Frag Factory joignable par l'agent" $true }
        else { Write-Host "AVERT Frag Factory injoignable (instance d'essai sans démarrage automatique : deploy\pc_fragfactory_essai.ps1)" }
    } catch { Check "Core V3 joignable" $false $_.Exception.Message }
} else { Check "jeton d'ORION Control (vérifications par le Core)" $false "absent : $TokenFile" }

Write-Host ""
if ($Fails) { Write-Host "$Fails point(s) en échec."; exit 1 } else { Write-Host "Tout est conforme."; exit 0 }
