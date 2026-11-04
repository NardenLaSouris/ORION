#!/bin/bash
# Déploiement des changements d'EVOLVE (docs/v3/evolve.md, « Mode autonome ») : préparation SUR LE CORE.
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_evolve_root.sh"
#
# Depuis le 4 novembre 2026 (feuille de route : « un déploiement n'est possible qu'après mon intervention et mon
# approbation »), plus aucun déclenchement automatique : ce script RETIRE l'ancien déclencheur (orion-v3-evolve.path,
# qui lançait le déploiement dès qu'ORION déposait une demande) et son service, et garde le dossier des demandes.
# Le déploiement est lancé par vous, dans un terminal, après « Approuver » dans ORION Control :
#   ssh -t user@192.168.1.91 "bash /opt/orion-v3/deploy/v3_evolve_runner.sh"
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
UNIT=/etc/systemd/system

systemctl disable --now orion-v3-evolve.path 2>/dev/null || true
systemctl stop orion-v3-evolve.service 2>/dev/null || true
rm -f "$UNIT/orion-v3-evolve.path" "$UNIT/orion-v3-evolve.service"
systemctl daemon-reload
install -d -o orion-v3 -g orion-v3 -m 2770 /opt/orion-v3/data/evolve /opt/orion-v3/data/evolve/deploy
echo "Déclencheur automatique retiré : $(systemctl list-unit-files 'orion-v3-evolve*' --no-legend | wc -l) unité restante."
echo "Déploiement d'un changement approuvé : bash /opt/orion-v3/deploy/v3_evolve_runner.sh (dans un terminal)."
