// Sid / KeyAccountMin — Frontend-Logik (NT-548)
// Vanilla JS, keine Bibliotheken. Drei-Ebenen-Tab-Navigation, Feld-Editor mit
// on-blur PUT, Sprachen-Modal, Stale-Markierung.

const state = {
  items: [],
  languages: [],          // Steam-Sprachen-Liste (29)
  langByCode: {},         // code -> {code, iso, display, native}
  fields: [],             // Feld-Metadaten aus /api/fields
  currentPlatform: "steam",
  currentItemKey: null,   // "steam:1141975"
  currentSubTab: "inhalt",
  currentTargetLang: null,
  // pro Item gecached: meta + master + translations-Summary + voll-translation-bei-Bedarf
  itemCache: {},
};

// Plattformen — heute nur Steam aktiv. Top-Level-Tabs sind hardcoded;
// "+ neuer Reiter" ist als Hint sichtbar.
const PLATFORMS = [
  { code: "steam",   label: "Steam",   active: true  },
  { code: "windows", label: "Windows", active: false },
  { code: "xbox",    label: "XBox",    active: false },
  { code: "android", label: "Android", active: false },
];

const SUB_TABS = [
  { code: "inhalt",   label: "Inhalt",   active: true  },
  { code: "grafiken", label: "Grafiken", active: false, hint: "Phase 2" },
];

// --- Boot --------------------------------------------------------------------

async function boot() {
  try {
    const [items, langs, fields, version] = await Promise.all([
      fetch("/api/items").then(r => r.json()),
      fetch("/api/languages").then(r => r.json()),
      fetch("/api/fields").then(r => r.json()),
      fetch("/api/version").then(r => r.json()),
    ]);
    state.items = items.items;
    state.languages = langs.languages;
    state.langByCode = Object.fromEntries(langs.languages.map(l => [l.code, l]));
    state.fields = fields.fields;
    document.getElementById("version").textContent = version.version;

    // Anfangs auf erstes Item der ersten Plattform springen
    const firstSteam = state.items.find(i => i.platform === "steam");
    if (firstSteam) {
      state.currentItemKey = `${firstSteam.platform}:${firstSteam.item_id}`;
    }
    renderPlatformTabs();
    renderItemTabs();
    renderSubTabs();
    await renderContent();
  } catch (err) {
    console.error("Boot failed:", err);
    document.getElementById("content").innerHTML =
      `<div class="card error">Fehler beim Laden: ${escapeHtml(String(err))}</div>`;
  }
}

// --- Tabs --------------------------------------------------------------------

function renderPlatformTabs() {
  const nav = document.getElementById("tabs-platform");
  const buttons = PLATFORMS.map(p => {
    const active = p.code === state.currentPlatform;
    const cls = ["tab"];
    if (active) cls.push("active");
    if (!p.active) cls.push("disabled");
    return `<button class="${cls.join(" ")}" data-platform="${p.code}" ${p.active ? "" : "disabled aria-disabled='true'"}>${p.label}</button>`;
  }).join("");
  nav.innerHTML = buttons + `<button class="tab add" id="btn-new-platform" title="Phase 8 — neue Plattform/Kunde anlegen" disabled>+</button>`;
  for (const b of nav.querySelectorAll("button[data-platform]")) {
    b.addEventListener("click", e => {
      const p = e.currentTarget.dataset.platform;
      if (PLATFORMS.find(x => x.code === p)?.active) {
        state.currentPlatform = p;
        const firstItem = state.items.find(i => i.platform === p);
        state.currentItemKey = firstItem ? `${firstItem.platform}:${firstItem.item_id}` : null;
        renderPlatformTabs(); renderItemTabs(); renderContent();
      }
    });
  }
}

