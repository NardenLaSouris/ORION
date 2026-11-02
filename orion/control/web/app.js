/*
 * ORION Control : interface. Toutes les demandes passent par le pont local (/bridge), qui seul parle au Core.
 * Aucun texte venu du Core n'est inséré comme HTML : le DOM est construit élément par élément.
 */
(() => {
  "use strict";

  const KEY = new URLSearchParams(location.hash.slice(1)).get("key") || sessionStorage.getItem("jc-key") || "";
  sessionStorage.setItem("jc-key", KEY);
  history.replaceState(null, "", location.pathname);

  const DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
  const TOOL_LABELS = {
    light_on: "Allumer la lumière", light_off: "Éteindre la lumière", light_toggle: "Inverser la lumière",
    set_brightness: "Régler la luminosité", set_color: "Changer la couleur", set_color_temperature: "Température de blanc",
    open_application: "Ouvrir une application", close_application: "Fermer une application", open_url: "Ouvrir une page Web",
    set_volume: "Régler le volume", mute_volume: "Couper le son", unmute_volume: "Remettre le son",
    create_timer: "Lancer un minuteur", create_reminder: "Programmer un rappel", cancel_timer: "Annuler un minuteur",
    cancel_reminder: "Annuler un rappel", lock_pc: "Verrouiller le PC",
  };
  const INFO_TOOLS = new Set(["get_time", "get_date", "get_weather", "system_info", "list_running_applications",
    "list_timers", "list_reminders", "create_alarm", "list_alarms", "cancel_alarm"]);
  const ANNOUNCE = { time: "L'heure", date: "La date", weather: "La météo", day: "La journée (date, météo, programme)",
    welcome: "Bon retour (et résumé de l'absence)" };
  const EVENTS = { arrival: "Retour à la maison confirmé", departure: "Départ de la maison confirmé" };
  const ACTION_TYPES = [["tool", "Action"], ["say", "ORION dit"], ["announce", "Annoncer"], ["alarm", "Réveil (sonnerie)"],
    ["wait", "Attendre"]];
  const PARAM_LABELS = {
    room: "Pièce", device: "Appareil", brightness: "Luminosité (%)", color: "Couleur", temperature: "Kelvins",
    application: "Application", url: "Adresse", volume: "Volume (%)", duration: "Durée (« 10 minutes »)",
    delay: "Délai (« 20 minutes »)", message: "Message", timer_id: "Numéro", reminder_id: "Numéro",
  };
  const CHOICE_LABELS = { all: "Toutes les pièces" };

  const state = { view: "dashboard", tools: null, settings: null, timer: null, devices: null, face: null };
  const view = document.getElementById("view");

  // --- Outils ---------------------------------------------------------------------------------------

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "class") node.className = value;
      else if (key === "value") node.value = value;
      else if (key === "checked") node.checked = Boolean(value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  async function api(method, path, body) {
    const response = await fetch("/bridge" + path, {
      method,
      headers: { "X-Control-Key": KEY, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error(response.status === 403 ? "Session expirée : rouvrez ORION Control." : `Erreur ${response.status}`);
    const data = await response.json();
    if (!data.ok) throw new Error(data.error);
    return data.data;
  }

  function toast(message, kind = "") {
    const node = el("div", { class: `toast ${kind}` }, message);
    document.getElementById("toasts").append(node);
    setTimeout(() => node.remove(), kind === "err" ? 7000 : 3500);
  }

  async function act(promise, success) {
    try {
      const result = await promise;
      if (success) toast(success, "ok");
      return result;
    } catch (error) {
      toast(error.message, "err");
      throw error;
    }
  }

  const cap = (text) => text ? text[0].toUpperCase() + text.slice(1) : "";
  const dot = (online) => el("span", { class: `dot ${online === true ? "ok" : online === false ? "err" : ""}` });
  const when = (iso) => iso ? new Date(iso).toLocaleString("fr-FR", { weekday: "short", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }) : "—";
  const clock = (iso) => iso ? new Date(iso).toLocaleTimeString("fr-FR") : "—";

  function setConnection(online, text) {
    const node = document.getElementById("connection");
    node.replaceChildren(dot(online), text);
  }

  async function tools() {
    if (!state.tools) state.tools = await api("GET", "/tools");
    return state.tools;
  }

  function triggerText(trigger) {
    if (trigger.type === "time") {
      const days = trigger.days.length === 0 || trigger.days.length === 7 ? "Tous les jours"
        : trigger.days.join() === "0,1,2,3,4" ? "Du lundi au vendredi"
        : trigger.days.join() === "5,6" ? "Le week-end" : trigger.days.map((d) => DAYS[d]).join(", ");
      return `${days} à ${trigger.time}`;
    }
    if (trigger.type === "interval") return `Toutes les ${trigger.minutes} min`;
    if (trigger.type === "at") return `Une fois, le ${new Date(trigger.at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}`;
    if (trigger.type === "event") return `${EVENTS[trigger.event] || trigger.event}${trigger.user ? ` (${trigger.user})` : ""}`;
    return "Manuellement";
  }

  function actionText(action) {
    if (action.type === "say") return `ORION dit « ${action.text} »`;
    if (action.type === "wait") return `Attendre ${action.seconds} s`;
    if (action.type === "alarm") return "Sonnerie du réveil";
    if (action.type === "announce") return `Annoncer : ${ANNOUNCE[action.what].toLowerCase()}`;
    const params = Object.entries(action.parameters || {}).map(([k, v]) => `${PARAM_LABELS[k] || k} : ${CHOICE_LABELS[v] || v}`);
    return `${TOOL_LABELS[action.tool] || action.tool}${params.length ? " (" + params.join(", ") + ")" : ""}`;
  }

  // --- Tableau de bord ------------------------------------------------------------------------------

  async function dashboard() {
    const status = await api("GET", "/status");
    setConnection(true, "Core connecté");
    setAlert([
      ...(status.llm && status.llm.online === false ? ["Module de réflexion (Katana) injoignable : mode réduit"] : []),
      ...status.agents.filter((a) => a.online === false).map((a) => `${cap(a.name)} ne répond pas`),
    ]);
    const llm = status.llm;
    const lights = status.lights;
    const schedule = [...status.schedule.timers.map((t) => `Minuteur : encore ${t.remaining} (${t.ends_at})`),
      ...status.schedule.reminders.map((r) => `Rappel « ${r.message} » à ${r.at}`)];
    view.replaceChildren(
      el("h1", {}, "Tableau de bord"),
      el("div", { class: "grid" },
        el("div", { class: "card" }, el("h3", {}, "ORION"), el("div", { class: "big" }, dot(true), "Opérationnel"),
          el("div", { class: "muted small" }, `Démarré depuis ${Math.round(status.core.uptime_s / 60)} min`)),
        el("div", { class: "card" }, el("h3", {}, "LLM"),
          el("div", { class: "big" }, dot(llm.online), llm.model || "—"),
          el("div", { class: "muted small" }, llm.online ? `Chargé : ${(llm.loaded || []).join(", ") || "aucun"}` : "Worker LLM injoignable",
            llm.fallback_model ? ` · secours : ${llm.fallback_model}` : "")),
        el("div", { class: "card" }, el("h3", {}, "Agents"),
          status.agents.length ? status.agents.map((a) => el("div", { class: "row" }, dot(a.online), cap(a.name))) : el("div", { class: "muted" }, "Aucun appareil configuré")),
        el("div", { class: "card" }, el("h3", {}, "Recherche Web"),
          el("div", { class: "big" }, dot(status.search.online), status.search.enabled === false ? "Désactivée" : status.search.online ? "SearXNG joignable" : "SearXNG injoignable")),
        el("div", { class: "card" }, el("h3", {}, "Lumières"),
          el("div", { class: "big" }, dot(lights.total ? lights.online === lights.total : null), `${lights.online}/${lights.total} connectées`),
          lights.items.map((l) => el("div", { class: "muted small" }, `${cap(l.name)} : ${l.online ? (l.on ? `allumée, ${l.brightness} %` : "éteinte") : "injoignable"}`))),
        el("div", { class: "card" }, el("h3", {}, "Routines"),
          el("div", { class: "big" }, `${status.routines.enabled} active${status.routines.enabled > 1 ? "s" : ""}`, el("span", { class: "muted small" }, ` / ${status.routines.total}`)),
          status.routines.next.map((r) => el("div", { class: "muted small" }, `${r.name} : ${when(r.next_run)}`))),
        el("div", { class: "card" }, el("h3", {}, "Minuteurs et rappels"),
          schedule.length ? schedule.map((s) => el("div", { class: "small" }, s)) : el("div", { class: "muted" }, "Rien de prévu")),
        el("div", { class: "card" }, el("h3", {}, "Dernière activité"),
          status.last_activity ? [el("div", {}, status.last_activity.subject), el("div", { class: "muted small" }, clock(status.last_activity.time))]
            : el("div", { class: "muted" }, "Aucune")),
      ),
    );
  }

  // --- Routines -------------------------------------------------------------------------------------

  async function routines() {
    const list = await api("GET", "/routines");
    const rows = list.map((r) => el("div", { class: "card row" },
      el("label", { class: "switch", title: r.enabled ? "Désactiver" : "Activer" },
        el("input", { type: "checkbox", checked: r.enabled, onchange: async (e) => {
          await act(api("POST", `/routines/${r.id}/${e.target.checked ? "enable" : "disable"}`), e.target.checked ? "Routine activée" : "Routine désactivée").catch(() => {});
          show("routines");
        } }), el("span")),
      el("div", { class: "grow" },
        el("div", {}, el("strong", {}, r.name), r.actions.some((a) => a.type === "alarm") ? el("span", { class: "chip" }, "⏰ réveil") : null,
          r.once ? el("span", { class: "chip" }, "une fois") : null, r.running ? el("span", { class: "chip" }, "en cours") : null),
        el("div", { class: "muted small" }, triggerText(r.trigger), " · ", `${r.actions.length} action${r.actions.length > 1 ? "s" : ""}`,
          r.next_run ? ` · prochaine : ${when(r.next_run)}` : "",
          r.last_run ? ` · dernière : ${when(r.last_run)} ${r.last_status === "success" ? "✓" : "✗"}` : "")),
      el("button", { class: "btn", onclick: () => act(api("POST", `/routines/${r.id}/run`), `« ${r.name} » lancée`).then(() => setTimeout(() => show("routines"), 1500)).catch(() => {}) }, "▶ Tester"),
      el("button", { class: "btn", onclick: () => editor(r) }, "Modifier"),
      el("button", { class: "btn", onclick: () => act(api("POST", `/routines/${r.id}/duplicate`), "Routine dupliquée").then(() => show("routines")).catch(() => {}) }, "Dupliquer"),
      el("button", { class: "btn danger", onclick: async () => {
        if (!confirm(`Supprimer la routine « ${r.name} » ?`)) return;
        await act(api("DELETE", `/routines/${r.id}`), "Routine supprimée").catch(() => {});
        show("routines");
      } }, "Supprimer")));
    view.replaceChildren(
      el("div", { class: "toolbar" }, el("h1", { class: "grow" }, "Routines"),
        el("button", { class: "btn primary", onclick: () => editor(null) }, "+ Nouvelle routine")),
      rows.length ? el("div", { class: "list" }, rows) : el("div", { class: "empty" }, "Aucune routine. Créez la première avec « Nouvelle routine »."),
    );
  }

  function paramInput(name, spec, value, onchange, names = {}) {
    const label = PARAM_LABELS[name] || spec.description;
    let input;
    if (spec.choices && spec.choices.length) {
      const options = [...(spec.required || name === "room" ? [] : [el("option", { value: "" }, name === "device" ? "Appareil par défaut" : "—")]),
        ...spec.choices.map((c) => el("option", { value: c }, CHOICE_LABELS[c] || names[c] || c))];
      input = el("select", { onchange: (e) => onchange(e.target.value || undefined) }, options);
      input.value = value ?? (name === "room" ? spec.choices[0] : "");
      if (name === "room" && value === undefined) onchange(spec.choices[0]);
    } else if (spec.type === "int") {
      input = el("input", { type: "number", min: spec.minimum ?? undefined, max: spec.maximum ?? undefined, value: value ?? "",
        oninput: (e) => onchange(e.target.value === "" ? undefined : Number(e.target.value)) });
    } else {
      input = el("input", { type: "text", value: value ?? "", oninput: (e) => onchange(e.target.value.trim() || undefined) });
    }
    return el("label", { class: "field" }, label + (spec.required ? " *" : ""), input);
  }

  async function editor(routine) {
    const catalog = (await tools()).filter((t) => t.safe && !INFO_TOOLS.has(t.name));
    const names = Object.fromEntries((await api("GET", "/devices")).map((d) => [d.id, cap(d.name)]));
    const draft = routine ? JSON.parse(JSON.stringify({ name: routine.name, description: routine.description, enabled: routine.enabled, once: routine.once, trigger: routine.trigger, actions: routine.actions }))
      : { name: "", description: "", enabled: true, once: false, trigger: { type: "time", time: "21:00", days: [] }, actions: [] };

    const render = () => {
      const trigger = draft.trigger;
      const triggerFields = trigger.type === "time"
        ? [el("label", { class: "field" }, "Heure", el("input", { type: "time", value: trigger.time, oninput: (e) => { trigger.time = e.target.value; } })),
          el("div", { class: "field" }, el("span", { class: "muted small" }, "Jours (aucun = tous)"),
            el("div", { class: "days" }, DAYS.map((d, i) => el("label", {}, el("input", { type: "checkbox", checked: trigger.days.includes(i),
              onchange: (e) => { trigger.days = e.target.checked ? [...trigger.days, i].sort() : trigger.days.filter((x) => x !== i); } }), d))))]
        : trigger.type === "interval"
          ? [el("label", { class: "field" }, "Toutes les (minutes)", el("input", { type: "number", min: 1, max: 1440, value: trigger.minutes, oninput: (e) => { trigger.minutes = Number(e.target.value); } }))]
          : trigger.type === "event"
            ? [el("label", { class: "field" }, "Événement", el("select", { onchange: (e) => { trigger.event = e.target.value; } },
                Object.entries(EVENTS).map(([v, t]) => el("option", { value: v, selected: trigger.event === v }, t)))),
               el("div", { class: "muted small" }, "Décidé par le moteur de présence (téléphone, porte, mouvement), jamais par une porte seule.")]
            : [el("div", { class: "muted small" }, "Lancée seulement avec « Tester » (ou plus tard par une phrase ou un événement).")];

      const blocks = draft.actions.flatMap((action, index) => {
        const move = (delta) => { const [a] = draft.actions.splice(index, 1); draft.actions.splice(index + delta, 0, a); render(); };
        const controls = el("div", { class: "row" },
          el("select", { onchange: (e) => { draft.actions[index] = newAction(e.target.value, catalog); render(); } },
            ACTION_TYPES.map(([v, t]) => el("option", { value: v, selected: action.type === v }, t))),
          el("div", { class: "grow" }),
          el("button", { class: "btn icon", disabled: index === 0, onclick: () => move(-1), title: "Monter" }, "↑"),
          el("button", { class: "btn icon", disabled: index === draft.actions.length - 1, onclick: () => move(1), title: "Descendre" }, "↓"),
          el("button", { class: "btn icon danger", onclick: () => { draft.actions.splice(index, 1); render(); }, title: "Supprimer" }, "✕"));
        let body;
        if (action.type === "say") {
          body = el("label", { class: "field" }, "Phrase", el("input", { type: "text", maxlength: 200, value: action.text, oninput: (e) => { action.text = e.target.value; } }));
        } else if (action.type === "alarm") {
          body = el("div", { class: "muted small" }, "Joue la sonnerie jusqu'à « Orion » ou « arrête » (durée maximale réglée dans le Core).");
        } else if (action.type === "announce") {
          body = el("label", { class: "field" }, "ORION annonce", el("select", { onchange: (e) => { action.what = e.target.value; } },
            Object.entries(ANNOUNCE).map(([v, t]) => el("option", { value: v, selected: action.what === v }, t))));
        } else if (action.type === "wait") {
          body = el("label", { class: "field" }, "Secondes", el("input", { type: "number", min: 1, max: 600, value: action.seconds, oninput: (e) => { action.seconds = Number(e.target.value); } }));
        } else {
          const tool = catalog.find((t) => t.name === action.tool) || catalog[0];
          action.tool = tool.name;
          action.parameters = action.parameters || {};
          body = el("div", { class: "fields" },
            el("label", { class: "field" }, "Outil", el("select", { onchange: (e) => { action.tool = e.target.value; action.parameters = {}; render(); } },
              catalog.map((t) => el("option", { value: t.name, selected: t.name === tool.name }, TOOL_LABELS[t.name] || t.name)))),
            Object.entries(tool.parameters).map(([name, spec]) => paramInput(name, spec, action.parameters[name], (v) => {
              if (v === undefined) delete action.parameters[name]; else action.parameters[name] = v;
            }, names)));
        }
        return [el("div", { class: "arrow" }, "↓"), el("div", { class: `block ${{ tool: "action", announce: "say", alarm: "alarm" }[action.type] || action.type}` }, controls, body)];
      });

      view.replaceChildren(
        el("div", { class: "toolbar" }, el("h1", { class: "grow" }, routine ? `Modifier « ${routine.name} »` : "Nouvelle routine"),
          el("button", { class: "btn", onclick: () => show("routines") }, "Annuler"),
          el("button", { class: "btn primary", onclick: save }, "Enregistrer")),
        el("div", { class: "flow" },
          el("div", { class: "block" }, el("div", { class: "fields" },
            el("label", { class: "field" }, "Nom *", el("input", { type: "text", maxlength: 60, value: draft.name, oninput: (e) => { draft.name = e.target.value; } })),
            el("label", { class: "field" }, "Description", el("input", { type: "text", maxlength: 200, value: draft.description, oninput: (e) => { draft.description = e.target.value; } }))),
            el("div", { class: "row", style: "margin-top:10px" },
              el("label", { class: "row" }, el("input", { type: "checkbox", checked: draft.enabled, onchange: (e) => { draft.enabled = e.target.checked; } }), "Activée"),
              el("label", { class: "row" }, el("input", { type: "checkbox", checked: draft.once, onchange: (e) => { draft.once = e.target.checked; } }), "Une seule fois (s'efface après)"))),
          el("div", { class: "arrow" }, "↓"),
          el("div", { class: "block trigger" }, el("h3", {}, "Déclencheur"),
            el("div", { class: "fields" }, el("label", { class: "field" }, "Type", el("select", { onchange: (e) => { draft.trigger = newTrigger(e.target.value); render(); } },
              [["time", "Heure"], ["interval", "Intervalle"], ["event", "Événement de la maison"], ["manual", "Manuel"]].map(([v, t]) => el("option", { value: v, selected: trigger.type === v }, t)))),
              triggerFields)),
          blocks,
          el("div", { class: "arrow" }, "↓"),
          el("div", { class: "row" },
            el("button", { class: "btn", onclick: () => { draft.actions.push(newAction("tool", catalog)); render(); } }, "+ Action"),
            el("button", { class: "btn", onclick: () => { draft.actions.push(newAction("wait")); render(); } }, "+ Attente"),
            el("button", { class: "btn", onclick: () => { draft.actions.push(newAction("say")); render(); } }, "+ ORION dit"),
            el("button", { class: "btn", onclick: () => { draft.actions.push(newAction("announce")); render(); } }, "+ Annonce"),
            el("button", { class: "btn", onclick: () => { draft.actions.push(newAction("alarm")); render(); } }, "+ Réveil"))));
    };

    async function save() {
      try {
        await (routine ? api("PUT", `/routines/${routine.id}`, draft) : api("POST", "/routines", draft));
        toast("Routine enregistrée", "ok");
        show("routines");
      } catch (error) {
        toast(error.message, "err");
      }
    }

    render();
  }

  function newTrigger(type) {
    if (type === "time") return { type, time: "21:00", days: [] };
    if (type === "interval") return { type, minutes: 30 };
    if (type === "event") return { type, event: "arrival" };
    return { type: "manual" };
  }

  function newAction(type, catalog = []) {
    if (type === "say") return { type, text: "" };
    if (type === "wait") return { type, seconds: 2 };
    if (type === "alarm") return { type };
    if (type === "announce") return { type, what: "day" };
    return { type: "tool", tool: (catalog[0] || {}).name, parameters: {} };
  }

  // --- Appareils ------------------------------------------------------------------------------------

  async function devices() {
    const list = await api("GET", "/devices");
    const pcs = list.filter((d) => d.kind !== "light");
    const lights = list.filter((d) => d.kind === "light");
    const light = (d) => {
      const call = (tool, parameters, message) => act(api("POST", `/tools/${tool}`, { parameters: { room: d.id, ...parameters } }), message)
        .then(() => setTimeout(() => show("devices"), 300)).catch(() => {});
      const state = d.state || {};
      return el("div", { class: "card" },
        el("div", { class: "row" }, el("div", { class: "grow" }, el("h2", {}, dot(d.online), cap(d.name)),
          el("div", { class: "muted small" }, `Ampoule · ${d.address}`, d.online ? ` · ${state.on ? `allumée, ${state.brightness} %` : "éteinte"}` : " · injoignable")),
          el("button", { class: "btn", disabled: !d.online, onclick: () => call(state.on ? "light_off" : "light_on", {}) }, state.on ? "Éteindre" : "Allumer")),
        el("div", { class: "fields", style: "margin-top:12px" },
          el("label", { class: "field" }, "Luminosité", el("input", { type: "range", min: 1, max: 100, value: state.brightness || 100, disabled: !d.online,
            onchange: (e) => call("set_brightness", { brightness: Number(e.target.value) }) })),
          el("label", { class: "field" }, "Couleur", el("select", { disabled: !d.online, onchange: (e) => e.target.value && call("set_color", { color: e.target.value }) },
            el("option", { value: "" }, "—"), ["blanc chaud", "blanc neutre", "blanc froid", "rouge", "orange", "jaune", "vert", "cyan", "bleu", "violet", "magenta", "rose"]
              .map((c) => el("option", { value: c }, c)))),
          el("label", { class: "field" }, "Température (K)", el("input", { type: "number", min: 2700, max: 6500, step: 100, placeholder: "2700 à 6500", disabled: !d.online,
            onchange: (e) => e.target.value && call("set_color_temperature", { temperature: Number(e.target.value) }) }))),
        el("div", { class: "muted small", style: "margin-top:8px" }, "Alias : ", (d.aliases || []).map((a) => el("span", { class: "chip" }, a))));
    };
    view.replaceChildren(
      el("h1", {}, "Appareils"),
      el("h3", {}, "Ordinateurs"),
      el("div", { class: "grid" }, pcs.map((d) => el("div", { class: "card" },
        el("h2", {}, d.kind === "core" ? dot(null) : dot(d.online), cap(d.name), d.default ? el("span", { class: "chip" }, "par défaut") : null),
        el("div", { class: "muted small" }, d.kind === "core" ? "Core ORION · aucune action autorisée" : `PC · ${d.address} · ${d.online ? "agent connecté" : "agent injoignable"}`),
        el("div", { style: "margin-top:8px" }, d.features.map((f) => el("span", { class: "chip" }, f))),
        d.aliases.length ? el("div", { class: "muted small", style: "margin-top:6px" }, "Alias : ", d.aliases.join(", ")) : null))),
      el("h3", { style: "margin-top:22px" }, "Lumières"),
      lights.length ? el("div", { class: "grid" }, lights.map(light)) : el("div", { class: "empty" }, "Aucune lumière configurée."),
    );
  }

  // --- Historique -----------------------------------------------------------------------------------

  async function historyView() {
    const filters = state.historyFilters || (state.historyFilters = { kind: "routine", day: "", routine: "", result: "", search: "" });
    const items = await api("GET", `/history?kind=${filters.kind}&limit=500`);
    const routinesSeen = [...new Set(items.map((i) => i.routine).filter(Boolean))];
    const shown = items.filter((i) => (!filters.day || (i.time || "").startsWith(filters.day))
      && (!filters.routine || i.routine === filters.routine)
      && (!filters.result || (filters.result === "ok" ? i.success === true : i.success === false))
      && (!filters.search || `${i.subject} ${i.action || ""} ${i.message || ""}`.toLowerCase().includes(filters.search.toLowerCase())));
    const refresh = () => show("history");
    view.replaceChildren(
      el("h1", {}, "Historique"),
      el("div", { class: "toolbar" },
        el("select", { onchange: (e) => { filters.kind = e.target.value; refresh(); } },
          [["routine", "Routines"], ["tool", "Outils"], ["all", "Tout"]].map(([v, t]) => el("option", { value: v, selected: filters.kind === v }, t))),
        el("input", { type: "date", value: filters.day, onchange: (e) => { filters.day = e.target.value; refresh(); } }),
        el("select", { onchange: (e) => { filters.routine = e.target.value; refresh(); } },
          el("option", { value: "" }, "Toutes les routines"), routinesSeen.map((r) => el("option", { value: r, selected: filters.routine === r }, r))),
        el("select", { onchange: (e) => { filters.result = e.target.value; refresh(); } },
          [["", "Succès et erreurs"], ["ok", "Succès"], ["err", "Erreurs"]].map(([v, t]) => el("option", { value: v, selected: filters.result === v }, t))),
        el("input", { type: "search", placeholder: "Appareil, action…", value: filters.search, onchange: (e) => { filters.search = e.target.value; refresh(); } })),
      shown.length ? el("table", {},
        el("thead", {}, el("tr", {}, ["Heure", "Routine", "Événement", "Résultat"].map((h) => el("th", {}, h)))),
        el("tbody", {}, shown.map((i) => el("tr", {},
          el("td", { class: "small" }, when(i.time)),
          el("td", {}, i.routine || "—"),
          el("td", {}, i.action || i.subject, i.message ? el("div", { class: "muted small" }, i.message) : null),
          el("td", { class: i.success === true ? "ok" : i.success === false ? "err" : "" },
            i.success === true ? "✓ Succès" : i.success === false ? "✗ Erreur" : i.type.endsWith(".started") ? "Démarrée" : "—")))))
        : el("div", { class: "empty" }, "Aucun événement pour ces filtres."),
    );
  }

  // --- Paramètres -----------------------------------------------------------------------------------

  // Pastilles de couleur du visage : un clic l'applique sur tous les écrans du visage.
  function faceColors() {
    const box = el("div", { class: "face-colors" });
    const themes = (state.face && state.face.themes) || [];
    if (!themes.length) return el("div", { class: "muted small" }, "Visage indisponible sur le Core.");
    for (const t of themes) {
      const special = { auto: "linear-gradient(135deg, #3c8b6f 50%, #e8e8e8 50%)", day: "radial-gradient(circle, #c9a45c, #0f3d2e)",
        night: "radial-gradient(circle, #f2f2f2, #262626)",
        arcenciel: "conic-gradient(red, orange, yellow, lime, cyan, blue, magenta, red)" }[t.id];
      const tone = (h) => (h === "night" ? "#e8e8e8" : h === "day" ? "#5fae8f" : `hsl(${h} 100% 60%)`);
      const color = special
        || (t.hues === "rainbow" ? "conic-gradient(red, orange, yellow, lime, cyan, blue, magenta, red)"
          : Array.isArray(t.hues) ? `linear-gradient(135deg, ${t.hues.map(tone).join(", ")})`
          : `radial-gradient(circle, hsl(${t.hue} 100% 66%), hsl(${t.hue} 80% 22%))`);
      box.append(el("button", { class: "swatch" + (state.face.theme === t.id ? " active" : ""), title: t.label,
        style: `background:${color}`, onclick: async () => {
          await act(api("POST", "/tools/set_face_theme", { parameters: { theme: t.id } }), `Visage : ${t.label}`)
            .catch(() => null);
          await loadFace();
          show("settings");
        } }));
    }
    return box;
  }

  async function settingsView() {
    const s = await api("GET", "/settings");
    const form = { ...s, token: "" };
    const field = (label, input) => el("label", { class: "field" }, label, input);
    const check = (key, label) => el("label", { class: "row" }, el("input", { type: "checkbox", checked: form[key], onchange: (e) => { form[key] = e.target.checked; } }), label);
    view.replaceChildren(
      el("h1", {}, "Paramètres"),
      el("div", { class: "card", style: "max-width:720px" },
        el("div", { class: "fields" },
          field("Adresse du Core", el("input", { type: "text", value: form.core_url, oninput: (e) => { form.core_url = e.target.value; } })),
          field(`Jeton API ${s.token_configured ? "(configuré)" : "(manquant)"}`, el("input", { type: "password", placeholder: "Laisser vide pour garder l'actuel", autocomplete: "off", oninput: (e) => { form.token = e.target.value; } })),
          field("Nom du PC", el("input", { type: "text", maxlength: 40, value: form.pc_name, oninput: (e) => { form.pc_name = e.target.value; } })),
          field("Thème", el("select", { onchange: (e) => { form.theme = e.target.value; applyTheme(form.theme); } },
            THEMES.map(([v, t]) => el("option", { value: v, selected: form.theme === v }, t)))),
          field("Couleur du visage d'ORION (aussi à la voix : « mets ton visage en vert »)", faceColors()),
          field("Rafraîchissement (s)", el("input", { type: "number", min: 5, max: 3600, value: form.refresh_seconds, oninput: (e) => { form.refresh_seconds = Number(e.target.value); } }))),
        el("div", { class: "list", style: "margin-top:14px" },
          check("autostart", "Démarrer avec Windows (dans la zone de notification)"),
          check("notifications", "Notifications"),
          check("auto_connect", "Connexion automatique au Core")),
        el("div", { class: "row", style: "margin-top:16px" }, el("div", { class: "grow muted small" }, "Ctrl+Shift+O ouvre ou masque ORION Control."),
          el("button", { class: "btn primary", onclick: async () => {
            const body = { ...form };
            if (!body.token) delete body.token;
            delete body.token_configured;
            state.settings = await act(api("POST", "/settings", body), "Paramètres enregistrés").catch(() => null) || state.settings;
            applyTheme();
            show("settings");
          } }, "Enregistrer"))));
  }

  // --- Navigation -----------------------------------------------------------------------------------

  // --- Modules V3 (WATCH, NEWS, EVOLVE, PURCHASE, notifications) ------------------------------------------
  // Tout passe par /bridge/modules/<module>/… ; un module désactivé (ou absent du Core) affiche comment l'activer.

  const mod = (name, path = "", method = "GET", body) => api(method, `/modules/${name}${path}`, body);
  const stamp = (t) => (t ? new Date(t * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "—");

  function table(headers, rows, empty = "Rien pour l'instant.") {
    if (!rows.length) return el("div", { class: "empty" }, empty);
    return el("table", {}, el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((cells) => el("tr", {}, cells.map((c) => el("td", {}, c))))));
  }

  function cards(items) {
    return el("div", { class: "grid" }, items.map(([label, value, note]) =>
      el("div", { class: "card" }, el("div", { class: "muted small" }, label), el("div", { class: "big" }, String(value)),
        note ? el("div", { class: "muted small" }, note) : null)));
  }

  function button(label, onclick, kind = "") {
    return el("button", { class: `btn ${kind}`, onclick }, label);
  }

  async function moduleView(title, name, render) {
    let entry;
    try {
      entry = (await api("GET", "/modules")).find((m) => m.name === name);
    } catch (error) {
      entry = { state: error.message };
    }
    if (!entry || entry.state !== "actif") {
      const why = !entry ? "non activé" : entry.error ? `${entry.state} (${entry.error})` : entry.state;
      view.replaceChildren(el("h1", {}, title), el("div", { class: "banner" }, `Module ${why}. Pour l'activer : `
        + `[${name}] enabled = true dans la configuration du Core.`));
      return;
    }
    await render(entry.details || {});
  }

  async function watchView() {
    await moduleView("Surveillances", "watch", async (status) => {
      const watches = await mod("watch", "", "GET");
      const refresh = () => show("watch");
      const request = el("input", { type: "text", placeholder: "« Surveille les RTX 5090 et préviens-moi d'une bonne affaire »", style: "flex:1" });
      const act2 = (path, ok, method = "POST") => act(mod("watch", path, method), ok).then(refresh);
      const detail = el("div", {});
      async function open(id) {
        const d = await mod("watch", `/${encodeURIComponent(id)}`);
        detail.replaceChildren(el("h2", {}, d.name),
          el("div", { class: "muted small" }, `Condition : ${JSON.stringify(d.condition)} · Sources : ${d.sources.join(", ")}`),
          el("h3", {}, "Vérifications"),
          table(["Heure", "Résultat", "Résumé", "Erreurs"], d.checks.map((c) => [stamp(c.at), c.ok ? "✓" : "✗",
            JSON.stringify(c.summary), Object.entries(c.errors).map(([k, v]) => `${k} : ${v}`).join(" ; ") || "—"])),
          el("h3", {}, "Déjà signalé"),
          table(["Heure", "Titre", "Adresse"], d.found.map((f) => [stamp(f.at), f.title, f.url])),
          el("h3", {}, "Historique"),
          table(["Heure", "Événement", "Détail"], d.history.map((h) => [stamp(h.at), h.kind, JSON.stringify(h.detail)])));
      }
      view.replaceChildren(
        el("h1", {}, "Surveillances"),
        cards([["Surveillances", status.surveillances], ["Actives", status.actives], ["En échec", status.en_échec],
          ["Sources configurées", status.sources]]),
        el("div", { class: "toolbar" }, request, button("Créer", () => act(mod("watch", "", "POST", { request: request.value }),
          "Surveillance créée.").then(refresh), "primary")),
        table(["Nom", "État", "Dernière vérification", "Déclenchements", "Actions"], watches.map((w) => [
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); open(w.id); } }, w.name),
          w.state + (w.last_error ? ` (${w.last_error})` : ""), stamp(w.last_check_at), String(w.triggers),
          el("div", { class: "row" },
            w.state === "PAUSED" || w.state === "FAILED"
              ? button("Reprendre", () => act2(`/${w.id}/resume`, "Reprise."))
              : ["ACTIVE", "TRIGGERED"].includes(w.state) ? button("Pause", () => act2(`/${w.id}/pause`, "En pause.")) : null,
            ["ACTIVE", "TRIGGERED"].includes(w.state) ? button("Vérifier", () => act2(`/${w.id}/check`, "Vérifiée.")) : null,
            button("Supprimer", () => confirm(`Supprimer « ${w.name} » ?`) && act2(`/${w.id}`, "Supprimée.", "DELETE"), "danger"))]),
          "Aucune surveillance."),
        detail);
    });
  }

  async function newsView() {
    await moduleView("Actualités", "news", async (status) => {
      const [settings, stories, bulletins, alerts, sources] = await Promise.all([mod("news", "/settings"),
        mod("news", "/stories"), mod("news", "/bulletins"), mod("news", "/alerts"), mod("news", "/sources")]);
      const refresh = () => show("news");
      const times = el("input", { type: "text", value: settings.bulletin_times.join(", ") });
      const th = { ...settings.default_thresholds, ...settings.thresholds };
      const thresholds = Object.fromEntries(Object.entries(th).map(([k, v]) => [k, el("input", { type: "number", min: 0, max: 100, value: v, style: "width:5em" })]));
      const calls = el("input", { type: "number", min: 0, max: 10, value: settings.max_calls_per_day, style: "width:5em" });
      const interval = el("input", { type: "number", min: 5, max: 1440, value: settings.min_call_interval_minutes, style: "width:6em" });
      const alertsOn = el("input", { type: "checkbox", checked: settings.alerts_enabled });
      const bulletinsOn = el("input", { type: "checkbox", checked: settings.bulletins_enabled });
      const cats = Object.entries(settings.category_labels).map(([k, label]) =>
        [k, el("input", { type: "checkbox", checked: settings.categories.includes(k) }), label]);
      const save = () => act(mod("news", "/settings", "PUT", {
        bulletin_times: times.value.split(",").map((t) => t.trim()).filter(Boolean),
        thresholds: Object.fromEntries(Object.entries(thresholds).map(([k, i]) => [k, Number(i.value)])),
        max_calls_per_day: Number(calls.value), min_call_interval_minutes: Number(interval.value),
        alerts_enabled: alertsOn.checked, bulletins_enabled: bulletinsOn.checked,
        categories: cats.filter(([, box]) => box.checked).map(([k]) => k),
      }), "Réglages enregistrés.").then(refresh);
      const latest = bulletins[0];
      view.replaceChildren(
        el("h1", {}, "Actualités"),
        cards([["Sources", status.sources], ["Événements (48 h)", status["événements_48h"]],
          ["Dernier bulletin", stamp(status.dernier_bulletin)], ["Appels (24 h)", status.appels_24h],
          ["Synthèse LLM", status.llm ? "disponible" : "mode dégradé"]]),
        el("div", { class: "toolbar" }, button("Collecter maintenant", () => act(mod("news", "/collect", "POST"), "Collecte faite.").then(refresh)),
          button("Aperçu du bulletin", () => act(mod("news", "/bulletins", "POST"), "Aperçu composé (non envoyé).").then(refresh))),
        el("h2", {}, "Réglages"),
        el("div", { class: "list" },
          el("label", { class: "row" }, "Heures des bulletins ", times),
          el("div", { class: "row" }, "Seuils : ", Object.entries(thresholds).map(([k, i]) => el("label", {},
            ` ${{ bulletin: "bulletin", priority: "prioritaire", alert: "alerte", priority_alert: "alerte prioritaire" }[k] || k} `, i))),
          el("div", { class: "row" }, "Catégories : ", cats.map(([, box, label]) => el("label", {}, box, ` ${label} `))),
          el("div", { class: "row" }, el("label", {}, "Appels par jour ", calls), el("label", {}, " · écart (min) ", interval),
            el("label", {}, alertsOn, " alertes"), el("label", {}, bulletinsOn, " bulletins")),
          button("Enregistrer", save, "primary")),
        el("h2", {}, "Dernier bulletin"),
        latest ? el("pre", { class: "small", style: "white-space:pre-wrap" }, latest.text) : el("div", { class: "empty" }, "Aucun bulletin."),
        el("h2", {}, "Événements"),
        table(["Titre", "Catégorie", "Statut", "Score", "Niveau", "Sources"], stories.map((s) =>
          [s.title, s.category, s.status, String(s.score), s.tier, String(s.sources)])),
        el("h2", {}, "Alertes"),
        table(["Heure", "Titre", "Canaux", "Non envoyé"], alerts.map((a) => [stamp(a.at), a.title, a.channels.join(", ") || "—",
          Object.entries(a.skipped).map(([k, v]) => `${k} : ${v}`).join(" ; ")])),
        el("h2", {}, "Sources"),
        table(["Nom", "Catégorie", "Fiabilité", "Dernière lecture", "État"], sources.map((s) => [s.name, s.category,
          s.reliability, stamp(s.last_at), s.ok === 0 ? `✗ ${s.error}` : s.ok === 1 ? "✓" : "—"])));
    });
  }

  async function evolveView() {
    await moduleView("Évolution", "evolve", async (status) => {
      const [anomalies, patches, proposals, history] = await Promise.all([mod("evolve", "/anomalies"),
        mod("evolve", "/patches"), mod("evolve", "/proposals"), mod("evolve", "/history")]);
      const refresh = () => show("evolve");
      const detail = el("div", {});
      async function diagnose(sig) {
        const d = await act(mod("evolve", `/anomalies/${sig}`, "POST"));
        if (!d) return;
        detail.replaceChildren(el("h2", {}, "Diagnostic"), el("p", {}, d.summary),
          el("div", { class: "small" }, `Fichiers : ${d.files.join(", ") || "—"} · Dépendances : ${d.dependencies.join(", ") || "—"}`),
          el("div", { class: "small" }, `Hypothèses : ${d.hypotheses.join(" ; ")}`),
          el("div", { class: "small" }, `Pistes : ${d.solutions.join(" ; ") || "—"} · Confiance : ${d.confidence} (${d.by})`),
          el("pre", { class: "small", style: "white-space:pre-wrap" }, d.evidence.join("\n")));
      }
      view.replaceChildren(
        el("h1", {}, "Évolution"),
        cards([["Niveau", `${status.niveau} — ${status["niveau_libellé"]}`, status.remarque || "niveau 3 désactivé"],
          ["Anomalies", status.anomalies], ["Propositions en attente", status.propositions_en_attente],
          ["Correctifs", status.correctifs]]),
        el("div", { class: "toolbar" },
          button("Analyser le journal", () => act(mod("evolve", "/scan", "POST"), "Analyse faite.").then(refresh)),
          button("Niveau 1 (observateur)", () => act(mod("evolve", "/level", "PUT", { level: 1 }), "Niveau 1.").then(refresh)),
          button("Niveau 2 (développeur)", () => confirm("Autoriser EVOLVE à préparer des correctifs (branches, tests, commits) dans son espace isolé ?")
            && act(mod("evolve", "/level", "PUT", { level: 2 }), "Niveau 2.").then(refresh))),
        el("h2", {}, "Anomalies"),
        table(["Type", "Titre", "Occurrences", "Dernière", "Actions"], anomalies.map((a) => [a.kind, a.title, String(a.count),
          stamp(a.last_at), el("div", { class: "row" }, button("Diagnostic", () => diagnose(a.signature)),
            a.kind === "logicielle" && status.niveau >= 2 ? button("Préparer un correctif", () => act(mod("evolve", "/patches", "POST", { origin: a.signature }),
              "Correctif traité.").then(refresh)) : null)])),
        detail,
        el("h2", {}, "Correctifs"),
        table(["Heure", "Origine", "Branche", "État", "Vérifications", "Pull Request"], patches.map((p) => [stamp(p.at), p.origin,
          p.branch, p.state, (p.report.steps || []).map((s) => `${s["étape"]} ${s.ok ? "✓" : "✗"}`).join(" · ")
            + ((p.report.problems || []).length ? ` — ${p.report.problems.join(" ; ")}` : ""), p.pr || "—"])),
        el("h2", {}, "Propositions"),
        table(["Titre", "Origine", "Difficulté", "Statut", "Détail", "Décision"], proposals.map((p) => [p.title, p.origin,
          p.difficulty, p.status, `${p.description} Intérêt : ${p.interest} · Risques : ${p.risks} · Coût : ${p.cost}`,
          p.status === "en attente" ? el("div", { class: "row" },
            button("Accepter", () => act(mod("evolve", `/proposals/${p.id}/accept`, "POST"), "Acceptée.").then(refresh), "primary"),
            button("Refuser", () => act(mod("evolve", `/proposals/${p.id}/reject`, "POST"), "Refusée.").then(refresh), "danger")) : "—"])),
        el("h2", {}, "Historique"),
        table(["Heure", "Événement", "Élément", "Détail"], history.slice(0, 50).map((h) => [stamp(h.at), h.kind, h.item,
          JSON.stringify(h.detail)])));
    });
  }

  async function purchaseView() {
    await moduleView("Achats", "purchase", async (status) => {
      const [mandates, orders, opportunities] = await Promise.all([mod("purchase", "/mandates"), mod("purchase", "/orders"),
        mod("purchase", "/opportunities")]);
      const refresh = () => show("purchase");
      const f = { product: el("input", { type: "text", value: "RTX 5090" }), max_price: el("input", { type: "number", value: 2000 }),
        max_shipping: el("input", { type: "number", value: 0 }), total_budget: el("input", { type: "number", value: 2000 }),
        sellers: el("input", { type: "text", placeholder: "LDLC, Materiel.net" }), days: el("input", { type: "number", value: 30 }),
        level: el("select", {}, [1, 2, 3].map((l) => el("option", { value: l }, `Niveau ${l}`))) };
      const create = () => act(mod("purchase", "/mandates", "POST", {
        product: f.product.value, max_price: Number(f.max_price.value), max_shipping: Number(f.max_shipping.value),
        total_budget: Number(f.total_budget.value), sellers: f.sellers.value.split(",").map((s) => s.trim()).filter(Boolean),
        expires_at: Date.now() / 1000 + Number(f.days.value) * 86400, level: Number(f.level.value),
      }), "Mandat créé.").then(refresh);
      const orderAction = (id, action, body) => act(mod("purchase", `/orders/${id}/${action}`, "POST", body), "Fait.").then(refresh);
      view.replaceChildren(
        el("h1", {}, "Achats"),
        el("div", { class: "banner" }, "Bac à sable : aucun paiement ni aucune commande réels dans cette version."),
        cards([["Niveau", status.niveau], ["Mandats actifs", status.mandats_actifs], ["Budget autorisé", `${status["budget_autorisé"]} €`],
          ["Dépensé", `${status["dépensé"]} €`], ["En attente", status.en_attente],
          ["Arrêt global", status["arrêt_global"] ? "ACTIF" : "non"]]),
        el("div", { class: "toolbar" }, status["arrêt_global"]
          ? button("Reprendre les achats", () => act(mod("purchase", "/resume", "POST"), "Achats repris.").then(refresh))
          : button("Arrêt global des achats", () => act(mod("purchase", "/stop", "POST"), "Achats arrêtés.").then(refresh), "danger")),
        el("h2", {}, "Nouveau mandat"),
        el("div", { class: "row", style: "flex-wrap:wrap" }, Object.entries({ "Produit": f.product, "Prix max (€)": f.max_price,
          "Livraison max (€)": f.max_shipping, "Budget total (€)": f.total_budget, "Vendeurs autorisés": f.sellers,
          "Validité (jours)": f.days, "Niveau": f.level }).map(([label, input]) => el("label", {}, `${label} `, input)),
          button("Créer le mandat", () => confirm("Créer ce mandat d'achat ?") && create(), "primary")),
        el("h2", {}, "Mandats"),
        table(["Produit", "Vendeurs", "Prix max", "Budget restant", "Achats", "Expire", "Niveau", "État", ""], mandates.map((m) => [
          m.product, m.sellers.join(", "), `${m.max_price} € + ${m.max_shipping} €`, `${m.remaining} €`,
          `${m.purchases}/${m.max_purchases}`, stamp(m.expires_at), String(m.level), m.state,
          m.state === "ACTIVE" ? button("Révoquer", () => confirm("Révoquer ce mandat immédiatement ?")
            && act(mod("purchase", `/mandates/${m.id}/revoke`, "POST"), "Mandat révoqué.").then(refresh), "danger") : "—"])),
        el("h2", {}, "Commandes"),
        table(["Créée", "Produit", "Vendeur", "Total", "État", "Raison", ""], orders.map((o) => [stamp(o.created_at), o.offer.titre,
          o.offer.vendeur, `${o.total} €`, o.state, o.reason, el("div", { class: "row" },
            o.state === "AWAITING_CONFIRMATION" ? button("Confirmer l'achat", () => confirm(`Commander pour ${o.total} € ?`)
              && orderAction(o.id, "confirm"), "primary") : null,
            o.state === "AWAITING_AUTH" ? button("Authentifié (bac à sable)", () => orderAction(o.id, "auth", { authenticated: true })) : null,
            ["AWAITING_CONFIRMATION", "AWAITING_AUTH", "CONFIRMED"].includes(o.state) ? button("Annuler", () => orderAction(o.id, "cancel"), "danger") : null)])),
        el("h2", {}, "Offres évaluées"),
        table(["Heure", "Offre", "Vendeur", "Total", "Autorisée", "Bloquée par"], opportunities.slice(0, 50).map((o) => [stamp(o.at),
          o.title, o.seller, o.total === null ? "inconnu" : `${o.total} €`, o.allowed ? "✓" : "✗",
          o.checks.filter((c) => c.ok !== true).map((c) => `${c["critère"]}${c.ok === null ? " (inconnu)" : ""}`).join(", ") || "—"])));
    });
  }

  async function notificationsView() {
    await moduleView("Notifications", "notifications", async (status) => {
      const history = await mod("notifications", "/history");
      view.replaceChildren(
        el("h1", {}, "Notifications"),
        cards([["Présence", { home: "à la maison", away: "absent", unknown: "inconnue" }[status.presence] || status.presence],
          ["Appels (24 h)", `${status.calls_last_24h} / ${status.max_calls_per_day}`], ["Heures calmes", status.quiet_hours]]),
        table(["Heure", "Module", "Niveau", "Titre", "Message", "Canaux", "Non envoyé"], history.map((a) => [stamp(a.at), a.source,
          a.level, a.title, a.message, a.channels.join(", ") || "—",
          Object.entries(a.skipped).map(([k, v]) => `${k} : ${v}`).join(" ; ") || "—"]), "Aucune notification."));
    });
  }

  // --- CORE FOUNDATION : interrupteur d'autonomie, registre d'actions, autorisations ----------------------
  // Réactiver : ici seulement, après confirmation et la phrase exacte (vérifiée aussi par le Core).

  const AUTONOMY = { active: "Active", suspended: "Suspendue", emergency_stop: "Arrêt d'urgence" };
  const REACTIVATE = "réactiver l'autonomie";

  async function autonomyView() {
    let status;
    try {
      status = await api("GET", "/autonomy");
    } catch (error) {
      view.replaceChildren(el("h1", {}, "Autonomie"), el("div", { class: "banner" }, error.message),
        el("p", { class: "muted" }, "Activez [platform] dans la configuration du Core (docs/v3/foundation.md)."));
      return;
    }
    const [ledger, integrity, grants, tasks, confirmations] = await Promise.all([api("GET", "/ledger?limit=100"),
      api("GET", "/ledger/verify"), api("GET", "/grants"), api("GET", "/tasks"), api("GET", "/confirmations")]);
    const taskAction = async (task, verb, done) => {
      if (verb === "confirm" && !confirm(`Autoriser une fois « ${task.kind} » (${task.capability}) ?`)) return;
      await act(api("POST", `/tasks/${encodeURIComponent(task.id)}/${verb}`), done);
      show("autonomy");
    };
    const decide = async (c, verb, done) => {
      await act(api("POST", `/confirmations/${encodeURIComponent(c.id)}/${verb}`), done);
      show("autonomy");
    };
    const set = async (target) => {
      const body = { state: target, reason: "ORION Control" };
      const strict = { active: 0, suspended: 1, emergency_stop: 2 };
      if ((strict[target] ?? 9) < (strict[status.state] ?? 9)) {
        if (!confirm(`Réactiver l'autonomie (« ${AUTONOMY[target]} ») ?`)) return;
        const phrase = prompt(`Pour confirmer, tapez : ${REACTIVATE}`);
        if (phrase === null) return;
        body.confirmation = phrase;
      } else if (target === "emergency_stop" && !confirm("Arrêt d'urgence : plus aucune action autonome. Continuer ?")) {
        return;
      }
      await act(api("POST", "/autonomy", body), `Autonomie : ${AUTONOMY[target]}`);
      show("autonomy");
    };
    view.replaceChildren(
      el("h1", {}, "Autonomie"),
      cards([["État", AUTONOMY[status.state] || status.state, status.since ? `depuis le ${stamp(status.since)} (${status.changed_by})` : "réglage par défaut"],
        ["Registre d'actions", `${integrity.count} lignes`, integrity.ok ? "chaînage intact" : `ALTÉRÉ à la ligne ${integrity.broken_at} : ${integrity.problem}`],
        ["Empreinte", integrity.head.slice(0, 16) + "…", "à noter pour comparer plus tard"]]),
      el("div", { class: "row" },
        button("Activer", () => set("active"), "primary"),
        button("Suspendre", () => set("suspended")),
        button("Arrêt d'urgence", () => set("emergency_stop"), "danger")),
      el("p", { class: "muted small" }, "Suspendue : plus aucune initiative des modules ; vos tâches programmées continuent. "
        + "Arrêt d'urgence : plus aucune action autonome. La voix peut suspendre ou arrêter, jamais réactiver."),
      el("h2", {}, "Autorisations"),
      table(["Principal", "Capacité", "Limites", "Échéance", "État", ""], grants.map((g) => [g.principal, g.capability,
        JSON.stringify(g.limits), stamp(g.expires_at), g.revoked_at ? `révoquée ${stamp(g.revoked_at)}` : "valide",
        g.revoked_at ? "" : button("Révoquer", async () => { await act(api("DELETE", `/grants/${encodeURIComponent(g.id)}`), "Autorisation révoquée"); show("autonomy"); }, "danger")]),
        "Aucune autorisation : les actions sensibles autonomes demandent votre confirmation."),
      el("h2", {}, "Confirmations en attente"),
      table(["Demande", "Origine", "Action", "État", "Expire", ""], confirmations.map((c) => [c.summary || c.id,
        c.principal, c.action, c.state, stamp(c.expires_at), el("div", { class: "row" },
          c.state === "en attente" ? button("Accorder une fois", () => decide(c, "approve", "Confirmation accordée"), "primary") : "",
          button(c.state === "en attente" ? "Refuser" : "Retirer", () => decide(c, "refuse", "Confirmation retirée"), "danger"))]),
        "Aucune confirmation en attente."),
      el("h2", {}, "Tâches"),
      table(["Tâche", "Origine", "Capacité", "État", "Prochaine exécution", "Essais", "Dernier message", ""],
        tasks.tasks.map((t) => [t.kind, `${t.origin === "utilisateur" ? "programmée par vous" : "initiative"} (${t.principal})`,
          t.capability, TASK_STATES[t.state] || t.state, stamp(t.run_at), `${t.attempts}/${t.max_attempts}`, t.last_error,
          el("div", { class: "row" },
            t.state === "held" ? button("Confirmer", () => taskAction(t, "confirm", "Tâche confirmée"), "primary") : "",
            ["interrupted", "failed"].includes(t.state) ? button("Relancer", () => taskAction(t, "retry", "Tâche relancée")) : "",
            ["pending", "held", "running"].includes(t.state) ? button("Annuler", () => taskAction(t, "cancel", "Tâche annulée"), "danger") : "")]),
        "Aucune tâche persistante."),
      el("p", { class: "muted small" }, "Une tâche interrompue en pleine exécution n'est jamais relancée seule : "
        + "son résultat est inconnu. Les autorisations sont relues à chaque exécution."),
      el("h2", {}, "Registre d'actions"),
      table(["#", "Heure", "Origine", "Capacité", "Action", "Décision", "Raison"], ledger.entries.map((e) => [e.id, stamp(e.at),
        e.principal, e.capability, e.action, e.decision, e.reason]), "Registre vide."));
  }

  const TASK_STATES = { pending: "en attente", running: "en cours", held: "à confirmer", done: "faite",
    failed: "en échec", expired: "expirée", cancelled: "annulée", interrupted: "interrompue" };

  const VIEWS = { dashboard, routines, devices, history: historyView, settings: settingsView, watch: watchView,
    news: newsView, evolve: evolveView, purchase: purchaseView, notifications: notificationsView,
    autonomy: autonomyView };

  const THEMES = [["auto", "ORION automatique (nuit de 22 h à 7 h)"],
    ["visage", "Comme le visage d'ORION (sa couleur du moment)"], ["jarvis", "ORION (jour)"],
    ["nuit", "ORION nuit (noir et blanc)"], ["rouge", "ORION rouge"], ["system", "Système"], ["dark", "Sombre"],
    ["light", "Clair"]];
  const FACE_THEMES = ["jarvis", "nuit", "rouge", "teinte"];
  let rainbowHue = 0, partyStep = 0;

  // Couleur du visage (choisie à la voix ou ici) : lue sur le Core, reprise par le thème « visage ».
  async function loadFace() {
    try {
      state.face = await api("GET", "/face");
    } catch (error) {
      state.face = null;
    }
    applyTheme();
  }
  setInterval(loadFace, 30000);

  function faceTheme() {
    const face = state.face && state.face.theme;
    const entry = state.face && state.face.themes.find((t) => t.id === face);
    if (face === "day") return ["jarvis"];
    if (face === "night") return ["nuit"];
    if (face === "arcenciel" || (entry && entry.hues === "rainbow")) return ["teinte", (rainbowHue = (rainbowHue + 30) % 360)];
    if (entry && Array.isArray(entry.hues)) {
      const numbers = entry.hues.filter((h) => typeof h === "number");
      if (numbers.length) return ["teinte", numbers[(partyStep = (partyStep + 1) % numbers.length)]];
    }
    if (entry && entry.hue !== null && entry.hue !== undefined) return ["teinte", entry.hue];
    return [new Date().getHours() >= 22 || new Date().getHours() < 7 ? "nuit" : "jarvis"];
  }

  // Thèmes du visage : « auto » suit les mêmes heures que lui (nuit de 22 h à 7 h). « preview » : aperçu non enregistré.
  function applyTheme(preview) {
    let theme = preview || (state.settings && state.settings.theme) || "auto";
    const chosen = theme;
    if (theme === "auto") {
      const hour = new Date().getHours();
      theme = hour >= 22 || hour < 7 ? "nuit" : "jarvis";
    }
    if (theme === "visage") {
      const [name, hue] = faceTheme();
      theme = name;
      if (hue !== undefined) document.documentElement.style.setProperty("--h", hue);
    }
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.skin = FACE_THEMES.includes(theme) ? "face" : "";
    document.querySelectorAll("#themes button").forEach((b) => b.classList.toggle("active", b.dataset.theme === chosen));
  }
  setInterval(() => applyTheme(), 60000);
  setInterval(() => {
    const entry = state.face && state.face.themes.find((t) => t.id === state.face.theme);
    if (state.face && (state.face.theme === "arcenciel" || (entry && entry.hues))) applyTheme();
  }, 4000);

  // Sélecteur rapide du menu : un clic applique et enregistre le thème.
  document.querySelectorAll("#themes button").forEach((b) => b.addEventListener("click", async () => {
    applyTheme(b.dataset.theme);
    try {
      state.settings = await api("POST", "/settings", { theme: b.dataset.theme });
    } catch (error) {
      toast(error.message, "err");
    }
    applyTheme();
  }));

  // Comme le visage : rouge tant qu'un composant indispensable est en panne (worker LLM, agents).
  function setAlert(problems) {
    document.documentElement.dataset.alert = problems.length ? "1" : "";
    const banner = document.getElementById("alert");
    if (banner) {
      banner.hidden = !problems.length;
      banner.textContent = problems.join(" · ");
    }
  }

  async function show(name) {
    state.view = name;
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    try {
      await VIEWS[name]();
    } catch (error) {
      if (name === "dashboard") setConnection(false, "Core injoignable");
      view.replaceChildren(el("h1", {}, name === "settings" ? "Paramètres" : "Connexion au Core"),
        el("div", { class: "banner" }, error.message),
        name === "settings" ? null : el("button", { class: "btn", onclick: () => show("settings") }, "Vérifier les paramètres"));
    }
  }

  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  document.addEventListener("keydown", (e) => { if (e.key === "F5") { e.preventDefault(); show(state.view); } });

  (async () => {
    try {
      state.settings = await api("GET", "/settings");
    } catch (error) {
      toast(error.message, "err");
    }
    applyTheme();
    loadFace();
    await show("dashboard");
    setInterval(() => { if (state.view === "dashboard" && document.visibilityState === "visible") show("dashboard"); },
      ((state.settings && state.settings.refresh_seconds) || 15) * 1000);
  })();
})();
