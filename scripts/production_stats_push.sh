#!/bin/sh
# Coût de production à jour après chaque commit (hook scripts/hooks/post-commit) : relevé des tokens et des heures des
# sessions de développement sur ce PC (scripts/production_stats.py, des nombres seulement, aucun texte), puis copie
# dans les données du Core V3, où le fond d'écran le relit (panneau « Cœur »). Lancé en arrière-plan : le commit
# n'attend pas ; en cas d'échec (Core injoignable…), le commit n'est pas touché et la raison est notée dans le journal.
#   sh scripts/production_stats_push.sh      à la main, même effet
set -u
top=$(cd "$(dirname "$0")/.." && pwd)
dir="$HOME/.orion"
log="$dir/production_stats.log"
core="user@192.168.1.91"
dest="/opt/orion-v3/data/production_stats.json"
mkdir -p "$dir"
out="$dir/production_stats.json"
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
py=$(command -v python || command -v py || command -v python3)
stamp=$(date '+%Y-%m-%d %H:%M:%S')
commit=$(git -C "$top" rev-parse --short HEAD 2>/dev/null || echo "?")
if ! summary=$("$py" "$(win "$top/scripts/production_stats.py")" --out "$(win "$out")" 2>&1); then
  echo "$stamp $commit relevé en échec : $summary" >> "$log"; exit 1
fi
ssh_opts="-o BatchMode=yes -o ConnectTimeout=10"
# Copie dans un fichier temporaire puis remplacement en une fois : le fond d'écran ne lit jamais un fichier à moitié écrit.
if scp -q $ssh_opts "$out" "$core:$dest.tmp" && ssh $ssh_opts "$core" "chmod 640 '$dest.tmp' && mv -f '$dest.tmp' '$dest'"; then
  echo "$stamp $commit $summary -> Core" >> "$log"
else
  echo "$stamp $commit relevé fait ($summary) mais copie vers le Core en échec" >> "$log"; exit 1
fi