function renderItemTabs() {
  const nav = document.getElementById("tabs-item");
  const items = state.items.filter(i => i.platform === state.currentPlatform);
  const buttons = items.map(it => {
    const key = `${it.platform}:${it.item_id}`;
    const active = key === state.currentItemKey;
    return `<button class="tab ${active ? "active" : ""}" data-item="${key}">
      ${escapeHtml(it.name)}<span class="muted"> #${it.item_id}</span>
    </button>`;
  }).join("");
  nav.innerHTML = buttons + `<button class="tab add" id="btn-new-item" title="Neues Item">+</button>`;
  for (const b of nav.querySelectorAll("button[data-item]")) {
    b.addEventListener("click", e => {
      state.currentItemKey = e.currentTarget.dataset.item;
      state.currentTargetLang = null; // reset
      renderItemTabs(); renderContent();
    });
  }
  document.getElementById("btn-new-item").addEventListener("click", () => openNewItemModal());
}

function renderSubTabs() {
  const nav = document.getElementById("tabs-sub");
  const buttons = SUB_TABS.map(t => {
    const active = t.code === state.currentSubTab;
    const cls = ["tab"];
    if (active) cls.push("active");
    if (!t.active) cls.push("disabled");
    const hint = t.hint ? ` <span class="muted">(${t.hint})</span>` : "";
    return `<button class="${cls.join(" ")}" data-sub="${t.code}" ${t.active ? "" : "disabled"}>${t.label}${hint}</button>`;
  }).join("");
  nav.innerHTML = buttons + `<button class="tab" id="btn-langs" title="Aktive Sprachen verwalten">🌐 Sprachen</button>`;
  for (const b of nav.querySelectorAll("button[data-sub]")) {
    b.addEventListener("click", e => {
      const s = e.currentTarget.dataset.sub;
      if (SUB_TABS.find(x => x.code === s)?.active) {
        state.currentSubTab = s; renderSubTabs(); renderContent();
      }
    });
  }
  document.getElementById("btn-langs").addEventListener("click", () => openLanguagesModal());
}

// --- Content (Inhalt-Tab) ----------------------------------------------------

async function renderContent() {
  const main = document.getElementById("content");
  if (!state.currentItemKey) {
    main.innerHTML = `<div class="card">
      <h2>Kein Item ausgewaehlt</h2>
      <p class="muted">Lege ein neues Item an oder waehle eines aus den Reitern oben.</p>
    </div>`;
    return;
  }
  const [platform, itemId] = state.currentItemKey.split(":");
  let it = state.itemCache[state.currentItemKey];
  if (!it) {
    it = await fetch(`/api/items/${platform}/${itemId}`).then(r => r.json());
    state.itemCache[state.currentItemKey] = it;
  }
  // Aktive Sprachen ohne Master
  const targetLangs = it.meta.active_languages.filter(l => l !== it.meta.master_lang);
  if (!state.currentTargetLang || !targetLangs.includes(state.currentTargetLang)) {
    state.currentTargetLang = targetLangs[0] || null;
  }

  if (state.currentSubTab === "inhalt") {
    main.innerHTML = renderInhalt(it, targetLangs);
    bindInhaltHandlers(it);
    if (state.currentTargetLang) {
      await loadAndRenderTargetLang(it, state.currentTargetLang);
    }
  }
}

