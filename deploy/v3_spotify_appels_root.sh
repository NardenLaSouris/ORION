#!/bin/bash
# Spotify et appels pour la V3, Core actif (docs/v3/recuperation_v2.md, § 2.5 et 2.6). À lancer par vous, une fois :
#   ssh -t user@192.168.1.91 "sudo bash /home/user/orion-v3/deploy/v3_spotify_appels_root.sh"
#
# Spotify : le lecteur orion-librespot tourne sous le compte orion-v3 (fichier complémentaire de son unité, l'unité
#   d'origine n'est pas modifiée), avec SA copie du cache de connexion (/var/lib/orion-v3/librespot ; celui de la V2
#   reste en place), et écrit dans le tube de la V3 (/opt/orion-v3/data/music.fifo) ; la V3 peut le relancer (polkit :
#   ce service seulement, relance seulement) ; jeton Spotify et catalogue de la V2 recopiés (votre accord).
# Appels : la V3 a le droit de joindre la passerelle orion-call (127.0.0.1:8770) ; la passerelle n'est pas modifiée
#   (elle garde son jeton, repris dans la V3 avec votre accord).
# Retour arrière : sudo bash /home/user/orion-v3/deploy/v3_spotify_appels_root.sh --retour
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo : sudo bash $0" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
DROPIN=/etc/systemd/system/orion-librespot.service.d/v3.conf
RULE=/etc/polkit-1/rules.d/51-orion-v3-librespot.rules
CACHE=/var/lib/orion-v3/librespot

if [ "${1:-}" = "--retour" ]; then
    rm -f "$DROPIN" "$RULE"
    last=$(ls -t /etc/orion-v3/isolation.nft.avant-* 2>/dev/null | head -1)
    [ -n "$last" ] && cp -a "$last" /etc/orion-v3/isolation.nft && nft -f /etc/orion-v3/isolation.nft
    systemctl daemon-reload
    systemctl restart orion-librespot orion-v3
    echo "Retour fait : lecteur Spotify comme avant (compte orion, tube de la V2), 8770 de nouveau interdit à la V3."
    exit 0
fi

echo "1. Règles réseau : passerelle d'appel (8770) permise à la V3"
nft -c -f "$HERE/v3_isolation.nft"
cp -a /etc/orion-v3/isolation.nft "/etc/orion-v3/isolation.nft.avant-$(date +%Y%m%d-%H%M%S)"
install -m 0644 "$HERE/v3_isolation.nft" /etc/orion-v3/isolation.nft
nft -f /etc/orion-v3/isolation.nft

echo "2. Lecteur Spotify sous le compte orion-v3"
install -d -m 700 -o orion-v3 -g orion-v3 /var/lib/orion-v3 "$CACHE"
cp -a /var/lib/orion/librespot/. "$CACHE/"
chown -R orion-v3:orion-v3 "$CACHE"
mkdir -p "$(dirname "$DROPIN")"
cat > "$DROPIN" <<'EOF'
# ORION V3 Core actif (deploy/v3_spotify_appels_root.sh) : lecteur sous le compte de la V3, tube de la V3.
[Service]
User=orion-v3
Group=orion-v3
ExecStart=
ExecStart=/usr/local/bin/librespot --name ORION --device-type speaker --cache /var/lib/orion-v3/librespot --disable-discovery --disable-audio-cache --backend pipe --device /opt/orion-v3/data/music.fifo --format S16 --bitrate 320 --initial-volume 100
ReadWritePaths=
ReadWritePaths=/opt/orion-v3/data /var/lib/orion-v3/librespot
EOF
cat > "$RULE" <<'EOF'
// ORION V3 relance son lecteur quand l'appareil disparaît de Spotify : seul ce service, et seulement le relancer.
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" && subject.user == "orion-v3" &&
        action.lookup("unit") == "orion-librespot.service" && action.lookup("verb") == "restart") {
        return polkit.Result.YES;
    }
});
EOF
chmod 644 "$RULE"

echo "3. Jeton Spotify et catalogue de la V2 recopiés dans la V3"
for f in spotify_token.json spotify_catalog.json; do
    [ -f "/opt/orion/data/$f" ] && install -m 660 -o orion-v3 -g orion-v3 "/opt/orion/data/$f" "/opt/orion-v3/data/$f"
done

echo "4. Redémarrages : la V3 (elle crée son tube), puis le lecteur"
systemctl daemon-reload
systemctl restart orion-v3
for i in $(seq 1 30); do [ -p /opt/orion-v3/data/music.fifo ] && break; sleep 2; done
[ -p /opt/orion-v3/data/music.fifo ] || echo "ATTENTION : tube de la V3 absent (journal : journalctl -u orion-v3)"
systemctl restart orion-librespot
sleep 3
echo "Lecteur : $(systemctl is-active orion-librespot) sous $(systemctl show orion-librespot -p User --value) ; V3 : $(systemctl is-active orion-v3)"
