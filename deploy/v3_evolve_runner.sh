#!/bin/bash
# Exécutant des demandes d'EVOLVE autonome (service orion-v3-evolve, compte user ; deploy/v3_evolve_root.sh).
# Déclenché quand ORION dépose /opt/orion-v3/data/evolve/deploy/request.json ; ORION ne déploie jamais elle-même.
#
#   deploy   : récupère la branche du clone d'EVOLVE dans ~/orion-dev (v3-evolve), contrôle indépendant
#              (deploy/v3_evolve_gate.py), suite complète des tests sur ce commit, puis deploy/v3_deploy.sh v3-evolve
#              (sauvegarde, avance rapide, santé, retour automatique à la version précédente sinon) ;
#   rollback : seulement si le changement est le dernier déployé ; sauvegarde, retour au commit d'avant, redémarrage,
#              contrôle de santé.
# Résultat dans result.json (lu par ORION, qui vous prévient) ; journal : ~/sauvegardes/orion-v3/evolve.log.
set -uo pipefail
DIR=/opt/orion-v3/data/evolve/deploy
REQ=$DIR/request.json
PROC=$DIR/processing.json
TARGET=/opt/orion-v3
CLONE=$TARGET/data/evolve/repo
DEV=$HOME/orion-dev
LOG=$HOME/sauvegardes/orion-v3/evolve.log
PY=$TARGET/.venv/bin/python
SAFE=(-c "safe.directory=$CLONE" -c "safe.directory=$CLONE/.git")
mkdir -p "$(dirname "$LOG")"

[ -f "$REQ" ] || exit 0
mv -f "$REQ" "$PROC"
field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2],""); print(v if isinstance(v,str) else "")' "$PROC" "$1"; }
id=$(field id); kind=$(field kind); commit=$(field commit); branch=$(field branch); base=$(field base)
before=$(git -C "$TARGET" rev-parse HEAD)

finish() {  # finish <ok|échec> <message>
    python3 - "$DIR/result.json" "$id" "$kind" "$1" "$2" "$before" "$(git -C "$TARGET" rev-parse HEAD)" <<'EOF'
import json, sys, os
path, ident, kind, state, message, before, after = sys.argv[1:8]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"id": ident, "kind": kind, "state": state, "message": message[-600:], "before": before,
               "after": after}, f, ensure_ascii=False)
os.replace(tmp, path)
EOF
    echo "$(date +%s) $( [ "$1" = ok ] && echo "${kind/deploy/déployé}" || echo "refusé-$kind") $id ${before:0:7} : $2" >> "$LOG"
    rm -f "$PROC"
    exit 0
}

[[ "$id" =~ ^[0-9a-f]{10}$ ]] || finish échec "identifiant invalide"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || finish échec "commit invalide"

case "$kind" in
deploy)
    [[ "$branch" =~ ^evolve/auto-[0-9a-f]{10}$ ]] || finish échec "branche invalide"
    [ "$base" = "$before" ] || finish échec "le Core a changé depuis la préparation (${before:0:7})"
    git -C "$DEV" "${SAFE[@]}" fetch -q --force "$CLONE" "refs/heads/$branch:refs/heads/v3-evolve" \
        || finish échec "branche d'EVOLVE illisible"
    [ "$(git -C "$DEV" rev-parse v3-evolve)" = "$commit" ] || finish échec "la branche ne correspond pas au commit demandé"
    reason=$(python3 "$TARGET/deploy/v3_evolve_gate.py" --repo "$DEV" --base "$before" --commit "$commit" --log "$LOG" 2>&1) \
        || finish échec "contrôle refusé : $reason"
    work=$(mktemp -d)
    git -C "$DEV" worktree add -q --detach "$work/w" "$commit" || finish échec "copie de test impossible"
    tests=$(cd "$work/w" && PYTHONDONTWRITEBYTECODE=1 nice "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1)
    code=$?
    git -C "$DEV" worktree remove --force "$work/w"; rm -rf "$work"
    [ "$code" = 0 ] || finish échec "tests en échec : $tests"
    out=$(bash "$TARGET/deploy/v3_deploy.sh" v3-evolve 2>&1)
    code=$?
    [ "$code" = 0 ] && finish ok "$tests ; $(echo "$out" | tail -1)" || finish échec "$(echo "$out" | tail -3 | tr '\n' ' ')"
    ;;
rollback)
    # Retour arrière par un commit d'annulation (git revert) : l'historique reste juste partout (PC, GitHub), et le
    # déploiement passe par le même chemin (sauvegarde, avance rapide, santé, retour automatique sinon).
    git -C "$TARGET" merge-base --is-ancestor "$commit" "$before" || finish échec "ce changement n'est pas déployé"
    git -C "$DEV" fetch -q --force "$TARGET" "HEAD:refs/heads/v3-evolve" || finish échec "Core illisible"
    work=$(mktemp -d)
    git -C "$DEV" worktree add -q "$work/w" v3-evolve || finish échec "copie de travail impossible"
    if ! git -C "$work/w" -c user.name="ORION EVOLVE" -c user.email=evolve@orion.local -c commit.gpgsign=false \
            revert --no-edit "$commit" >/dev/null 2>&1; then
        git -C "$work/w" revert --abort 2>/dev/null
        git -C "$DEV" worktree remove --force "$work/w"; rm -rf "$work"
        finish échec "annulation en conflit avec des changements suivants : à faire à la main"
    fi
    git -C "$DEV" worktree remove --force "$work/w"; rm -rf "$work"
    out=$(bash "$TARGET/deploy/v3_deploy.sh" v3-evolve 2>&1)
    [ $? = 0 ] && finish ok "annulé : $(echo "$out" | tail -1)" || finish échec "$(echo "$out" | tail -3 | tr '\n' ' ')"
    ;;
*)
    finish échec "demande inconnue"
    ;;
esac
