#!/bin/bash
# Service de déploiement d'EVOLVE autonome (docs/v3/evolve.md, « Mode autonome ») : installation SUR LE CORE.
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_evolve_root.sh"            installer
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_evolve_root.sh --retirer"  désinstaller
#
# orion-v3-evolve.path surveille /opt/orion-v3/data/evolve/deploy/request.json (déposé par ORION) et lance
# orion-v3-evolve.service : /opt/orion-v3/deploy/v3_evolve_runner.sh sous le compte user (celui qui déploie déjà),
# jamais sous orion-v3. ORION ne reçoit aucun droit nouveau : elle dépose une demande, le service juge et déploie.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
OWNER=${SUDO_USER:-user}
UNIT=/etc/systemd/system

if [ "${1:-}" = "--retirer" ]; then
    systemctl disable --now orion-v3-evolve.path 2>/dev/null || true
    rm -f "$UNIT/orion-v3-evolve.path" "$UNIT/orion-v3-evolve.service"
    systemctl daemon-reload
    echo "Service de déploiement d'EVOLVE retiré (les demandes restent sans effet)."
    exit 0
fi

install -d -o orion-v3 -g orion-v3 -m 2770 /opt/orion-v3/data/evolve /opt/orion-v3/data/evolve/deploy

cat > "$UNIT/orion-v3-evolve.service" <<EOF
[Unit]
Description=ORION V3 : déploiement des changements d'EVOLVE autonome (contrôle, tests, déploiement, retour arrière)
After=orion-v3.service

[Service]
Type=oneshot
User=$OWNER
Group=$OWNER
Environment=HOME=$(getent passwd "$OWNER" | cut -d: -f6)
ExecStart=/bin/bash /opt/orion-v3/deploy/v3_evolve_runner.sh
TimeoutStartSec=45min
Nice=10
EOF

cat > "$UNIT/orion-v3-evolve.path" <<'EOF'
[Unit]
Description=ORION V3 : demandes de déploiement d'EVOLVE autonome

[Path]
PathExists=/opt/orion-v3/data/evolve/deploy/request.json
Unit=orion-v3-evolve.service

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now orion-v3-evolve.path
echo "Service prêt : $(systemctl is-active orion-v3-evolve.path) (orion-v3-evolve.path)."
echo "Activer le mode autonome : ORION Control, vue « Évolution » (niveau 2 et bouton « Mode autonome »)."
