# Instance d'ESSAI de Frag Factory pour la V3 : données fictives seulement (REC synthétiques de l'auto-test), port
# 8752, dossier %LOCALAPPDATA%\ORION V3 Agent\fragfactory-essai. Votre vraie instance (port 8742, vos
# enregistrements) n'est ni lue ni modifiée.
#   deploy\v3_fragfactory_essai.ps1             lance (crée l'instance et un premier clip au premier lancement)
#   deploy\v3_fragfactory_essai.ps1 -Clip       produit un nouveau clip (auto-test : REC synthétique -> rendu 9:16)
#   deploy\v3_fragfactory_essai.ps1 -Stop       arrête l'instance d'essai
param([switch]$Clip, [switch]$Stop)
$ErrorActionPreference = "Stop"
$FFRoot = "C:\Users\theju\AppData\Roaming\Claude\scratch-workspaces\088b7887-957e-4849-9a79-bd6095b9d58e\5e9af580-f6cd-44f0-b10d-1e0f1c490b2b\scratch-2026-11-02-9bb6ed"
$FFPython = "C:\Users\theju\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\FragFactory\venv\Scripts\python.exe"
$Agent = Join-Path $env:LOCALAPPDATA "ORION V3 Agent"
$Home_ = Join-Path $Agent "fragfactory-essai"
$Config = Join-Path $Home_ "ff.toml"
$Port = 8752

$PidFile = Join-Path $Home_ "serve.pid"
# Arrêt de l'instance d'essai seulement : processus noté au lancement (jamais celui de votre vraie instance).
if (Test-Path $PidFile) {
    $Old = Get-Content $PidFile
    Get-CimInstance Win32_Process -Filter "ProcessId = $Old OR ParentProcessId = $Old" |
        Where-Object { $_.CommandLine -like "*fragfactory serve*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item $PidFile
}
if ($Stop) { return }

New-Item -ItemType Directory -Force $Home_ | Out-Null
$Data = (Join-Path $Home_ "data").Replace([char]92, [char]47)
$Toml = @"
# Instance d'essai de Frag Factory pour ORION V3 (donnees fictives).
[paths]
data_dir = "$Data"
watch_dirs = []

[server]
port = $Port

[analysis]
stt_enabled = false

[ai]
provider = "heuristic"

[render]
auto_render_tiers = []

[orion]
enabled = true
public_url = "http://127.0.0.1:$Port"
"@
# UTF-8 sans BOM : Set-Content -Encoding UTF8 de Windows PowerShell en ajoute un, que le lecteur TOML refuse.
[IO.File]::WriteAllText($Config, $Toml, (New-Object Text.UTF8Encoding $false))

# Jeton de liaison : celui de l'agent V3 (FRAGFACTORY_LINK_TOKEN), créé au premier lancement.
$EnvFile = Join-Path $Agent ".env"
$Line = Select-String -Path $EnvFile -Pattern '^FRAGFACTORY_LINK_TOKEN=(.+)$' -ErrorAction SilentlyContinue
if (-not $Line) {
    $Bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($Bytes)
    $New = [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Add-Content -Path $EnvFile -Value "FRAGFACTORY_LINK_TOKEN=$New"
    $Line = Select-String -Path $EnvFile -Pattern '^FRAGFACTORY_LINK_TOKEN=(.+)$'
}
$env:ORION_LINK_TOKEN = $Line.Matches[0].Groups[1].Value
$env:FRAGFACTORY_CONFIG = $Config
$env:PYTHONIOENCODING = "utf-8"

if ($Clip -or -not (Test-Path (Join-Path $Home_ "data\fragfactory.sqlite3"))) {
    $Test = Start-Process -FilePath $FFPython -WorkingDirectory $FFRoot -WindowStyle Hidden -Wait -PassThru `
        -ArgumentList "-m fragfactory selftest" -RedirectStandardOutput (Join-Path $Home_ "selftest.log") `
        -RedirectStandardError (Join-Path $Home_ "selftest.err")
    if ($Test.ExitCode -ne 0) { throw "Auto-test de Frag Factory en échec (voir selftest.err)" }
}
$Proc = Start-Process -FilePath $FFPython -WorkingDirectory $FFRoot -WindowStyle Hidden -PassThru `
    -ArgumentList "-m fragfactory serve" -RedirectStandardError (Join-Path $Home_ "serve.log")
Set-Content -Path $PidFile -Value $Proc.Id
