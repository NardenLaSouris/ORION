# Installation et mise à jour de l'agent Windows V3 du Katana, depuis le PC fixe (docs/v3/reference_unique.md) :
#   powershell -ExecutionPolicy Bypass -File deploy\katana_install.ps1                  dernier commit de origin/v3
#   powershell -ExecutionPolicy Bypass -File deploy\katana_install.ps1 -Commit 6dd7411  commit V3 précis
# Même installation de référence que le PC (C:\Users\theju\.orion : releases\<commit>, current/previous, venv,
# agent.toml, data\.env) ; le jeton est celui des agents V3, lu dans l'installation du PC et jamais affiché.
# La copie et la vérification sont faites ici ; l'installation, la bascule et le retour arrière par
# deploy\katana_setup.ps1 sur le Katana.
param([string]$Commit = "origin/v3")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Katana = "theju@192.168.1.73"
$Key = Join-Path $env:USERPROFILE ".ssh\id_ed25519_katana"
git -C $Repo fetch -q origin v3
$Sha = (git -C $Repo rev-parse --short=12 "$Commit^{commit}").Trim()
git -C $Repo merge-base --is-ancestor $Sha origin/v3
if ($LASTEXITCODE) { throw "$Sha n'est pas un commit de la branche v3 (origin/v3) : refusé." }

$Tar = Join-Path $env:TEMP "orion-katana-$Sha.tar"
git -C $Repo archive -o $Tar $Sha orion deploy/katana_agent.toml deploy/katana_setup.ps1 deploy/pc_start.pyw requirements-pc.txt
$Remote = "C:/Users/theju/.orion/releases/$Sha"
ssh -i $Key $Katana "if not exist C:\Users\theju\.orion\releases\$Sha mkdir C:\Users\theju\.orion\releases\$Sha"
scp -q -i $Key $Tar "${Katana}:C:/Users/theju/.orion/releases/$Sha.tar"
ssh -i $Key $Katana "tar -xf C:\Users\theju\.orion\releases\$Sha.tar -C C:\Users\theju\.orion\releases\$Sha && del C:\Users\theju\.orion\releases\$Sha.tar"
Remove-Item $Tar

# Jeton des agents V3 (installation du PC) copié par un fichier temporaire, jamais dans une ligne de commande ni par
# l'entrée d'une commande (Windows PowerShell 5.1 la réencode : jeton déformé, agent qui refuse le Core).
$Line = Select-String -Path (Join-Path $env:USERPROFILE ".orion\data\.env") -Pattern '^ORION_V3_AGENT_TOKEN=(.+)$'
if (-not $Line) { throw "ORION_V3_AGENT_TOKEN absent de l'installation du PC." }
$EnvTmp = Join-Path $env:TEMP ("orion-katana-" + [guid]::NewGuid().ToString("N") + ".env")
try {
    [IO.File]::WriteAllText($EnvTmp, "ORION_V3_AGENT_TOKEN=$($Line.Matches[0].Groups[1].Value.Trim())`n", (New-Object Text.UTF8Encoding $false))
    ssh -i $Key $Katana "if not exist C:\Users\theju\.orion\data mkdir C:\Users\theju\.orion\data"
    scp -q -i $Key $EnvTmp "${Katana}:C:/Users/theju/.orion/data/.env"
} finally { Remove-Item $EnvTmp -ErrorAction SilentlyContinue }
ssh -i $Key $Katana "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/theju/.orion/releases/$Sha/deploy/katana_setup.ps1 -Sha $Sha"
if ($LASTEXITCODE) { throw "Installation sur le Katana en échec." }
