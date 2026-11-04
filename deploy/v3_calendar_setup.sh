#!/bin/bash
# Agenda Google « ORION » pour la V3 (docs/v3/google_calendar.md) : saisie de l'adresse secrète iCal SUR LE CORE.
#   ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_calendar_setup.sh"            saisir et activer
#   ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_calendar_setup.sh --desactiver" couper le connecteur
#   ssh -t user@192.168.1.91 "bash /home/user/orion-v3/deploy/v3_calendar_setup.sh --oublier"    effacer l'adresse
# L'adresse n'est jamais affichée, ni passée en argument, ni écrite ailleurs que dans /opt/orion-v3/.env (droits 640,
# compte user, groupe orion-v3) ; elle n'apparaît ni dans Git, ni dans les journaux, ni dans ORION Control.
set -euo pipefail
ENV=/opt/orion-v3/.env
CONF=/opt/orion-v3/config.local.toml
KEY=ORION_CALENDAR_ICS
PATTERN='^https://calendar\.google\.com/calendar/ical/[A-Za-z0-9%._@-]{3,200}/private-[0-9a-f]{16,64}/basic\.ics$'
[ -w "$ENV" ] && [ -w "$CONF" ] || { echo "À lancer sous le compte user du Core (fichiers $ENV et $CONF)." >&2; exit 1; }
stamp=$(date +%Y%m%d-%H%M%S)

set_enabled() {  # [google_calendar] enabled = true|false, configuration sauvegardée avant
    cp -a "$CONF" "$CONF.avant-agenda-$stamp"
    python3 - "$CONF" "$1" <<'EOF'
import re, sys, tomllib
from pathlib import Path
path, value = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?ms)^\[google_calendar\]\n.*?(?=^\[|\Z)", "", text).rstrip()
text += f"\n\n# --- Agenda Google « ORION » (deploy/v3_calendar_setup.sh) ---\n[google_calendar]\nenabled = {value}\n"
tomllib.loads(text)
path.write_text(text, encoding="utf-8")
EOF
}

remove_secret() {
    cp -a "$ENV" "$ENV.avant-agenda-$stamp"
    grep -v "^$KEY=" "$ENV" > "$ENV.tmp" || true
    cat "$ENV.tmp" > "$ENV" && rm -f "$ENV.tmp"   # même fichier : propriétaire et droits gardés
}

case "${1:-}" in
    --desactiver) set_enabled false; sudo -n systemctl restart orion-v3; echo "Connecteur désactivé (adresse gardée)."; exit 0 ;;
    --oublier) set_enabled false; remove_secret; sudo -n systemctl restart orion-v3
               echo "Adresse effacée de la V3 (copie de sûreté : $ENV.avant-agenda-$stamp, à supprimer vous-même)."; exit 0 ;;
esac

echo "Google Agenda > Paramètres > calendrier « ORION » > Intégrer l'agenda > « Adresse secrète au format iCal »."
echo "Collez-la ci-dessous (rien ne s'affiche), puis Entrée."
IFS= read -rs url
echo
url=$(printf '%s' "$url" | tr -d '\r\n[:space:]')
if ! printf '%s' "$url" | grep -Eq "$PATTERN"; then
    unset url
    echo "Ce n'est pas une adresse secrète iCal de Google Agenda (https://calendar.google.com/.../private-.../basic.ics)." >&2
    exit 1
fi
remove_secret
printf '%s=%s\n' "$KEY" "$url" >> "$ENV"
unset url
echo "Adresse enregistrée dans $ENV (jamais affichée)."
read -rp "Activer le connecteur maintenant (lecture seule, toutes les 5 minutes) ? [o/N] " answer
if [ "${answer,,}" = "o" ]; then
    set_enabled true
    sudo -n systemctl restart orion-v3
    echo "Connecteur activé. État : ORION Control, vue « Agenda Google »."
    echo "Si la base personnelle est verrouillée (clé F4 absente), il reste « verrouillé » et ne lit rien."
else
    echo "Connecteur laissé désactivé : relancez ce script pour l'activer."
fi
