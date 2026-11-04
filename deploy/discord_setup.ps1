# Liaison de Discord à ORION (docs/v3/discord.md § 5), une fois. À lancer vous-même sur le PC :
#   powershell -ExecutionPolicy Bypass -File C:\Users\theju\orion-v3\deploy\discord_setup.ps1
# 1. ouvre le Developer Portal (application « ORION », redirection http://localhost) ;
# 2. demande le Client ID et le Client Secret (le secret n'est jamais affiché ni journalisé) ;
# 3. les écrit dans C:\Users\theju\.orion\data\.env (jamais dans le dépôt) et relance l'agent ;
# 4. il ne reste qu'à cliquer « Relier ORION à Discord » dans ORION Control, puis « Autoriser » dans Discord.
$ErrorActionPreference = "Stop"
$Base = Join-Path $env:USERPROFILE ".orion"
$EnvFile = Join-Path $Base "data\.env"
if (-not (Test-Path $EnvFile)) { throw "Fichier $EnvFile absent : installez d'abord ORION (deploy\pc_install.ps1)." }

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
# relance : le superviseur arrête l'agent sur demande, puis le raccourci le redémarre avec les nouveaux réglages
Set-Content -Path (Join-Path $Base "data\supervise.stop") -Value "relance"
Start-Sleep -Seconds 8
Remove-Item -Path (Join-Path $Base "data\supervise.stop") -ErrorAction SilentlyContinue
Start-Process explorer.exe -ArgumentList ('"{0}"' -f (Join-Path $Base "ORION Agent.lnk"))
Write-Host ""
Write-Host "Étape 4 : dans ORION Control, vue Discord : « Relier ORION à Discord »,"
Write-Host "          puis « Autoriser » dans la fenêtre que Discord affiche."