function renderInhalt(it, targetLangs) {
  const masterLang = it.meta.master_lang;
  const masterDisplay = state.langByCode[masterLang]?.display || masterLang;
  const targetOptions = targetLangs.map(code => {
    const l = state.langByCode[code];
    const summary = it.translations[code];
    const stale = summary?.stale > 0 ? ` (${summary.stale} stale)` : "";
    return `<option value="${code}" ${code === state.currentTargetLang ? "selected" : ""}>${escapeHtml(l?.display || code)}${stale}</option>`;
  }).join("");

  const targetHeader = targetLangs.length === 0
    ? `<span class="muted">Keine Zielsprache aktiv. <button class="link" id="btn-langs-inline">Sprachen aktivieren</button></span>`
    : `<label>Zielsprache: <select id="select-target-lang">${targetOptions}</select></label>`;

  const eaToggle = `
    <label class="checkbox-line">
      <input type="checkbox" id="toggle-ea" ${it.meta.early_access ? "checked" : ""}>
      Early Access — pflege EA-Q&amp;A-Felder
    </label>`;

  const standardFields = state.fields.filter(f => f.block === "standard");
  const eaFields = state.fields.filter(f => f.block === "early_access");

  const renderFieldGroup = (fields, title) => {
    if (!fields.length) return "";
    const rows = fields.map(f => renderFieldRow(it, f)).join("");
    return `<section class="card">
      <h2>${escapeHtml(title)}</h2>
      <div class="fields">${rows}</div>
    </section>`;
  };

  return `
    <section class="card item-header">
      <h2>${escapeHtml(it.meta.name)} <span class="muted">#${it.meta.item_id} · ${it.meta.platform}</span></h2>
      <div class="header-controls">
        <span>Master: <strong>${escapeHtml(masterDisplay)}</strong></span>
        <span class="sep">·</span>
        ${targetHeader}
        <span class="sep">·</span>
        ${eaToggle}
      </div>
    </section>
    ${renderFieldGroup(standardFields, "Inhalt")}
    ${it.meta.early_access ? renderFieldGroup(eaFields, "Early Access (Q&A)") : ""}
  `;
}

function renderFieldRow(it, fieldMeta) {
  const masterValue = it.master.fields[fieldMeta.field] ?? "";
  const langCode = state.currentTargetLang || "";
  const masterField = fieldMeta.multiline
    ? `<textarea class="editor master" data-kind="master" data-field="${fieldMeta.field}">${escapeHtml(masterValue)}</textarea>`
    : `<input type="text" class="editor master" data-kind="master" data-field="${fieldMeta.field}" value="${escapeHtml(masterValue)}">`;
  const targetField = fieldMeta.multiline
    ? `<textarea class="editor target" data-kind="target" data-field="${fieldMeta.field}" ${langCode ? "" : "disabled"}></textarea>`
    : `<input type="text" class="editor target" data-kind="target" data-field="${fieldMeta.field}" ${langCode ? "" : "disabled"}>`;
  return `<div class="field-row" data-field="${fieldMeta.field}">
    <div class="field-label">
      <strong>${escapeHtml(fieldMeta.label)}</strong>
      ${fieldMeta.hint ? `<div class="hint muted">${escapeHtml(fieldMeta.hint)}</div>` : ""}
    </div>
    <div class="field-master">
      <label class="lang-tag">DE</label>
      ${masterField}
    </div>
    <div class="field-target">
      <label class="lang-tag" data-target-lang="${langCode}">${langCode ? state.langByCode[langCode]?.iso || langCode : "—"}</label>
      ${targetField}
      <div class="field-flags"></div>
    </div>
  </div>`;
}

