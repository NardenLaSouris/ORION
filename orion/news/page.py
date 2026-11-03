"""Bulletin NEWS mis en page (HTML) : le même rendu pour le navigateur, le mail et ORION Control.

- Une rubrique par catégorie, une carte par sujet : titre, statut (confirmé ou non), résumé, faits confirmés qui ne
  répètent pas le résumé, éléments incertains, et les sources en liens.
- Styles en ligne seulement (les messageries ignorent souvent les feuilles de style), aucune ressource externe
  (ni police, ni image, ni script) : la page s'affiche telle quelle hors ligne et ne trahit pas son ouverture.
- Tout texte venant des articles est échappé ; seules les adresses http(s) deviennent des liens.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from orion.news.synthesis import CATEGORY_LABELS

INK, MUTED, LINE, PAPER, CARD = "#1d2433", "#5b6576", "#e3e7ee", "#f4f6fa", "#ffffff"
ACCENT = {"geopolitique": "#3b6fd8", "defense": "#8a5a2b", "tech_ia": "#7b4bd6", "france": "#2c8a5a",
          "economie": "#c0712c", "sciences": "#1f8a9a"}
ORDER = ("geopolitique", "defense", "france", "economie", "tech_ia", "sciences")


def _link(url: str, label: str) -> str:
    if not str(url).startswith(("http://", "https://")):
        return escape(label)
    return f'<a href="{escape(url, quote=True)}" style="color:{MUTED};">{escape(label)}</a>'


def _badge(text: str, color: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;'
            f'background:{color}1a;color:{color};border:1px solid {color}55;">{escape(text)}</span>')


def _entry(entry: dict, color: str) -> str:
    confirmed = entry.get("status") == "confirmé"
    badges = [_badge("confirmé" if confirmed else "non confirmé à ce stade", "#2c8a5a" if confirmed else "#b07a12")]
    if entry.get("evolution"):
        badges.insert(0, _badge("évolution", color))
    parts = [f'<div style="font-size:16px;font-weight:600;line-height:1.35;margin:0 0 6px;">'
             f'{escape(entry.get("title", ""))}</div>', f'<div style="margin:0 0 8px;">{" ".join(badges)}</div>']
    if entry.get("summary"):
        parts.append(f'<p style="margin:0 0 8px;line-height:1.5;">{escape(entry["summary"])}</p>')
    for label, values in (("Faits confirmés", entry.get("confirmed") or []), ("Incertain", entry.get("uncertain") or [])):
        if values:
            items = "".join(f'<li style="margin:2px 0;">{escape(v)}</li>' for v in values)
            parts.append(f'<div style="font-size:13px;color:{MUTED};margin:6px 0 2px;">{label}</div>'
                         f'<ul style="margin:0 0 8px 18px;padding:0;line-height:1.45;">{items}</ul>')
    links = entry.get("links") or []
    sources = " · ".join(_link(l.get("url", ""), l.get("source", "")) for l in links) if links \
        else escape(entry.get("sources", ""))
    if sources:
        parts.append(f'<div style="font-size:12px;color:{MUTED};">Sources : {sources}</div>')
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-left:4px solid {color};border-radius:8px;'
            f'padding:14px 16px;margin:0 0 12px;">{"".join(parts)}</div>')


def render_html(bulletin: dict, assistant: str = "ORION") -> str:
    """``bulletin`` : ligne de ``news_bulletins`` lue par le moteur (``entries`` décodées)."""
    at = datetime.fromtimestamp(bulletin.get("at") or 0)
    slot = bulletin.get("slot") or at.strftime("%H:%M")
    entries = bulletin.get("entries") or []
    title = f"Bulletin de {slot} — {at:%d/%m/%Y}"
    sections = []
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(entry.get("category", ""), []).append(entry)
    for category in sorted(groups, key=lambda c: ORDER.index(c) if c in ORDER else len(ORDER)):
        color = ACCENT.get(category, "#3b6fd8")
        label = CATEGORY_LABELS.get(category, category or "Divers")
        cards = "".join(_entry(e, color) for e in groups[category])
        sections.append(f'<h2 style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:{color};'
                        f'margin:22px 0 10px;">{escape(label)}</h2>{cards}')
    if not sections:
        quiet = bulletin.get("text") or "Peu d’actualité importante depuis le précédent bulletin."
        body = f'<p style="line-height:1.5;">{escape(quiet)}</p>'
    else:
        body = "".join(sections)
    count = len(entries)
    subtitle = f"{count} sujet{'s' if count > 1 else ''}" if count else "Rien d’important"
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><base target="_blank"></head>
<body style="margin:0;background:{PAPER};color:{INK};font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:720px;margin:0 auto;padding:24px 16px 40px;">
<div style="font-size:12px;letter-spacing:.3em;color:{MUTED};">{escape(assistant.upper())} · ACTUALITÉS</div>
<h1 style="font-size:24px;margin:6px 0 2px;">{escape(title)}</h1>
<div style="color:{MUTED};font-size:14px;margin-bottom:8px;">{escape(subtitle)}</div>
{body}
<div style="font-size:12px;color:{MUTED};margin-top:24px;border-top:1px solid {LINE};padding-top:10px;">
Résumés établis à partir des seuls articles cités ; « non confirmé » : une seule source ou aucune source officielle.
</div></div></body></html>"""


