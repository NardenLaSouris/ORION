# Audit LOCAL du Katana, à lancer SUR le Katana par le propriétaire (aucun accès distant, aucune modification) :
#   powershell -ExecutionPolicy Bypass -File katana_audit.ps1
# Lecture seule : versions installées (C:\Users\theju\.orion), agent V3 (port 8875), transcription (port 8766),
# Ollama (port 11434), GPU, démarrage automatique, vestiges de l'agent V2, espace disque. Aucun secret n'est lu ni
# affiché : pour les fichiers .env, seuls leur présence et les NOMS des variables sont notés, jamais leurs valeurs.
# Le rapport est écrit sur le Bureau (katana-audit-<date>.txt) ; il suffit de le renvoyer ou d'en coller le contenu.
$ErrorActionPreference = "Continue"
# Accents lisibles aussi quand le script est lancé par ssh depuis le PC.
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$Base = Join-Path $env:USERPROFILE ".orion"
$Out = Join-Path ([Environment]::GetFolderPath("Desktop")) ("katana-audit-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")
$Lines = New-Object System.Collections.Generic.List[string]
function Say([string]$Text) { $Lines.Add($Text); Write-Host $Text }
function Section([string]$Title) { Say ""; Say "=== $Title" }

Say "Audit du Katana : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ; machine $env:COMPUTERNAME ; compte $env:USERNAME"
$Os = Get-CimInstance Win32_OperatingSystem
Say ("Windows : {0} {1} ; démarré le {2}" -f $Os.Caption, $Os.Version, $Os.LastBootUpTime)

Section "Installation ORION V3 ($Base)"
if (Test-Path $Base) {
    foreach ($f in "current", "previous") {
        $p = Join-Path $Base $f
        Say ("{0} : {1}" -f $f, $(if (Test-Path $p) { (Get-Content $p -Raw).Trim() } else { "absent" }))
    }
    $Rel = Join-Path $Base "releases"
    if (Test-Path $Rel) {
        Say ("versions présentes : " + ((Get-ChildItem $Rel -Directory | Sort-Object LastWriteTime | ForEach-Object { $_.Name + " (" + $_.LastWriteTime.ToString("dd/MM HH:mm") + ")" }) -join ", "))
    }
    foreach ($f in "agent.toml", "agent.local.toml", "pc_start.pyw", "venv\Scripts\pythonw.exe") {
        Say ("{0} : {1}" -f $f, $(if (Test-Path (Join-Path $Base $f)) { "présent" } else { "absent" }))
    }
    $Env = Join-Path $Base "data\.env"
    if (Test-Path $Env) {
        $Names = Get-Content $Env | Where-Object { $_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=' } | ForEach-Object { $Matches[1] }
        Say ("data\.env : présent ; variables (noms seuls) : " + ($Names -join ", "))
    } else { Say "data\.env : absent" }
    $Log = Join-Path $Base "data\agent.log"
    if (Test-Path $Log) { Say ("agent.log : {0:N0} Ko, modifié le {1}" -f ((Get-Item $Log).Length / 1KB), (Get-Item $Log).LastWriteTime) }
} else { Say "absente" }

Section "Ports à l'écoute"
foreach ($p in @(@{Port = 8875; Nom = "agent V3"}, @{Port = 8766; Nom = "transcription"}, @{Port = 11434; Nom = "Ollama"})) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $p.Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $c.OwningProcess)
        # Ligne de commande réduite au programme et à ses deux premiers arguments (jamais d'éventuel jeton en argument).
        $cmd = if ($proc.CommandLine) { (($proc.CommandLine -split '\s+') | Select-Object -First 3) -join " " } else { $proc.Name }
        Say ("{0} ({1}) : à l'écoute sur {2}, pid {3}, depuis {4} : {5}" -f $p.Port, $p.Nom, $c.LocalAddress, $c.OwningProcess, $proc.CreationDate, $cmd)
    } else { Say ("{0} ({1}) : rien à l'écoute" -f $p.Port, $p.Nom) }
}

Section "Réponses locales"
foreach ($u in "http://127.0.0.1:11434/api/version", "http://127.0.0.1:11434/api/tags") {
    try { $r = Invoke-RestMethod -Uri $u -TimeoutSec 5
          if ($r.models) { Say ("$u : " + (($r.models | ForEach-Object { $_.name }) -join ", ")) } else { Say ("$u : " + ($r | ConvertTo-Json -Compress)) } }
    catch { Say "$u : pas de réponse ($($_.Exception.Message))" }
}

Section "GPU"
$Smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($Smi) { & nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu --format=csv,noheader | ForEach-Object { Say $_ } }
else { Say "nvidia-smi introuvable" }

Section "Démarrage automatique"
$Startup = [Environment]::GetFolderPath("Startup")
$Shell = New-Object -ComObject WScript.Shell
Get-ChildItem $Startup -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    $l = $Shell.CreateShortcut($_.FullName); Say ("raccourci {0} -> {1} {2}" -f $_.Name, $l.TargetPath, $l.Arguments)
}
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'ORION|JARVIS|Ollama|Transcription' } | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
    Say ("tâche « {0} » : {1} ; dernier lancement {2} (résultat {3})" -f $_.TaskName, $_.State, $i.LastRunTime, $i.LastTaskResult)
}
Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'orion|jarvis|ollama|stt' } | ForEach-Object { Say ("service {0} : {1} ({2})" -f $_.Name, $_.Status, $_.StartType) }

Section "Vestiges de l'agent V2 (C:\Users\theju\jarvis)"
$Jarvis = Join-Path $env:USERPROFILE "jarvis"
if (Test-Path $Jarvis) {
    Say "dossier présent (conservé volontairement par le retrait du 4 novembre)"
    if (Test-Path (Join-Path $Jarvis ".git")) { Say ("commit : " + ((git -C $Jarvis log -1 --format="%h %ad %s" --date=short 2>$null) | Out-String).Trim()) }
} else { Say "absent" }
$V2 = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like "*jarvis*winagent*" }
Say ("agent V2 en marche : " + $(if ($V2) { "OUI (pid " + (($V2 | ForEach-Object { $_.ProcessId }) -join ", ") + ")" } else { "non" }))
Say ("sauvegarde du retrait V2 : " + $(if (Test-Path (Join-Path $env:USERPROFILE "orion-retrait-agent-v2")) { "présente" } else { "absente" }))

Section "Pare-feu (règles ORION)"
Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'ORION|JARVIS|8875|8766|Ollama' } | ForEach-Object {
    $a = $_ | Get-NetFirewallAddressFilter; $pf = $_ | Get-NetFirewallPortFilter
    Say ("{0} : {1}, {2}, port {3}, depuis {4}" -f $_.DisplayName, $(if ($_.Enabled -eq 'True') { "active" } else { "inactive" }), $_.Action, $pf.LocalPort, ($a.RemoteAddress -join ","))
}

Section "Disques"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object { Say ("{0} libre {1:N0} Go sur {2:N0} Go" -f $_.DeviceID, ($_.FreeSpace / 1GB), ($_.Size / 1GB)) }

[IO.File]::WriteAllLines($Out, $Lines, (New-Object Text.UTF8Encoding $true))
Write-Host ""
Write-Host "Rapport écrit : $Out (aucun secret dedans)."