function bindInhaltHandlers(it) {
  // EA-Toggle
  const eaCb = document.getElementById("toggle-ea");
  if (eaCb) eaCb.addEventListener("change", async e => {
    const enabled = e.target.checked;
    const r = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/early-access`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(r => r.json());
    if (!r.ok) { alert("Fehler beim EA-Toggle"); return; }
    delete state.itemCache[state.currentItemKey];
    await renderContent();
  });

  // Zielsprache wechseln
  const sel = document.getElementById("select-target-lang");
  if (sel) sel.addEventListener("change", async e => {
    state.currentTargetLang = e.target.value;
    delete state.itemCache[state.currentItemKey]; // Translations-Summary neu laden
    await renderContent();
  });

  const langsInline = document.getElementById("btn-langs-inline");
  if (langsInline) langsInline.addEventListener("click", () => openLanguagesModal());

  // Master-Editor: on-blur PUT
  for (const ed of document.querySelectorAll(".editor.master")) {
    ed.addEventListener("blur", async e => {
      const field = e.target.dataset.field;
      const value = e.target.value;
      const oldValue = it.master.fields[field] ?? "";
      if (value === oldValue) return;
      try {
        const r = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/master/${field}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        }).then(r => r.json());
        if (!r.ok) throw new Error("save failed");
        // Cache invalidieren und Content neu rendern, damit Stale-Counts und
        // Badges in den Translation-Summary-Karten aktuell sind. Ohne das
        // bleiben alte Stale-Zahlen sichtbar bis zur naechsten Tab-Navigation.
        it.master.fields[field] = value;
        delete state.itemCache[state.currentItemKey];
        flashOk(e.target);
        await renderContent();
      } catch (err) {
        alert("Master speichern fehlgeschlagen: " + err);
      }
    });
  }

  // Target-Editor: on-blur PUT
  for (const ed of document.querySelectorAll(".editor.target")) {
    ed.addEventListener("blur", async e => {
      if (!state.currentTargetLang) return;
      const field = e.target.dataset.field;
      const value = e.target.value;
      const oldValue = e.target.dataset.lastValue ?? "";
      if (value === oldValue) return;
      try {
        const r = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/translation/${state.currentTargetLang}/${field}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        }).then(r => r.json());
        if (!r.ok) throw new Error("save failed");
        e.target.dataset.lastValue = value;
        // Manually-edited badge zeigen
        const flags = e.target.parentElement.querySelector(".field-flags");
        if (flags) flags.innerHTML = `<span class="flag manual">manuell editiert</span>`;
        // stale-Class entfernen
        e.target.parentElement.classList.remove("stale");
        flashOk(e.target);
        // Cache invalidieren UND neu rendern. Ohne Re-Render bleibt
        // it.translations[code].stale/manually_edited/filled im Sprachselektor
        // veraltet, bis manuell die Sprache gewechselt wird (Lisbeth NT-548
        // 12:37, LOW FUNCTIONAL).
        delete state.itemCache[state.currentItemKey];
        await renderContent();
      } catch (err) {
        alert("Translation speichern fehlgeschlagen: " + err);
      }
    });
  }
}

async function loadAndRenderTargetLang(it, lang) {
  const t = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/translation/${lang}`).then(r => r.json());
  if (t.detail) return; // 404
  // Race-Schutz: state.currentTargetLang kann sich waehrend des fetch
  // geaendert haben (User wechselt schnell die Sprache). Wenn die Antwort
  // nicht mehr zur aktuellen Auswahl passt, abbrechen — sonst zeigt der
  // Editor Inhalte einer anderen Sprache mit den passenden stale/manual
  // Badges (Lisbeth NT-548 12:51, MEDIUM FUNCTIONAL).
  if (state.currentTargetLang !== lang) return;
  for (const row of document.querySelectorAll(".field-row")) {
    const field = row.dataset.field;
    const tf = t.fields[field];
    const targetEditor = row.querySelector(".editor.target");
    const flags = row.querySelector(".field-flags");
    const langTag = row.querySelector(".field-target .lang-tag");
    if (!tf) {
      targetEditor.value = "";
      targetEditor.dataset.lastValue = "";
      flags.innerHTML = "";
      row.classList.remove("stale");
      if (langTag) langTag.textContent = state.langByCode[lang]?.iso || lang;
      continue;
    }
    targetEditor.value = tf.value || "";
    targetEditor.dataset.lastValue = tf.value || "";
    if (langTag) langTag.textContent = state.langByCode[lang]?.iso || lang;
    if (tf.stale) row.classList.add("stale"); else row.classList.remove("stale");
    let flagHtml = "";
    if (tf.stale) flagHtml += `<span class="flag stale">stale</span>`;
    if (tf.manually_edited) flagHtml += `<span class="flag manual">manuell editiert</span>`;
    flags.innerHTML = flagHtml;
  }
}

// --- Modal: Sprachen ---------------------------------------------------------

async function openLanguagesModal() {
  if (!state.currentItemKey) { alert("Erst ein Item waehlen oder anlegen."); return; }
  const [platform, itemId] = state.currentItemKey.split(":");
  const it = state.itemCache[state.currentItemKey] || await fetch(`/api/items/${platform}/${itemId}`).then(r => r.json());
  state.itemCache[state.currentItemKey] = it;
  const active = new Set(it.meta.active_languages);
  const masterLang = it.meta.master_lang;

  const checks = state.languages.map(l => {
    const isMaster = l.code === masterLang;
    const checked = active.has(l.code);
    return `<label class="lang-check ${isMaster ? "master" : ""}">
      <input type="checkbox" value="${l.code}" ${checked ? "checked" : ""} ${isMaster ? "disabled" : ""}>
      <span class="lang-display">${escapeHtml(l.display)}</span>
      <span class="muted">${escapeHtml(l.native)} <code>${l.iso}</code></span>
      ${isMaster ? `<span class="muted">(Master)</span>` : ""}
    </label>`;
  }).join("");

  showModal(`
    <h2>Sprachen fuer ${escapeHtml(it.meta.name)}</h2>
    <p class="muted">Master <strong>${escapeHtml(state.langByCode[masterLang]?.display || masterLang)}</strong> bleibt immer aktiv.
    Aktivierte Sprachen bekommen ein leeres Translation-Stub-File und sind im Editor sichtbar.</p>
    <div class="langs-grid">${checks}</div>
    <div class="modal-actions">
      <button class="btn" id="btn-cancel-langs">Abbrechen</button>
      <button class="btn primary" id="btn-save-langs">Speichern</button>
    </div>
  `);
  document.getElementById("btn-cancel-langs").addEventListener("click", closeModal);
  document.getElementById("btn-save-langs").addEventListener("click", async () => {
    const langs = [...document.querySelectorAll(".lang-check input:not(:disabled):checked")].map(c => c.value);
    langs.unshift(masterLang);
    const r = await fetch(`/api/items/${platform}/${itemId}/active-languages`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ languages: langs }),
    }).then(r => r.json());
    if (!r.ok) { alert("Speichern fehlgeschlagen"); return; }
    closeModal();
    delete state.itemCache[state.currentItemKey];
    // items neu laden (active_languages-Liste hat sich geaendert)
    state.items = (await fetch("/api/items").then(r => r.json())).items;
    state.currentTargetLang = null;
    await renderContent();
  });
}