RUBRIC_COLORS = {"essentiel": "#c0392b", "vos_sujets": "#7b4bd6", "evolutions": "#3b6fd8", "tech_sciences": "#1f8a9a",
                 "international": "#3b6fd8", "economie": "#c0712c", "france": "#2c8a5a"}
RELIABILITY_COLORS = {"officielle": "#2c8a5a", "confirmée": "#2c8a5a", "non confirmée": "#b07a12"}


def _journal_card(item: dict, color: str) -> str:
    published = datetime.fromtimestamp(item.get("published_at") or 0).strftime("%d/%m %H:%M")
    badges = [_badge(f"n° {item.get('number', '')}", color),
              _badge(item.get("reliability_label", ""), RELIABILITY_COLORS.get(item.get("reliability"), MUTED))]
    parts = [f'<div style="font-size:16px;font-weight:600;line-height:1.35;margin:0 0 6px;">'
             f'{escape(item.get("title", ""))}</div>', f'<div style="margin:0 0 8px;">{" ".join(badges)}</div>']
    if item.get("summary"):
        parts.append(f'<p style="margin:0 0 8px;line-height:1.5;">{escape(item["summary"])}</p>')
    for label, values in (("Faits confirmés", item.get("confirmed") or []), ("Incertain", item.get("uncertain") or [])):
        if values:
            rows = "".join(f'<li style="margin:2px 0;">{escape(v)}</li>' for v in values)
            parts.append(f'<div style="font-size:13px;color:{MUTED};margin:6px 0 2px;">{label}</div>'
                         f'<ul style="margin:0 0 8px 18px;padding:0;line-height:1.45;">{rows}</ul>')
    parts.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 4px;">Pourquoi : {escape(item.get("why", ""))}'
                 f'</div>')
    lead = _link(item.get("url", ""), f"Lire l’article ({item.get('source', '')})")
    others = " · ".join(_link(link.get("url", ""), link.get("source", "")) for link in (item.get("links") or [])[1:])
    parts.append(f'<div style="font-size:12px;color:{MUTED};">Publié le {published} · {lead}'
                 f'{" · Autres sources : " + others if others else ""}</div>')
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-left:4px solid {color};border-radius:8px;'
            f'padding:14px 16px;margin:0 0 12px;">{"".join(parts)}</div>')


def render_journal(journal: dict, assistant: str = "ORION") -> str:
    """Journal quotidien (``news_journals`` décodé) : une rubrique par section, une fiche numérotée par information."""
    from orion.news.briefing import RUBRICS

    at = datetime.fromtimestamp(journal.get("at") or 0)
    items = journal.get("items") or []
    title = f"Journal du {at:%d/%m/%Y}"
    sections = []
    for rubric, label in RUBRICS.items():
        group = [i for i in items if i.get("rubric") == rubric]
        if not group:
            continue
        color = RUBRIC_COLORS.get(rubric, "#3b6fd8")
        cards = "".join(_journal_card(i, color) for i in group)
        sections.append(f'<h2 style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:{color};'
                        f'margin:22px 0 10px;">{escape(label)}</h2>{cards}')
    body = "".join(sections) or f'<p style="line-height:1.5;">{escape(journal.get("spoken") or "")}</p>'
    excluded = len(journal.get("excluded") or {})
    count = len(items)
    subtitle = (f"{count} information{'s' if count > 1 else ''}" if count else "Rien d’important") + \
        (f" · {excluded} écartée{'s' if excluded > 1 else ''} (doublon ou titre trompeur)" if excluded else "")
    preview = "" if journal.get("day") else " · aperçu"
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><base target="_blank"></head>
<body style="margin:0;background:{PAPER};color:{INK};font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:720px;margin:0 auto;padding:24px 16px 40px;">
<div style="font-size:12px;letter-spacing:.3em;color:{MUTED};">{escape(assistant.upper())} · JOURNAL</div>
<h1 style="font-size:24px;margin:6px 0 2px;">{escape(title)}</h1>
<div style="color:{MUTED};font-size:14px;margin-bottom:8px;">{escape(subtitle + preview)}</div>
{body}
<div style="font-size:12px;color:{MUTED};margin-top:24px;border-top:1px solid {LINE};padding-top:10px;">
Résumés établis à partir des seuls articles cités. « Non confirmée » : une seule source, ou aucune source fiable ou
officielle. Les titres à sensation non confirmés et les doublons sont écartés. Aucune donnée personnelle n’est
utilisée pour composer ce journal.
</div></div></body></html>"""
