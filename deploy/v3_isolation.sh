#!/usr/bin/env bash
# Durcissement de l'environnement V3 (audit d'isolation du 3 novembre 2026, docs/v3/environnements.md).
#
#   sudo bash /home/user/orion-v3/deploy/v3_isolation.sh
#
# Idempotent. Ne touche ni à /opt/orion, ni au service orion, ni à ses comptes :
# 1. règles nftables : le compte orion-v3 ne joint plus les ports de la production ni ses agents ;
# 2. limites de ressources : la V3 cède la mémoire et le processeur à la production, et le noyau la sacrifie la
#    première en cas de manque de mémoire ;
# 3. /opt/jarvis (ancienne copie de la production) masqué au service, en plus de ses droits.
# Les règles sont rechargées à chaque démarrage d'orion-v3 : si elles ne se chargent pas, le service ne démarre pas.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
RULES=/etc/orion-v3/isolation.nft
DROPIN=/etc/systemd/system/orion-v3.service.d/isolation.conf

[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo." >&2; exit 1; }
id orion-v3 >/dev/null 2>&1 || { echo "Compte orion-v3 absent : lancer d'abord deploy/v3_env.sh." >&2; exit 1; }
command -v nft >/dev/null || { echo "nft absent (paquet nftables)." >&2; exit 1; }

echo "1. Règles réseau ($RULES)"
install -d -o root -g root -m 755 /etc/orion-v3
install -o root -g root -m 644 "$HERE/v3_isolation.nft" "$RULES"
nft -c -f "$RULES"
nft -f "$RULES"

echo "2. Limites et chargement des règles au démarrage ($DROPIN)"
install -d -o root -g root -m 755 "$(dirname "$DROPIN")"
cat > "$DROPIN" <<'UNIT'
# Audit d'isolation V2/V3 : voir docs/v3/environnements.md.
[Service]
ExecStartPre=+/usr/sbin/nft -f /etc/orion-v3/isolation.nft
MemoryHigh=2G
MemoryMax=3G
MemorySwapMax=512M
OOMScoreAdjust=500
CPUWeight=50
IOWeight=50
TasksMax=512
InaccessiblePaths=-/opt/jarvis
UNIT
systemctl daemon-reload

echo "3. Redémarrage d'orion-v3 (la production n'est pas concernée)"
systemctl restart orion-v3
sleep 2
systemctl is-active orion-v3

echo "4. Contrôle"
probe() {  # $1 hôte, $2 port : « joint » ou « bloqué » pour le compte orion-v3
  if sudo -u orion-v3 timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then echo joint; else echo bloqué; fi
}
fail=0
for target in 127.0.0.1:8766 127.0.0.1:8767 192.168.1.91:8767 127.0.0.1:8770 192.168.1.128:8765; do
  state=$(probe "${target%:*}" "${target#*:}")
  echo "   orion-v3 -> $target : $state"
  [ "$state" = bloqué ] || fail=1
done
echo "   orion-v3 -> 127.0.0.1:11434 (Ollama) : $(probe 127.0.0.1 11434)"
[ "$fail" = 0 ] || { echo "Isolation incomplète : voir ci-dessus." >&2; exit 1; }
echo "Terminé."
