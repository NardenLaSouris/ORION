#!/bin/bash
# ORION V3 devient le seul Core actif (docs/v3/reference_unique.md). À lancer par vous, une fois, sur le mini-PC :
#   sudo bash /home/user/orion-v3/deploy/v3_reference_root.sh
#
# 1. règles réseau de la V3 : la transcription du Katana (8766) lui est permise ; le Core V2 et les anciens agents V2
#    lui restent interdits ;
# 2. ressources de la V3 : celles du Core principal (elle était bridée comme un environnement secondaire) ;
# 3. Core V2 (service orion) arrêté et désactivé, mais GARDÉ : /opt/orion, ses données, ses secrets et ses sauvegardes
#    ne sont pas touchés. Retour arrière : sudo systemctl enable --now orion (après avoir arrêté orion-v3).
# Les services annexes de la V2 (orion-call, orion-librespot) ne sont pas touchés.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo : sudo bash $0" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/v3_isolation.nft" ] || { echo "Règles introuvables : $HERE/v3_isolation.nft" >&2; exit 1; }
[ -d /opt/orion-v3 ] && systemctl cat orion-v3 >/dev/null || { echo "Service orion-v3 introuvable" >&2; exit 1; }

echo "1. Règles réseau de la V3"
nft -c -f "$HERE/v3_isolation.nft"   # vérifiées avant d'être installées
cp -a /etc/orion-v3/isolation.nft "/etc/orion-v3/isolation.nft.avant-$(date +%Y%m%d-%H%M%S)"
install -m 0644 "$HERE/v3_isolation.nft" /etc/orion-v3/isolation.nft
nft -f /etc/orion-v3/isolation.nft

echo "2. Ressources du Core principal"
cat > /etc/systemd/system/orion-v3.service.d/principal.conf <<'EOF'
# ORION V3 est le Core actif (deploy/v3_reference_root.sh) : ressources du Core principal.
[Service]
MemoryHigh=5G
MemoryMax=7G
OOMScoreAdjust=0
CPUWeight=100
IOWeight=100
EOF

echo "3. Core V2 arrêté et désactivé (gardé pour un retour arrière)"
systemctl disable --now orion
systemctl daemon-reload
systemctl restart orion-v3
sleep 5
systemctl --no-pager --lines=0 status orion-v3 | head -5
echo "Core V2 : $(systemctl is-active orion) / $(systemctl is-enabled orion) ; /opt/orion conservé."
