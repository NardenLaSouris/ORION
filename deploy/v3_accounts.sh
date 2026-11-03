#!/usr/bin/env bash
# Comptes séparés de l'environnement V3 (lot F5, roadmap.md § 2.6) : orion-import et orion-evolve.
#
#   sudo bash /home/user/orion-v3/deploy/v3_accounts.sh
#
# NON INSTALLÉ : ce script attend la décision du propriétaire. Il est idempotent, ne touche ni à /opt/orion ni au
# service orion, et vérifie à la fin que les deux comptes ne lisent ni la base personnelle, ni la clé, ni les autres
# données de la V3.
#
# - orion-import : écrit seulement dans /var/lib/orion-v3/staging/import (zone d'attente lue par orion-v3) ;
# - orion-evolve : écrit seulement dans /var/lib/orion-v3/staging/evolve et dans son espace /var/lib/orion-v3/evolve.
# Aucun des deux n'appartient au groupe orion-v3 ; orion-v3 lit leurs zones par le groupe de chaque zone.
set -euo pipefail

BASE=/var/lib/orion-v3
DATA=/opt/orion-v3/data

[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
id orion-v3 >/dev/null 2>&1 || { echo "Compte orion-v3 absent : lancer d'abord deploy/v3_env.sh." >&2; exit 1; }

for account in orion-import orion-evolve; do
  id "$account" >/dev/null 2>&1 || useradd --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin "$account"
  for group in orion orion-v3 orion-call sudo adm docker systemd-journal; do
    if id -nG "$account" | tr ' ' '\n' | grep -qx "$group"; then
      echo "$account est dans le groupe $group : arrêt." >&2; exit 1
    fi
  done
done

# Zones d'attente : le compte dépose (écriture), orion-v3 lit et range (groupe de la zone, setgid).
install -d -o root -g root -m 755 "$BASE" "$BASE/staging"
for zone in import evolve; do
  account="orion-$zone"
  install -d -o "$account" -g "$account" -m 2770 "$BASE/staging/$zone"
  install -d -o orion-v3 -g "$account" -m 2770 "$BASE/staging/$zone/done" "$BASE/staging/$zone/quarantine"
done
install -d -o orion-evolve -g orion-evolve -m 700 "$BASE/evolve"
usermod -aG orion-import,orion-evolve orion-v3  # lecture des zones par le Core (et seulement d'elles)
# Le service orion-v3 (ProtectSystem=strict) range les lots traités : écriture dans les zones seulement.
install -d -o root -g root -m 755 /etc/systemd/system/orion-v3.service.d
printf '[Service]\nReadWritePaths=%s/staging\n' "$BASE" > /etc/systemd/system/orion-v3.service.d/staging.conf
systemctl daemon-reload

echo "Contrôle"
fail=0
check() {  # $1 compte, $2 chemin : jamais lisible
  if sudo -u "$1" test -r "$2"; then echo "   $1 lit $2 : REFUSÉ attendu" >&2; fail=1; else echo "   $1 ne lit pas $2"; fi
}
for account in orion-import orion-evolve; do
  check "$account" "$DATA"
  check "$account" "$DATA/orion_personal.db"
  check "$account" /opt/orion-v3/.env
  check "$account" /etc/credstore.encrypted
  check "$account" /opt/orion
done
check orion-import "$BASE/staging/evolve"
check orion-evolve "$BASE/staging/import"
[ "$fail" = 0 ] || { echo "Isolation des comptes incomplète." >&2; exit 1; }
echo "Terminé. Redémarrer orion-v3 pour qu'il prenne ses nouveaux groupes : sudo systemctl restart orion-v3"
