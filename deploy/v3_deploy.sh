#!/usr/bin/env bash
# Déploiement de la V3, et seulement de la V3 (mini-PC, compte user) :
#
#   bash /opt/orion-v3/deploy/v3_deploy.sh [référence]     (référence par défaut : v3-sync de ~/orion-dev)
#
# 1. garde-fous : cible, service, compte et ports codés en dur ; tout écart avec la production arrête tout ;
# 2. sauvegarde vérifiée des données V3 (scripts/v3_backup.py) ;
# 3. avance rapide seulement (git merge --ff-only) depuis le miroir ~/orion-dev ;
# 4. redémarrage d'orion-v3 (sudo limité à ce service) et contrôle de santé ;
# 5. échec du contrôle : retour automatique au commit précédent, redémarrage, code de sortie 1.
# La production (/opt/orion, service orion) n'est jamais lue en écriture ni redémarrée ; son heure de démarrage est
# comparée avant et après pour le prouver.
set -euo pipefail

TARGET=/opt/orion-v3
SERVICE=orion-v3
ACCOUNT=orion-v3
SOURCE=$HOME/orion-dev
REF=${1:-v3-sync}
PROD_PORTS="8765 8766 8767 8770"
LOG=$HOME/sauvegardes/orion-v3/deploiements.log
PY=$TARGET/.venv/bin/python
HERE=$(cd "$(dirname "$0")/.." && pwd)  # copie qui porte ce script (le premier déploiement n'a pas encore les outils)

die() { echo "ARRÊT : $*" >&2; exit 2; }

# --- 1. Garde-fous -----------------------------------------------------------------------------------------------
[ "$(id -un)" = user ] || die "à lancer sous le compte user"
[ "$(realpath "$TARGET")" = /opt/orion-v3 ] || die "cible inattendue $(realpath "$TARGET")"
[ "$(systemctl show "$SERVICE" -p WorkingDirectory --value)" = "$TARGET" ] || die "$SERVICE ne tourne pas dans $TARGET"
[ "$(systemctl show "$SERVICE" -p User --value)" = "$ACCOUNT" ] || die "$SERVICE ne tourne pas sous $ACCOUNT"
[ "$(git -C "$TARGET" rev-parse --show-toplevel)" = "$TARGET" ] || die "$TARGET n'est pas un dépôt"
for port in $PROD_PORTS; do
  if grep -Eq "^[[:space:]]*port[[:space:]]*=[[:space:]]*$port([^0-9]|$)" "$TARGET/config.local.toml"; then
    die "config.local.toml de la V3 utilise le port de production $port"
  fi
done
if [ -n "$(git -C "$TARGET" status --porcelain --untracked-files=no)" ]; then
  die "modifications locales dans $TARGET (git status)"
fi
prod_started=$(systemctl show orion -p ActiveEnterTimestampMonotonic --value)

before=$(git -C "$TARGET" rev-parse HEAD)
git -C "$TARGET" fetch -q "$SOURCE" "$REF"
after=$(git -C "$TARGET" rev-parse FETCH_HEAD)
git -C "$TARGET" merge-base --is-ancestor "$before" "$after" || die "$REF ne descend pas de $before (avance rapide seulement)"

# --- 2. Sauvegarde -----------------------------------------------------------------------------------------------
mkdir -p "$(dirname "$LOG")"
"$PY" "$HERE/scripts/v3_backup.py" backup --data "$TARGET/data" --out "$HOME/sauvegardes/orion-v3" --keep 14

# --- 3. Code ------------------------------------------------------------------------------------------------------
git -C "$TARGET" merge -q --ff-only FETCH_HEAD
if [ -f /tmp/production_stats.json ]; then  # relevé de production (fond d'écran), préparé sur le PC
  install -m 640 /tmp/production_stats.json "$TARGET/data/production_stats.json" && rm -f /tmp/production_stats.json
fi

# --- 4. Redémarrage et santé ---------------------------------------------------------------------------------------
healthy() {
  local since=$1
  for _ in $(seq 1 45); do
    sleep 2
    systemctl is-active --quiet "$SERVICE" || continue
    # Journal lu une fois : « journalctl | grep -q » échoue sous pipefail (SIGPIPE) dès que grep a trouvé.
    local journal
    journal=$(journalctl -u "$SERVICE" --since "$since" --no-pager -q 2>/dev/null || true)
    case "$journal" in *Traceback*) return 1 ;; esac
    case "$journal" in
      *"Modules V3 activés"*)
        [ "$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8865/)" = 200 ] && return 0 ;;
    esac
  done
  return 1
}

restart_and_check() {
  local since
  since=$(date '+%Y-%m-%d %H:%M:%S')
  sudo -n systemctl restart "$SERVICE"
  healthy "$since"
}

status=ok
if [ "$before" = "$after" ]; then
  echo "Déjà à jour ($after) : rien à redémarrer."
elif restart_and_check; then
  echo "V3 déployée : ${before:0:7} -> ${after:0:7}"
else
  status="échec, retour à ${before:0:7}"
  echo "Contrôle de santé en échec : retour à $before" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager -q >&2 || true
  git -C "$TARGET" reset -q --hard "$before"
  restart_and_check || echo "ATTENTION : la version précédente ne repart pas non plus" >&2
fi

[ "$(systemctl show orion -p ActiveEnterTimestampMonotonic --value)" = "$prod_started" ] \
  || echo "ATTENTION : la production a redémarré pendant le déploiement (sans lien avec ce script ?)" >&2
echo "$(date -Is) ${before:0:7} -> ${after:0:7} : $status" >> "$LOG"
[ "$status" = ok ]
