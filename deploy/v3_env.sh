#!/usr/bin/env bash
# Environnement de développement V3 isolé sur le mini-PC (docs/v3/environnements.md).
#
#   sudo bash /home/user/orion-v3/deploy/v3_env.sh
#
# Crée le compte orion-v3, /opt/orion-v3 (branche v3-sync de ~/orion-dev), sa configuration, son jeton, le service
# orion-v3 et le droit pour « user » de le redémarrer. Ne touche ni à /opt/orion, ni au service orion, ni à leurs
# comptes : la production est seulement lue (copie de l'environnement Python et des modèles). S'arrête sans rien
# modifier si /opt/orion-v3 existe déjà.
set -euo pipefail

SRC=/home/user/orion-dev
REF=v3-sync
DEST=/opt/orion-v3
PROD=/opt/orion
OWNER=user
ACCOUNT=orion-v3
PC=192.168.1.128
KATANA=192.168.1.73

[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
[ -e "$DEST" ] && { echo "$DEST existe déjà : rien n'est modifié." >&2; exit 1; }
cd /
sudo -u "$OWNER" git -C "$SRC" rev-parse --verify -q "$REF" >/dev/null || { echo "Branche $REF absente de $SRC." >&2; exit 1; }

echo "1. Compte $ACCOUNT"
if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$ACCOUNT"
fi
for group in orion orion-call ollama docker sudo adm; do
  if id -nG "$ACCOUNT" | tr ' ' '\n' | grep -qx "$group"; then
    echo "Le compte $ACCOUNT est dans le groupe $group : isolation impossible, arrêt." >&2; exit 1
  fi
done
usermod -aG "$ACCOUNT" "$OWNER"  # lecture des données de test et des journaux par l'administrateur

echo "2. Code ($REF) dans $DEST"
install -d -o "$OWNER" -g "$ACCOUNT" -m 750 "$DEST"
sudo -u "$OWNER" git clone -q --branch "$REF" "$SRC" "$DEST"

echo "3. Environnement Python et modèles (copies de la production, aucun lien)"
cp -a "$PROD/.venv" "$DEST/.venv"
install -d "$DEST/models" && cp -a "$PROD/models/." "$DEST/models/"

echo "4. Données, configuration et jeton propres"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 2770 "$DEST/data"
sed -e "s/__PC__/$PC/g" -e "s/__KATANA__/$KATANA/g" "$DEST/deploy/v3_env.toml" > "$DEST/config.local.toml"
umask 077
printf 'ORION_AGENT_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > "$DEST/.env"
umask 022
chown -R "$OWNER:$ACCOUNT" "$DEST"
chown -R "$ACCOUNT:$ACCOUNT" "$DEST/data"
chown -R "$ACCOUNT:$ACCOUNT" "$DEST/models/whisper"
chmod -R o-rwx "$DEST"
chmod 640 "$DEST/.env" "$DEST/config.local.toml"

echo "5. Service orion-v3"
cat > /etc/systemd/system/orion-v3.service <<UNIT
[Unit]
Description=ORION V3, environnement de développement isolé (compte orion-v3, aucune donnée de production)
After=network-online.target
Wants=network-online.target

[Service]
User=$ACCOUNT
Group=$ACCOUNT
WorkingDirectory=$DEST
ExecStart=$DEST/.venv/bin/python -m orion
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Paris HF_HUB_OFFLINE=1
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=$DEST/data $DEST/models/whisper
InaccessiblePaths=-$PROD -/etc/orion-call -/var/lib/orion -/var/lib/orion-call
# Clé des données personnelles (lot F4), si elle existe un jour : /etc/credstore.encrypted/orion-v3.data_key
# (systemd-creds encrypt). Absente : la base personnelle reste verrouillée, le service démarre quand même.
ImportCredential=orion-v3.data_key
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallArchitectures=native
UMask=0007

[Install]
WantedBy=multi-user.target
UNIT

echo "6. Droit de redémarrage pour $OWNER (ce service seulement)"
RULES=$(mktemp)
echo "$OWNER ALL=(root) NOPASSWD: /usr/bin/systemctl restart orion-v3, /usr/bin/systemctl stop orion-v3, /usr/bin/systemctl start orion-v3, /usr/bin/systemctl status orion-v3" > "$RULES"
visudo -cqf "$RULES"
install -o root -g root -m 440 "$RULES" /etc/sudoers.d/orion-v3
rm -f "$RULES"

echo "7. Pare-feu : ports 8865 à 8867 ouverts au PC et au Katana seulement"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  for ip in "$PC" "$KATANA"; do
    ufw allow proto tcp from "$ip" to any port 8865:8867 comment "ORION V3" >/dev/null
  done
  echo "   règles ufw ajoutées."
else
  echo "   ufw inactif : aucune règle ajoutée."
fi

echo "8. Contrôle de l'isolation"
if sudo -u "$ACCOUNT" test -r "$PROD/data" || sudo -u "$ACCOUNT" test -r "$PROD/.env"; then
  echo "Le compte $ACCOUNT peut lire la production : service non démarré." >&2; exit 1
fi
echo "   $ACCOUNT ne peut pas lire $PROD."

systemctl daemon-reload
systemctl enable --now orion-v3
sleep 3
systemctl --no-pager --lines=0 status orion-v3 || true
echo "Terminé. Conversation : http://192.168.1.91:8867/discuter ; journaux : journalctl -u orion-v3"
