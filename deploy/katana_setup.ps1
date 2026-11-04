# Partie Katana de deploy\katana_install.ps1 (lancée par SSH, depuis la version copiée dans releases\<commit>) :
# environnement Python, configuration, bascule de version, raccourci de démarrage unique, lancement dans la session
# de l'utilisateur (tâche planifiée interactive temporaire : un processus lancé par SSH meurt avec la session),
# vérification, retour à la version précédente en cas d'échec.
param([Parameter(Mandatory = $true)][string]$Sha, [string]$Token = "")
$ErrorActionPreference = "Stop"
$Base = Join-Path $env:USERPROFILE ".orion"
$Release = Join-Path $Base "releases\$Sha"
$Data = Join-Path $Base "data"
$Venv = Join-Path $Base "venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"
$BasePython = "C:\Users\theju\AppData\Local\Programs\Python\Python313\python.exe"
$Port = 8875
New-Item -ItemType Directory -Force $Data | Out-Null

function Get-Agent {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*\.orion\*" -and ($_.CommandLine -like "*pc_start.pyw*" -or $_.CommandLine -like "*orion.winagent*") }
}
function Stop-Agent {
    Set-Content -Path (Join-Path $Data "supervise.stop") -Value "arrêt"
    for ($i = 0; $i -lt 30 -and (Get-Agent); $i++) { Start-Sleep -Milliseconds 500 }
    Get-Agent | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item (Join-Path $Data "supervise.stop") -ErrorAction SilentlyContinue
}
function Start-Agent {
    $action = New-ScheduledTaskAction -Execute $PythonW -Argument ('"{0}" agent' -f (Join-Path $Base "pc_start.pyw")) -WorkingDirectory $Base
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
    Register-ScheduledTask -TaskName "ORION Agent (lancement)" -Action $action -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName "ORION Agent (lancement)"
    Start-Sleep -Seconds 3
    Unregister-ScheduledTask -TaskName "ORION Agent (lancement)" -Confirm:$false
}
function Test-Agent {
    for ($i = 0; $i -lt 40; $i++) {
        if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if (-not (Test-Path $Python)) { & $BasePython -m venv $Venv }
$reqHash = (Get-FileHash (Join-Path $Release "requirements-pc.txt")).Hash
$reqDone = Join-Path $Venv "requirements-pc.sha256"
if (-not (Test-Path $reqDone) -or (Get-Content $reqDone).Trim() -ne $reqHash) {
    & $Python -m pip install -q --disable-pip-version-check -r (Join-Path $Release "requirements-pc.txt")
    if ($LASTEXITCODE) { throw "Dépendances non installées." }
    Set-Content -Path $reqDone -Value $reqHash
}
Push-Location $Release
try {
    & $Python -c "import orion.winagent.__main__, orion.winagent.supervise"
    if ($LASTEXITCODE) { throw "Contrôle des imports en échec : rien n'a été arrêté." }
} finally { Pop-Location }

$EnvFile = Join-Path $Data ".env"
if ($Token) {
    [IO.File]::WriteAllText($EnvFile, "ORION_V3_AGENT_TOKEN=$Token`n", (New-Object Text.UTF8Encoding $false))
}
if (-not (Test-Path $EnvFile)) { throw "Jeton de l'agent absent ($EnvFile)." }
$Current = if (Test-Path (Join-Path $Base "current")) { (Get-Content (Join-Path $Base "current")).Trim() } else { "" }
if (Test-Path (Join-Path $Base "agent.toml")) { Copy-Item (Join-Path $Base "agent.toml") (Join-Path $Base "agent.toml.precedent") -Force }
Copy-Item (Join-Path $Release "deploy\katana_agent.toml") (Join-Path $Base "agent.toml") -Force
Copy-Item (Join-Path $Release "deploy\pc_start.pyw") (Join-Path $Base "pc_start.pyw") -Force

Stop-Agent
if ($Current -ne $Sha) {
    if ($Current) { Set-Content -Path (Join-Path $Base "previous") -Value $Current }
    Set-Content -Path (Join-Path $Base "current") -Value $Sha
}
Start-Agent
if (-not (Test-Agent)) {
    if ($Current -and $Current -ne $Sha) {
        Stop-Agent
        Set-Content -Path (Join-Path $Base "current") -Value $Current
        Copy-Item (Join-Path $Base "agent.toml.precedent") (Join-Path $Base "agent.toml") -Force
        Start-Agent
        throw "L'agent $Sha n'écoute pas : version $Current remise en service."
    }
    throw "L'agent $Sha n'écoute pas sur le port $Port (voir $Data\agent.log)."
}

# Démarrage de session : un seul raccourci « ORION Agent ».
$Startup = [Environment]::GetFolderPath("Startup")
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut((Join-Path $Startup "ORION Agent.lnk"))
$Link.TargetPath = $PythonW
$Link.Arguments = '"{0}" agent' -f (Join-Path $Base "pc_start.pyw")
$Link.WorkingDirectory = $Base
$Link.WindowStyle = 7
$Link.Description = "ORION : agent Windows V3 du Katana (port 8875)"
$Link.Save()
"Agent V3 du Katana en service : $Sha (port $Port)"
