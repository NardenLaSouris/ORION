#!/bin/bash
# Claude Code pour EVOLVE (docs/v3/evolve.md, « Consulter Claude Code ») : installation du programme SUR LE CORE.
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_claude_code_root.sh"
#
# 1. installe Claude Code pour le compte user avec l'installateur officiel d'Anthropic (https://claude.ai/install.sh),
#    s'il n'est pas déjà là ;
# 2. copie le programme dans /opt/orion-v3-claude/claude (root, groupe orion-v3, 0750) : le service orion-v3 ne voit
#    pas /home (ProtectHome) et ne peut pas le modifier ; mises à jour automatiques coupées (relancer ce script pour
#    mettre à jour) ;
# 3. vérifie que le compte orion-v3 peut le lancer.
# Aucun jeton ici : il se saisit ensuite avec deploy/v3_claude_code_setup.sh (compte user, sans sudo).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
OWNER=${SUDO_USER:-user}
HOME_OWNER=$(getent passwd "$OWNER" | cut -d: -f6)
DEST=/opt/orion-v3-claude

if [ ! -x "$HOME_OWNER/.local/bin/claude" ]; then
    echo "Installation de Claude Code pour $OWNER (installateur officiel d'Anthropic)…"
    sudo -u "$OWNER" -H bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
fi
src=$(readlink -f "$HOME_OWNER/.local/bin/claude")
[ -x "$src" ] || { echo "Programme Claude Code introuvable après l'installation." >&2; exit 1; }

install -d -o root -g orion-v3 -m 0750 "$DEST"
install -o root -g orion-v3 -m 0750 "$src" "$DEST/claude.new"
mv -f "$DEST/claude.new" "$DEST/claude"
install -d -o orion-v3 -g orion-v3 -m 2770 /opt/orion-v3/data/evolve /opt/orion-v3/data/evolve/claude-home

version=$(sudo -u orion-v3 env HOME=/opt/orion-v3/data/evolve/claude-home DISABLE_AUTOUPDATER=1 "$DEST/claude" --version)
echo "Claude Code prêt pour EVOLVE : $version ($DEST/claude)."
echo "Étape suivante (compte user, sans sudo) : bash /home/user/orion-v3/deploy/v3_claude_code_setup.sh"