// --- Modal: neues Item -------------------------------------------------------

function openNewItemModal() {
  showModal(`
    <h2>Neues Item — Plattform ${escapeHtml(state.currentPlatform)}</h2>
    <form id="form-new-item">
      <label>Item-ID (Steam: AppID)<input name="item_id" required pattern="[A-Za-z0-9_-]+" autofocus></label>
      <label>Anzeigename<input name="name" required></label>
      <label class="checkbox-line"><input type="checkbox" name="early_access"> Early Access</label>
      <div class="modal-actions">
        <button type="button" class="btn" id="btn-cancel-new">Abbrechen</button>
        <button type="submit" class="btn primary">Anlegen</button>
      </div>
    </form>
  `);
  document.getElementById("btn-cancel-new").addEventListener("click", closeModal);
  document.getElementById("form-new-item").addEventListener("submit", async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      platform: state.currentPlatform,
      item_id: fd.get("item_id"),
      name: fd.get("name"),
      early_access: fd.get("early_access") === "on",
    };
    const r = await fetch("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.status === 409) { alert("Item-ID existiert bereits."); return; }
    if (!r.ok) { alert("Anlage fehlgeschlagen: " + await r.text()); return; }
    closeModal();
    state.items = (await fetch("/api/items").then(r => r.json())).items;
    state.currentItemKey = `${body.platform}:${body.item_id}`;
    state.currentTargetLang = null;
    renderItemTabs();
    await renderContent();
  });
}

// --- Modal-Helper ------------------------------------------------------------

function showModal(html) {
  const m = document.getElementById("modal");
  m.querySelector(".modal-content").innerHTML = html;
  m.showModal();
}

function closeModal() {
  document.getElementById("modal").close();
}

// --- Util --------------------------------------------------------------------

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function flashOk(el) {
  el.classList.add("saved");
  setTimeout(() => el.classList.remove("saved"), 700);
}

document.addEventListener("DOMContentLoaded", boot);
