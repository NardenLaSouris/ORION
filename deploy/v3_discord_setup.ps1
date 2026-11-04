# Liaison de Discord à ORION V3, en une fois (docs/v3/discord.md § 5). À lancer vous-même sur le PC :
#   powershell -ExecutionPolicy Bypass -File C:\Users\theju\orion-v3\deploy\v3_discord_setup.ps1
# 1. ouvre le Developer Portal (créez l'application « ORION », ajoutez la redirection http://localhost) ;
# 2. demande le Client ID et le Client Secret (le secret n'est jamais affiché ni journalisé) ;
# 3. les écrit dans %LOCALAPPDATA%\ORION V3 Agent\.env (jamais dans le dépôt) et relance l'agent V3 ;
# 4. il ne reste qu'à cliquer « Relier ORION à Discord » dans ORION Control V3, puis « Autoriser » dans Discord.
$ErrorActionPreference = "Stop"
$EnvFile = Join-Path $env:LOCALAPPDATA "ORION V3 Agent\.env"
$Launcher = "C:\Users\theju\orion-v3-agent\deploy\v3_agent.ps1"   # copie dédiée de l'agent, jamais la copie de travail
if (-not (Test-Path $Launcher)) { $Launcher = Join-Path $PSScriptRoot "v3_agent.ps1" }

# Installé depuis l'application Claude (paquet isolé), l'agent a écrit ses fichiers dans le stockage propre au paquet
# (Packages\Claude_*\LocalCache\Local) : invisibles ici et au démarrage de session. On les ramène au vrai dossier.
if (-not (Test-Path $EnvFile)) {
    $Isolated = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "LocalCache\Local\ORION V3 Agent\.env" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Isolated) { throw "Fichier $EnvFile absent : l'agent V3 n'est pas installé." }
    Write-Host "Fichiers de l'agent V3 trouvés dans le stockage de l'application Claude : déplacement vers $EnvFile"
    & $Launcher -Stop
    New-Item -ItemType Directory -Force (Split-Path -Parent $EnvFile) | Out-Null
    Copy-Item $Isolated $EnvFile
    # mis de côté, pas effacé : sinon il masquerait le vrai fichier pour les programmes lancés depuis Claude
    Rename-Item $Isolated (".env.deplace-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}

Write-Host "Étape 1 : dans la page qui s'ouvre, « New Application » (nom : ORION), puis OAuth2 > Redirects :"
Write-Host "          ajoutez exactement http://localhost et enregistrez. Aucun bot n'est nécessaire."
Start-Process "https://discord.com/developers/applications"
$Id = (Read-Host "Étape 2 : Client ID (page OAuth2)").Trim()
if ($Id -notmatch '^\d{15,22}$') { throw "Client ID invalide : uniquement des chiffres (15 à 22)." }
$Secure = Read-Host "Étape 3 : Client Secret (Reset Secret s'il n'est pas affiché)" -AsSecureString
$Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
try { $Secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr) }
if ($Secret.Length -lt 16) { throw "Client Secret trop court." }

$Lines = @(Get-Content $EnvFile | Where-Object { $_ -notmatch '^DISCORD_CLIENT_(ID|SECRET)=' })
$Lines += "DISCORD_CLIENT_ID=$Id"
$Lines += "DISCORD_CLIENT_SECRET=$Secret"
[IO.File]::WriteAllLines($EnvFile, [string[]]$Lines, (New-Object Text.UTF8Encoding $false))
$Secret = $null
& $Launcher
Write-Host ""
Write-Host "Étape 4 : dans ORION Control V3, vue Discord : « Relier ORION à Discord »,"
Write-Host "          puis « Autoriser » dans la fenêtre que Discord affiche."
