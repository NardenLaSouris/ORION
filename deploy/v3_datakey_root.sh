#!/bin/bash
# Clé des données personnelles de la V3 (lot F4, docs/v3/foundation.md « Gestion de la clé ») : à lancer par vous, une
# fois, sur le mini-PC :
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_datakey_root.sh"
#
# 1. crée un trousseau neuf (une clé aléatoire de 32 octets) dans un dossier en mémoire réservé à root ;
# 2. vous l'affiche UNE fois, pour votre copie hors ligne (gestionnaire de mots de passe ou papier) : sans cette copie,
#    une panne du mini-PC rend vos données personnelles irrécupérables ;
# 3. le range chiffré par la machine (systemd-creds) dans /etc/credstore.encrypted/orion-v3.data_key : seul le service
#    orion-v3 le reçoit, en mémoire, au démarrage ; jamais dans .env, Git, un journal ou une sauvegarde ;
# 4. efface la copie en clair et redémarre la V3 : KNOWLEDGE, MISSIONS et l'agenda Google se déverrouillent.
# Refuse de remplacer une clé existante (elle chiffre déjà des données) : la rotation passe par datakey rotate.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo : sudo bash $0" >&2; exit 1; }
TARGET=/etc/credstore.encrypted/orion-v3.data_key
PY=/opt/orion-v3/.venv/bin/python
[ -e "$TARGET" ] && { echo "Une clé existe déjà ($TARGET) : rien n'est remplacé." >&2; exit 1; }
[ -x "$PY" ] || { echo "Python de la V3 introuvable ($PY)." >&2; exit 1; }
command -v systemd-creds >/dev/null || { echo "systemd-creds absent." >&2; exit 1; }

work=$(mktemp -d /run/orion-datakey.XXXXXX)   # /run : en mémoire, jamais sur disque
chmod 700 "$work"
trap 'shred -u "$work"/* 2>/dev/null || true; rm -rf "$work"' EXIT
(cd /opt/orion-v3 && "$PY" -m orion.platform.datakey generate "$work/keyring.json" >/dev/null)

echo
echo "=== COPIE HORS LIGNE : notez ce trousseau dans votre gestionnaire de mots de passe (ou sur papier) ==="
echo
cat "$work/keyring.json"
echo
echo "=== Il ne sera plus jamais affiché. ==="
read -rp "Copie faite et vérifiée ? Tapez « oui » pour continuer : " answer
[ "$answer" = "oui" ] || { echo "Arrêté : aucune clé n'a été rangée (rien n'a changé)."; exit 1; }
clear 2>/dev/null || true

mkdir -p /etc/credstore.encrypted
systemd-creds encrypt --name=orion-v3.data_key "$work/keyring.json" "$TARGET"
chmod 600 "$TARGET"
(cd /opt/orion-v3 && "$PY" -m orion.platform.datakey show "$work/keyring.json")   # identifiant et empreinte seulement
systemctl restart orion-v3
sleep 30
journalctl -u orion-v3 --since "-40s" --no-pager -o cat | grep -E "Données personnelles|Module (knowledge|missions|google_calendar)|Agenda Google" || true
echo "Clé rangée dans $TARGET ; la V3 a redémarré."
