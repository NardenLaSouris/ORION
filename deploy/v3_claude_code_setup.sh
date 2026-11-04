#!/bin/bash
# Claude Code pour EVOLVE : jeton de connexion SUR LE CORE (docs/v3/evolve.md, « Consulter Claude Code »).
#   1. sur le PC (ou n'importe où avec votre compte Claude) : claude setup-token   -> jeton longue durée affiché
#   2. ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_claude_code_setup.sh"             saisir, activer
#      ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_claude_code_setup.sh --desactiver"  couper
#      ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_claude_code_setup.sh --oublier"     effacer le jeton
# Le jeton n'est jamais affiché, ni passé en argument, ni écrit ailleurs que dans /opt/orion-v3/.env (640, user,
# groupe orion-v3) ; EVOLVE le passe au seul processus Claude Code, jamais au journal ni à ORION Control.
set -euo pipefail
ENV=/opt/orion-v3/.env
CONF=/opt/orion-v3/config.local.toml
KEY=CLAUDE_CODE_OAUTH_TOKEN
[ -w "$ENV" ] && [ -w "$CONF" ] || { echo "À lancer sous le compte user du Core (fichiers $ENV et $CONF)." >&2; exit 1; }
[ -x /opt/orion-v3-claude/claude ] || [ "${1:-}" != "" ] || {
    echo "Claude Code n'est pas installé : sudo bash /home/user/orion-v3/deploy/v3_claude_code_root.sh" >&2; exit 1; }
stamp=$(date +%Y%m%d-%H%M%S)

set_enabled() {  # [evolve] claude_code = true|false, le reste de [evolve] gardé ; configuration sauvegardée avant
    cp -a "$CONF" "$CONF.avant-claude-code-$stamp"
    python3 - "$CONF" "$1" <<'EOF'
import re, sys, tomllib
from pathlib import Path
path, value = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
section = re.search(r"(?ms)^\[evolve\]\n(.*?)(?=^\[|\Z)", text)
if section is None:
    text = text.rstrip() + f"\n\n[evolve]\nenabled = true\nclaude_code = {value}\n"
else:
    body = re.sub(r"(?m)^claude_code\s*=.*\n?", "", section.group(1)).rstrip("\n")
    body = (body + "\n" if body else "") + f"claude_code = {value}\n\n"
    text = text[:section.start(1)] + body + text[section.end(1):]
tomllib.loads(text)
path.write_text(text.rstrip() + "\n", encoding="utf-8")
EOF
}

remove_secret() {
    cp -a "$ENV" "$ENV.avant-claude-code-$stamp"
    grep -v "^$KEY=" "$ENV" > "$ENV.tmp" || true
    cat "$ENV.tmp" > "$ENV" && rm -f "$ENV.tmp"   # même fichier : propriétaire et droits gardés
}

case "${1:-}" in
    --desactiver) set_enabled false; sudo -n systemctl restart orion-v3; echo "Claude Code coupé pour EVOLVE (jeton gardé)."; exit 0 ;;
    --oublier) set_enabled false; remove_secret; sudo -n systemctl restart orion-v3
               echo "Jeton effacé (copie de sûreté : $ENV.avant-claude-code-$stamp, à supprimer vous-même)."
               echo "Pensez à le révoquer aussi depuis votre compte Claude."; exit 0 ;;
esac

echo "Collez le jeton affiché par « claude setup-token » (rien ne s'affiche), puis Entrée."
IFS= read -rs token
echo
token=$(printf '%s' "$token" | tr -d '\r\n[:space:]')
if ! printf '%s' "$token" | grep -Eq '^sk-ant-oat[0-9]{2}-[A-Za-z0-9_-]{20,400}$'; then
    unset token
    echo "Ce n'est pas un jeton de « claude setup-token » (sk-ant-oat…)." >&2
    exit 1
fi
remove_secret
printf '%s=%s\n' "$KEY" "$token" >> "$ENV"
unset token
echo "Jeton enregistré dans $ENV (jamais affiché)."
set_enabled true
sudo -n systemctl restart orion-v3
echo "EVOLVE est relié à Claude Code : ORION Control, vue « Évolution », bouton « Demander à Claude Code »."
echo "Il consulte aussi de lui-même après une nouvelle anomalie logicielle (6 consultations par 24 heures au plus)."
