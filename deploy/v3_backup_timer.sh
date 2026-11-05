#!/bin/bash
# Sauvegarde de la nuit du Core V3 : installation SUR LE CORE, compte user, sans sudo.
#   ssh user@192.168.1.91 "bash /opt/orion-v3/deploy/v3_backup_timer.sh"              installe et active
#   ssh user@192.168.1.91 "bash /opt/orion-v3/deploy/v3_backup_timer.sh --maintenant"  idem, plus une sauvegarde tout de suite
#   ssh user@192.168.1.91 "bash /opt/orion-v3/deploy/v3_backup_timer.sh --retirer"     retire la minuterie (sauvegardes gardées)
#
# Chaque nuit à 03:15 : scripts/v3_backup.py periodic (sauvegarde vérifiée dans ~/sauvegardes/orion-v3/quotidiennes,
# 14 gardées ; une par semaine recopiée dans hebdomadaires, 8 gardées ; état data/backup_status.json lu par SENTINEL).
# Aucune sauvegarde n'est jamais effacée par ce script ; seule la rotation du programme de sauvegarde en retire.
set -euo pipefail
[ "$(id -u)" != 0 ] || { echo "À lancer sans sudo, avec le compte user." >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
UNITS="$HOME/.config/systemd/user"
NAME=orion-v3-backup

case "${1:-}" in
  --retirer)
    systemctl --user disable --now "$NAME.timer" 2>/dev/null || true
    rm -f "$UNITS/$NAME.timer" "$UNITS/$NAME.service"
    systemctl --user daemon-reload
    rm -f /opt/orion-v3/data/backup_status.json  # état seul : SENTINEL considère alors la sauvegarde non installée
    echo "Minuterie retirée ; sauvegardes gardées dans ~/sauvegardes/orion-v3."
    exit 0 ;;
  ""|--maintenant) ;;
  *) echo "Option inconnue : $1 (--maintenant, --retirer)" >&2; exit 2 ;;
esac

[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ] \
  || echo "Attention : linger inactif pour $USER, la minuterie ne tournera que session ouverte (sudo loginctl enable-linger $USER)." >&2
install -d -m 700 "$UNITS" "$HOME/sauvegardes/orion-v3"
install -m 644 "$HERE/$NAME.service" "$HERE/$NAME.timer" "$UNITS/"
systemctl --user daemon-reload
systemctl --user enable --now "$NAME.timer"
if [ "${1:-}" = --maintenant ]; then
  echo "Sauvegarde en cours…"
  systemctl --user start "$NAME.service" || true
  journalctl --user -u "$NAME.service" -n 3 --no-pager -o cat || true
fi
systemctl --user list-timers "$NAME.timer" --no-pager
