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
  // Request-Token pro Item/Sprach-Kombination (Lisbeth NT-548 Pass 6,
  // MEDIUM FUNCTIONAL): jeder loadAndRenderTargetLang-Aufruf zaehlt hoch,
  // nur die Antwort mit dem aktuellsten Token darf rendern. Sonst kann eine
  // langsamere Vorgaenger-Antwort (z.B. die Pre-Save-Anfrage) eine spaetere
  // schon gespeicherte Anzeige ueberschreiben.
  langRenderTokens: {},
  assetsCatalog: null,    // /api/assets/catalog (einmal geladen)
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
  { code: "grafiken", label: "Grafiken", active: true },
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
  nav.innerHTML = buttons + `
    <button class="tab" id="btn-langs" title="Aktive Sprachen verwalten">🌐 Sprachen</button>
    <button class="tab" id="btn-glossary" title="Glossar fuer dieses Item">📖 Glossar</button>`;
  for (const b of nav.querySelectorAll("button[data-sub]")) {
    b.addEventListener("click", e => {
      const s = e.currentTarget.dataset.sub;
      if (SUB_TABS.find(x => x.code === s)?.active) {
        state.currentSubTab = s; renderSubTabs(); renderContent();
      }
    });
  }
  document.getElementById("btn-langs").addEventListener("click", () => openLanguagesModal());
  document.getElementById("btn-glossary").addEventListener("click", () => openGlossaryModal());
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
  const key = state.currentItemKey;
  const [platform, itemId] = key.split(":");
  // NT-550/551 Pass 24 (Lisbeth 14:31 LOW + 14:33 MEDIUM FUNCTIONAL):
  // renderContent() laeuft async und kann parallel feuern (onerror + close-
  // Handler nacheinander, oder Tab-Wechsel waehrend laufendem fetch). Ohne
  // Versionierung kann ein langsamerer Request den Cache mit veralteten
  // Daten ueberschreiben, nachdem ein neuerer Request schon committed hat.
  // Token-Schema: vor dem await wird der aktuelle Token gemerkt; nach dem
  // await wird er gegen den aktuellen verglichen. Nur die neueste Iteration
  // darf Cache + DOM aktualisieren.
  state.renderTokens = state.renderTokens || {};
  const token = (state.renderTokens[key] || 0) + 1;
  state.renderTokens[key] = token;
  const isCurrent = () => state.renderTokens[key] === token && state.currentItemKey === key;

  let it = state.itemCache[key];
  if (!it) {
    // NT-549 Pass 4 (Lisbeth 15:10 LOW FUNCTIONAL): mit dem 422-Pfad fuer
    // schema-invalide meta/master.json kann GET /api/items/... jetzt non-200
    // sein. Vorher wurde response.ok ignoriert -> renderContent crasht beim
    // it.meta-Zugriff. Jetzt: Fehlerstatus -> graceful Card mit Detail.
    const r = await fetch(`/api/items/${platform}/${itemId}`);
    if (!isCurrent()) return; // neuerer Aufruf ist drueber, abbrechen
    if (!r.ok) {
      let detail = `${r.status} ${r.statusText}`;
      try {
        const body = await r.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) { /* nicht JSON */ }
      if (!isCurrent()) return;
      main.innerHTML = `<div class="card">
        <h2>Item ${escapeHtml(platform)}/${escapeHtml(itemId)} nicht ladbar</h2>
        <p class="muted">${escapeHtml(String(detail))}</p>
      </div>`;
      return;
    }
    it = await r.json();
    if (!isCurrent()) return;
    state.itemCache[key] = it;
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
  } else if (state.currentSubTab === "grafiken") {
    main.innerHTML = `<div class="loading muted">Lade Grafiken ...</div>`;
    await renderGrafiken(it);
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

  // NT-550 Pass 2 (Lisbeth 15:14 LOW FUNCTIONAL): Export-Button auch ohne
  // aktive Zielsprachen rendern — Items mit nur Master sollen ein Steam-
  // JSON exportieren koennen, ohne erst eine Zielsprache aktivieren zu muessen.
  const exportButton = `<button class="btn small" id="btn-export" title="Steam-Loka-JSON exportieren">📤 Steam-Export</button>`;
  const targetHeader = targetLangs.length === 0
    ? `<span class="muted">Keine Zielsprache aktiv. <button class="link" id="btn-langs-inline">Sprachen aktivieren</button></span>
       ${exportButton}`
    : `<label>Zielsprache: <select id="select-target-lang">${targetOptions}</select></label>
       <button class="btn small" id="btn-translate" title="Auto-uebersetze NUR diese Zielsprache via Claude CLI">⚡ Diese Sprache</button>
       <button class="btn small" id="btn-translate-all" title="Auto-uebersetze ALLE aktiven Zielsprachen sequenziell">⚡ Alle Sprachen</button>
       ${exportButton}`;

  const eaToggle = `
    <label class="checkbox-line">
      <input type="checkbox" id="toggle-ea" ${it.meta.early_access ? "checked" : ""}>
      Early Access — pflege EA-Q&amp;A-Felder
    </label>`;

  const standardFields = state.fields.filter(f => f.block === "standard");
  const eaFields = state.fields.filter(f => f.block === "early_access");

  const renderFieldGroup = (fields, title, headerExtra = "") => {
    if (!fields.length) return "";
    const rows = fields.map(f => renderFieldRow(it, f)).join("");
    return `<section class="card">
      <h2>${escapeHtml(title)}</h2>
      ${headerExtra}
      <div class="fields">${rows}</div>
    </section>`;
  };

  // EA-Block-Header: Hinweis + Export-Buttons (NT-551)
  const targetLangCode = state.currentTargetLang;
  const targetLangDisp = targetLangCode ? (state.langByCode[targetLangCode]?.display || targetLangCode) : "";
  const eaHeader = `
    <div class="ea-warning">
      ⚠ Diese Felder gehoeren ins Steam-Backend unter "Early Access" — sie werden NICHT
      ueber das Lokalisierungs-JSON hochgeladen. Sid liefert sie als Plaintext zum Copy-Paste.
    </div>
    <div class="ea-actions">
      ${targetLangCode ? `<a class="btn small" href="/api/items/${it.meta.platform}/${it.meta.item_id}/ea-export/${targetLangCode}.txt" download>📄 ${escapeHtml(targetLangDisp)} als Text</a>` : ""}
      <a class="btn small" href="/api/items/${it.meta.platform}/${it.meta.item_id}/ea-export/${it.meta.master_lang}.txt" download>📄 ${escapeHtml(masterDisplay)} (Master) als Text</a>
      <a class="btn small primary" href="/api/items/${it.meta.platform}/${it.meta.item_id}/ea-export.zip" download>📦 Alle Sprachen als ZIP</a>
    </div>`;

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
    ${it.meta.early_access ? renderFieldGroup(eaFields, "Early Access (Q&A)", eaHeader) : ""}
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
    let data = null;
    try {
      const resp = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/early-access`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!resp.ok) {
        alert(`Fehler beim EA-Toggle (HTTP ${resp.status})`);
        return;
      }
      data = await resp.json();
    } catch (err) {
      alert(`Fehler beim EA-Toggle: ${err.message || err}`);
      return;
    }
    if (!data || !data.ok) { alert("Fehler beim EA-Toggle"); return; }
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

  const exBtn = document.getElementById("btn-export");
  if (exBtn) exBtn.addEventListener("click", () => openExportModal(it));

  const trBtn = document.getElementById("btn-translate");
  if (trBtn) trBtn.addEventListener("click", async () => {
    if (!state.currentTargetLang) return;
    const orig = trBtn.innerHTML;
    trBtn.disabled = true;
    trBtn.innerHTML = '<span class="spinner"></span> uebersetze ...';
    try {
      const r = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/translate/${state.currentTargetLang}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        const detail = (await r.json()).detail || `HTTP ${r.status}`;
        showToast(`Uebersetzen fehlgeschlagen: ${detail}`, "error");
        return;
      }
      const data = await r.json();
      const n = data.fields_translated.length;
      const skipped = data.fields_skipped.length;
      showToast(`${n} Felder uebersetzt (${data.engine}, ${data.duration_seconds}s)${skipped ? `, ${skipped} manuell editiert geschuetzt` : ""}`, "ok");
      delete state.itemCache[state.currentItemKey];
      await renderContent();
    } catch (err) {
      showToast(`Netzwerk-Fehler: ${err}`, "error");
    } finally {
      trBtn.disabled = false;
      trBtn.innerHTML = orig;
    }
  });

  const trAllBtn = document.getElementById("btn-translate-all");
  if (trAllBtn) trAllBtn.addEventListener("click", () => openTranslateAllModal(it));

  // Master-Editor: on-blur PUT
  for (const ed of document.querySelectorAll(".editor.master")) {
    ed.addEventListener("blur", async e => {
      const field = e.target.dataset.field;
      const value = e.target.value;
      const oldValue = it.master.fields[field] ?? "";
      if (value === oldValue) return;
      try {
        const resp = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/master/${field}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        });
        if (!resp.ok) {
          // Server hat 4xx/5xx geliefert — Body koennte JSON oder HTML sein.
          let detail = `${resp.status} ${resp.statusText}`;
          try { const body = await resp.json(); if (body?.detail) detail = body.detail; } catch (_) {}
          throw new Error(detail);
        }
        const r = await resp.json();
        if (!r.ok) throw new Error("save failed");
        // Cache invalidieren und Content neu rendern, damit Stale-Counts und
        // Badges in den Translation-Summary-Karten aktuell sind. Ohne das
        // bleiben alte Stale-Zahlen sichtbar bis zur naechsten Tab-Navigation.
        it.master.fields[field] = value;
        delete state.itemCache[state.currentItemKey];
        flashOk(e.target);
        await renderContent();
      } catch (err) {
        showToast(`Master speichern fehlgeschlagen: ${err}`, "error");
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
  const expectedItemKey = `${it.meta.platform}:${it.meta.item_id}`;
  // Race-Schutz Pass 6 (Lisbeth NT-548): zwei mehrfach-laufende Anfragen
  // fuer DIESELBE Item/Sprach-Kombi koennten beide die State-Checks unten
  // bestehen. Mit einem aufsteigenden Token pro Kombi rendert nur die
  // letzte Anfrage. Eine langsamere Pre-Save-Antwort, die nach dem Save
  // eintrifft, wird damit verworfen statt frische Werte zu ueberschreiben.
  const tokenKey = `${expectedItemKey}|${lang}`;
  const myToken = (state.langRenderTokens[tokenKey] || 0) + 1;
  state.langRenderTokens[tokenKey] = myToken;
  // Lisbeth NT-550 16:05 Pass 4 (MEDIUM FUNCTIONAL): bei kaputter
  // translations/<lang>.json kann der Endpoint 422/500 + JSON-Body mit
  // "detail" liefern. Vorher wurde blind .fields dereferenziert -> Editor
  // crasht. Jetzt: response.ok pruefen, im Fehlerfall Felder leeren statt
  // crashen. 404 ist weiterhin still (Translation-Datei existiert nicht
  // als legitimer Fall).
  const resp = await fetch(`/api/items/${it.meta.platform}/${it.meta.item_id}/translation/${lang}`);
  if (resp.status === 404) return;
  let t;
  try {
    t = await resp.json();
  } catch (_) {
    t = null;
  }
  if (!resp.ok || !t || !t.fields) {
    // Editor in Fehler-Zustand: leere target-Editoren, kein Crash, klare
    // Warnung im UI. Race-Schutz unten greift weiterhin.
    if (state.currentTargetLang !== lang) return;
    if (state.currentItemKey !== expectedItemKey) return;
    if (state.langRenderTokens[tokenKey] !== myToken) return;
    for (const row of document.querySelectorAll(".field-row")) {
      const targetEditor = row.querySelector(".editor.target");
      const flags = row.querySelector(".field-flags");
      if (targetEditor) { targetEditor.value = ""; targetEditor.dataset.lastValue = ""; }
      if (flags) flags.innerHTML = `<span class="flag stale">unlesbar</span>`;
    }
    return;
  }
  // Race-Schutz: state.currentTargetLang ODER state.currentItemKey kann sich
  // waehrend des fetch geaendert haben (User wechselt Sprache oder Item).
  // Wenn die Antwort nicht mehr zur aktuellen Auswahl passt, abbrechen — sonst
  // zeigt der Editor Inhalte einer anderen Sprache/eines anderen Items mit
  // passenden stale/manual Badges (Lisbeth NT-548 13:02, MEDIUM FUNCTIONAL).
  if (state.currentTargetLang !== lang) return;
  if (state.currentItemKey !== expectedItemKey) return;
  // Innerhalb derselben Kombi: nur das neueste Fetch rendern.
  if (state.langRenderTokens[tokenKey] !== myToken) return;
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

// --- Grafiken-Tab (NT-564) ---------------------------------------------------

async function ensureAssetsCatalog() {
  if (!state.assetsCatalog) {
    state.assetsCatalog = (await fetch("/api/assets/catalog").then(r => r.json())).slots;
  }
  return state.assetsCatalog;
}

async function renderGrafiken(it) {
  const main = document.getElementById("content");
  const platform = it.meta.platform;
  const itemId = it.meta.item_id;
  const itemKey = `${platform}:${itemId}`;
  let catalog, status;
  try {
    [catalog, status] = await Promise.all([
      ensureAssetsCatalog(),
      fetch(`/api/items/${platform}/${itemId}/assets`).then(r => r.json()),
    ]);
  } catch (err) {
    main.innerHTML = `<div class="card error">Grafiken konnten nicht geladen werden: ${escapeHtml(String(err))}</div>`;
    return;
  }
  // Race-Schutz: waehrend des fetch koennte der User Item/Tab gewechselt haben.
  if (state.currentItemKey !== itemKey || state.currentSubTab !== "grafiken") return;

  const statusByKey = Object.fromEntries((status.slots || []).map(s => [s.key, s]));
  const cv = Date.now();  // cache-buster fuer Bild-Previews nach Upload
  const fileInput = (attrs) => `<input type="file" accept="image/*" hidden ${attrs}>`;

  const slotCard = (slot) => {
    const st = statusByKey[slot.key] || {};
    const base = `/api/items/${platform}/${itemId}/assets/${slot.key}`;
    const preview = st.has_default
      ? `<img class="asset-preview" src="${base}/file?lang=default&v=${cv}" alt="">`
      : `<div class="asset-empty">kein Bild</div>`;
    const warn = (st.has_default && st.default_size_ok === false)
      ? `<span class="flag stale" title="Bildmaß weicht vom Soll ab">Maß ≠ Soll</span>` : "";
    let langRow = "";
    if (slot.localizable && st.per_lang) {
      const chips = Object.entries(st.per_lang).map(([lang, mode]) => {
        const disp = state.langByCode[lang]?.iso || lang;
        const clear = mode === "override"
          ? `<button class="chip-clear" data-clear-slot="${slot.key}" data-clear-lang="${lang}" title="Override entfernen">×</button>` : "";
        return `<span class="lang-chip ${mode}">
          <label class="chip-up" title="Override fuer ${escapeHtml(disp)} hochladen">${escapeHtml(disp)}
            ${fileInput(`data-slot="${slot.key}" data-lang="${lang}"`)}
          </label>${clear}</span>`;
      }).join("");
      langRow = `<div class="asset-langs"><span class="muted">Per-Sprache:</span> ${chips}</div>`;
    }
    const missingCls = (slot.required && !st.has_default) ? " missing" : "";
    return `<div class="asset-card${missingCls}">
      <div class="asset-head">
        <strong>${escapeHtml(slot.label)}</strong>
        <span class="muted">${slot.width}×${slot.height} · ${(slot.formats || []).join("/")}</span>
        ${slot.required ? `<span class="flag req">Pflicht</span>` : ""}${warn}
      </div>
      ${preview}
      <div class="asset-actions">
        <label class="btn small">⬆ Standard${fileInput(`data-slot="${slot.key}" data-default="1"`)}</label>
        ${st.has_default ? `<button class="btn small del" data-del-slot="${slot.key}">🗑</button>` : ""}
      </div>
      ${langRow}
    </div>`;
  };

  const categories = [
    { key: "store",   label: "Store-Seite" },
    { key: "library", label: "Bibliothek" },
    { key: "icon",    label: "Icons" },
  ];
  const catSections = categories.map(c => {
    const slots = catalog.filter(s => s.category === c.key);
    if (!slots.length) return "";
    return `<section class="card">
      <h2>${escapeHtml(c.label)}</h2>
      <div class="asset-grid">${slots.map(slotCard).join("")}</div>
    </section>`;
  }).join("");

  const shots = (status.screenshots && status.screenshots.items) || [];
  const targetLangs = it.meta.active_languages.filter(l => l !== it.meta.master_lang);
  const shotCards = shots.map((s, i) => {
    const base = `/api/items/${platform}/${itemId}/assets/screenshots/${s.id}`;
    const warn = s.size_ok === false ? `<span class="flag stale">Maß ≠ 1920×1080</span>` : "";
    const thumb = s.has_default
      ? `<img class="shot-thumb" src="${base}/file?lang=default&v=${cv}" alt="">`
      : `<div class="asset-empty">—</div>`;
    const captionCount = s.n_captions || 0;
    const overrideCount = s.n_overrides || 0;
    const langBadges = `<span class="muted">${overrideCount} Bild-Override${overrideCount === 1 ? "" : "s"} · ${captionCount}/${targetLangs.length} Caption${captionCount === 1 ? "" : "s"}</span>`;
    return `<div class="shot-card" data-shot="${s.id}">
      <div class="shot-num">#${i + 1}${warn}</div>
      ${thumb}
      <input type="text" class="shot-caption" data-shot="${s.id}" value="${escapeHtml(s.master_caption || "")}" placeholder="Caption (DE)">
      <div class="shot-langinfo">${langBadges}</div>
      <div class="shot-actions">
        <button class="btn small" data-shot-up="${s.id}" ${i === 0 ? "disabled" : ""} title="hoch">↑</button>
        <button class="btn small" data-shot-down="${s.id}" ${i === shots.length - 1 ? "disabled" : ""} title="runter">↓</button>
        <button class="btn small" data-shot-langs="${s.id}" title="Per-Sprache: Bild-Overrides + Captions">🌐 Sprachen</button>
        <button class="btn small" data-shot-translate="${s.id}" title="Master-Caption in alle Zielsprachen uebersetzen" ${(!s.master_caption || !targetLangs.length) ? "disabled" : ""}>✎ Übersetzen</button>
        <button class="btn small del" data-shot-del="${s.id}" title="Screenshot loeschen">🗑</button>
      </div>
    </div>`;
  }).join("");

  const exportOpts = it.meta.active_languages
    .map(l => `<option value="${l}">${escapeHtml(state.langByCode[l]?.display || l)}</option>`).join("");

  main.innerHTML = `
    <section class="card item-header">
      <h2>${escapeHtml(it.meta.name)} — Grafiken <span class="muted">#${it.meta.item_id} · ${escapeHtml(it.meta.platform)}</span></h2>
      <div class="header-controls">
        <span class="muted">Standard-Bild gilt fuer alle Sprachen — Overrides nur dort, wo Text im Bild steckt.</span>
        <span class="sep">·</span>
        <label>Export-Sprache: <select id="asset-export-lang">${exportOpts}</select></label>
        <button class="btn small" id="btn-asset-export" title="Alle fuer die Sprache gueltigen Assets als ZIP">📦 Assets als ZIP</button>
      </div>
    </section>
    ${catSections}
    <section class="card">
      <h2>Screenshots <span class="muted">${shots.length} · mind. 5 empfohlen · 1920×1080</span></h2>
      <div class="shot-grid">${shotCards || '<p class="muted">Noch keine Screenshots.</p>'}</div>
      <label class="btn small">➕ Screenshot hinzufuegen${fileInput('id="shot-add"')}</label>
    </section>`;

  bindGrafikenHandlers(it);
}

function bindGrafikenHandlers(it) {
  const platform = it.meta.platform;
  const itemId = it.meta.item_id;
  const reload = () => renderGrafiken(it);

  const uploadTo = async (url, file, form = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(form)) fd.append(k, v);
    try {
      const r = await fetch(url, { method: "POST", body: fd });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b = await r.json(); if (b && b.detail) detail = b.detail; } catch (_) {}
        showToast("Upload fehlgeschlagen: " + detail, "error");
        return false;
      }
      return true;
    } catch (err) {
      showToast("Upload fehlgeschlagen: " + (err.message || err), "error");
      return false;
    }
  };

  // NT-564 Pass 2 (Lisbeth 17:17 LOW FUNCTIONAL): zentrale Wrapper-Funktion
  // fuer alle Mutation-Calls — vorher haben delete/caption/reorder ihre
  // r.ok nicht geprueft und haben bei Server-Errors trotzdem reload/flashOk
  // ausgeloest. Jetzt: Fehler -> showToast, kein Reload, keine grueneren
  // Saved-Animation.
  const mutate = async (url, opts = {}, { errorLabel = "Aktion fehlgeschlagen" } = {}) => {
    try {
      const r = await fetch(url, opts);
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b = await r.json(); if (b && b.detail) detail = b.detail; } catch (_) {}
        showToast(`${errorLabel}: ${detail}`, "error");
        return null;
      }
      try { return await r.json(); } catch (_) { return {}; }
    } catch (err) {
      showToast(`${errorLabel}: ${err.message || err}`, "error");
      return null;
    }
  };

  // Standard- und Per-Sprache-Uploads (alle file-inputs mit data-slot)
  for (const inp of document.querySelectorAll('input[type="file"][data-slot]')) {
    inp.addEventListener("change", async e => {
      const file = e.target.files[0];
      if (!file) return;
      const slot = e.target.dataset.slot;
      const isDefault = e.target.dataset.default === "1";
      const form = isDefault ? {} : { lang: e.target.dataset.lang };
      if (await uploadTo(`/api/items/${platform}/${itemId}/assets/${slot}`, file, form)) {
        showToast("Bild hochgeladen", "ok");
        await reload();
      }
    });
  }
  // Standard-Bild loeschen
  for (const b of document.querySelectorAll("[data-del-slot]")) {
    b.addEventListener("click", async () => {
      if (!confirm("Standard-Bild dieses Slots loeschen?")) return;
      const res = await mutate(
        `/api/items/${platform}/${itemId}/assets/${b.dataset.delSlot}`,
        { method: "DELETE" },
        { errorLabel: "Bild loeschen fehlgeschlagen" },
      );
      if (res !== null) await reload();
    });
  }
  // Override entfernen
  for (const b of document.querySelectorAll(".chip-clear")) {
    b.addEventListener("click", async () => {
      const slot = b.dataset.clearSlot;
      const lang = b.dataset.clearLang;
      const res = await mutate(
        `/api/items/${platform}/${itemId}/assets/${slot}?lang=${encodeURIComponent(lang)}`,
        { method: "DELETE" },
        { errorLabel: "Override entfernen fehlgeschlagen" },
      );
      if (res !== null) await reload();
    });
  }
  // Screenshot hinzufuegen
  const addShot = document.getElementById("shot-add");
  if (addShot) addShot.addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    if (await uploadTo(`/api/items/${platform}/${itemId}/assets/screenshots`, file)) {
      showToast("Screenshot hinzugefuegt", "ok");
      await reload();
    }
  });
  // Screenshot-Caption (on-blur) — master_caption
  for (const inp of document.querySelectorAll(".shot-caption")) {
    inp.addEventListener("blur", async e => {
      const res = await mutate(
        `/api/items/${platform}/${itemId}/assets/screenshots/${e.target.dataset.shot}/caption`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: e.target.value }),
        },
        { errorLabel: "Caption speichern fehlgeschlagen" },
      );
      if (res !== null) flashOk(e.target);
    });
  }
  // Screenshot loeschen
  for (const b of document.querySelectorAll("[data-shot-del]")) {
    b.addEventListener("click", async () => {
      if (!confirm("Screenshot loeschen?")) return;
      const res = await mutate(
        `/api/items/${platform}/${itemId}/assets/screenshots/${b.dataset.shotDel}`,
        { method: "DELETE" },
        { errorLabel: "Screenshot loeschen fehlgeschlagen" },
      );
      if (res !== null) await reload();
    });
  }
  // Reihenfolge (hoch/runter)
  const moveShot = async (id, dir) => {
    const ids = [...document.querySelectorAll(".shot-card")].map(c => parseInt(c.dataset.shot, 10));
    const i = ids.indexOf(parseInt(id, 10));
    const j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    const res = await mutate(
      `/api/items/${platform}/${itemId}/assets/screenshots/reorder`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ordered_ids: ids }),
      },
      { errorLabel: "Reihenfolge speichern fehlgeschlagen" },
    );
    if (res !== null) await reload();
  };
  for (const b of document.querySelectorAll("[data-shot-up]")) b.addEventListener("click", () => moveShot(b.dataset.shotUp, -1));
  for (const b of document.querySelectorAll("[data-shot-down]")) b.addEventListener("click", () => moveShot(b.dataset.shotDown, 1));
  // Master-Caption uebersetzen -> alle Zielsprachen
  for (const b of document.querySelectorAll("[data-shot-translate]")) {
    b.addEventListener("click", async () => {
      const shotId = b.dataset.shotTranslate;
      const overwrite = confirm("Nur leere Captions fuellen?\n\nOK = nur leere\nAbbrechen = alle ueberschreiben");
      b.disabled = true;
      const res = await mutate(
        `/api/items/${platform}/${itemId}/assets/screenshots/${shotId}/translate-captions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ overwrite_existing: !overwrite }),
        },
        { errorLabel: "Caption-Uebersetzung fehlgeschlagen" },
      );
      b.disabled = false;
      if (res !== null) {
        const n = Object.keys(res.written || {}).length;
        showToast(n ? `${n} Caption(s) uebersetzt (${res.engine})` : "Keine Sprache zu uebersetzen", n ? "ok" : "error");
        await reload();
      }
    });
  }
  // Per-Sprache Captions + Bild-Overrides Modal
  for (const b of document.querySelectorAll("[data-shot-langs]")) {
    b.addEventListener("click", () => openScreenshotLangsModal(it, parseInt(b.dataset.shotLangs, 10)));
  }
  // Export ZIP
  const exBtn = document.getElementById("btn-asset-export");
  if (exBtn) exBtn.addEventListener("click", () => {
    const lang = document.getElementById("asset-export-lang").value;
    const a = document.createElement("a");
    a.href = `/api/items/${platform}/${itemId}/assets/export.zip?lang=${encodeURIComponent(lang)}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
}

async function openScreenshotLangsModal(it, shotId) {
  const platform = it.meta.platform;
  const itemId = it.meta.item_id;
  const masterLang = it.meta.master_lang;
  const targetLangs = it.meta.active_languages.filter(l => l !== masterLang);

  // Aktuellen Stand des Manifests holen (captions + localized)
  let manifest;
  try {
    const r = await fetch(`/api/items/${platform}/${itemId}/assets`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    manifest = await r.json();
  } catch (err) {
    showToast("Konnte Sprachstatus nicht laden: " + (err.message || err), "error");
    return;
  }
  // status() liefert keine voll-detaillierten Captions; fuer den Editor brauchen
  // wir den raw screenshot-Eintrag. Wir holen das ueber den /captions-PUT-
  // Round-Trip ab — bei der ersten Anzeige sind die Werte aus master_caption
  // + n_captions ableitbar; den Pro-Sprache-Text holen wir on-demand pro Feld
  // ueber das /caption-API.
  // Pragma: zur initialen Befuellung des Modals nutzen wir einen Read-Endpoint —
  // /assets liefert nur Aggregate. Workaround: per Sprache GET ueber das
  // file-Endpoint (HEAD) + caption-Status koennen wir aus dem `status`-Aggregate
  // nicht ablesen. Wir holen die Details ueber einen neuen, leichten Read:
  let captions = {};
  let hasOverride = {};
  try {
    const r = await fetch(`/api/items/${platform}/${itemId}/assets/screenshots/${shotId}/details`);
    if (r.ok) {
      const j = await r.json();
      captions = j.captions || {};
      hasOverride = j.has_override || {};
    }
  } catch (_) { /* details-Endpoint kann fehlen -> Modal funktioniert trotzdem */ }

  const rows = targetLangs.map(l => {
    const lab = state.langByCode[l]?.display || l;
    const cap = captions[l] || "";
    const ov = hasOverride[l] ? `<span class="muted">Override</span>` : `<span class="muted">faellt auf Standard zurueck</span>`;
    return `<tr data-lang="${l}">
      <td><span class="lang-tag">${escapeHtml(l)}</span> ${escapeHtml(lab)}</td>
      <td><input type="text" class="lang-cap" data-lang="${l}" value="${escapeHtml(cap)}" placeholder="(leer)"></td>
      <td>
        <label class="btn small">⬆ Bild<input type="file" class="lang-override-file" data-lang="${l}" hidden accept="image/png,image/jpeg"></label>
        ${hasOverride[l] ? `<button class="btn small del" data-lang-clear="${l}" title="Override entfernen">🗑</button>` : ""}
        <div class="lang-override-state">${ov}</div>
      </td>
    </tr>`;
  }).join("");

  showModal(`
    <h2>Screenshot #${shotId} — Per-Sprache</h2>
    <p class="muted">Captions werden beim ZIP-Export pro Sprache verwendet. Bild-Overrides werden nur fuer die jeweilige Sprache exportiert, sonst gilt das Standard-Bild.</p>
    <table class="lang-edit-table"><thead><tr><th>Sprache</th><th>Caption</th><th>Bild-Override</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="muted">Keine Zielsprachen aktiv.</td></tr>'}</tbody></table>
    <div class="modal-actions">
      <button class="btn" id="btn-shot-langs-close">Schliessen</button>
    </div>
  `);

  document.getElementById("btn-shot-langs-close").addEventListener("click", async () => {
    closeModal();
    // Status neu laden (Counter koennen sich geaendert haben)
    delete state.itemCache[state.currentItemKey];
    await renderGrafiken(it);
  });

  const saveCaption = async (lang, text) => {
    const r = await fetch(`/api/items/${platform}/${itemId}/assets/screenshots/${shotId}/caption`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, lang }),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const b = await r.json(); if (b && b.detail) detail = b.detail; } catch (_) {}
      showToast("Caption speichern fehlgeschlagen: " + detail, "error");
      return false;
    }
    return true;
  };

  for (const inp of document.querySelectorAll(".lang-cap")) {
    inp.addEventListener("blur", async e => {
      if (await saveCaption(e.target.dataset.lang, e.target.value)) flashOk(e.target);
    });
  }
  for (const fi of document.querySelectorAll(".lang-override-file")) {
    fi.addEventListener("change", async e => {
      const file = e.target.files[0];
      if (!file) return;
      const lang = e.target.dataset.lang;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("lang", lang);
      const r = await fetch(`/api/items/${platform}/${itemId}/assets/screenshots/${shotId}/override`, {
        method: "POST", body: fd,
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b = await r.json(); if (b && b.detail) detail = b.detail; } catch (_) {}
        showToast("Override-Upload fehlgeschlagen: " + detail, "error");
        return;
      }
      showToast(`Override fuer ${lang} gespeichert`, "ok");
      closeModal();
      await openScreenshotLangsModal(it, shotId);
    });
  }
  for (const b of document.querySelectorAll("[data-lang-clear]")) {
    b.addEventListener("click", async () => {
      const lang = b.dataset.langClear;
      if (!confirm(`Override fuer ${lang} loeschen?`)) return;
      // Override-Loeschen-Endpoint existiert nicht direkt; fallback: DELETE Screenshot ist zu hart.
      // Wir loeschen die Datei via screenshot override mit lang-Parameter? Es gibt
      // keinen dedizierten Endpoint dafuer; Workaround: caption setzen auf "" wuerde
      // nicht greifen. Wir nutzen das gleiche Caption-Endpoint nicht; stattdessen
      // schicken wir eine spezielle DELETE-Variante.
      const r = await fetch(`/api/items/${platform}/${itemId}/assets/screenshots/${shotId}/override?lang=${encodeURIComponent(lang)}`, { method: "DELETE" });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b2 = await r.json(); if (b2 && b2.detail) detail = b2.detail; } catch (_) {}
        showToast("Override loeschen fehlgeschlagen: " + detail, "error");
        return;
      }
      showToast("Override entfernt", "ok");
      closeModal();
      await openScreenshotLangsModal(it, shotId);
    });
  }
}

// --- Modal: Sprachen ---------------------------------------------------------

async function openLanguagesModal() {
  if (!state.currentItemKey) { alert("Erst ein Item waehlen oder anlegen."); return; }
  const [platform, itemId] = state.currentItemKey.split(":");
  // Lisbeth NT-549 15:54 Pass 6 (LOW FUNCTIONAL): Mit der neuen 422/404-Logik
  // bei kaputter meta.json kommt eine Fehler-Payload {detail: "..."} statt
  // {meta: {...}} zurueck. Vorher hat openLanguagesModal blind it.meta
  // dereferenziert und ist gecrasht. Jetzt: Status pruefen und controlled
  // Error-Modal rendern statt zu crashen.
  let it = state.itemCache[state.currentItemKey];
  if (!it || !it.meta) {
    const resp = await fetch(`/api/items/${platform}/${itemId}`);
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { const body = await resp.json(); if (body && body.detail) detail = body.detail; } catch (_) {}
      showModal(`
        <h2>Sprachen-Modal nicht verfuegbar</h2>
        <p>Das Item kann nicht geladen werden:</p>
        <pre class="error-detail">${escapeHtml(String(detail))}</pre>
        <div class="modal-actions">
          <button class="btn" id="btn-cancel-langs-err">Schliessen</button>
        </div>
      `);
      document.getElementById("btn-cancel-langs-err").addEventListener("click", closeModal);
      return;
    }
    it = await resp.json();
  }
  if (!it || !it.meta) {
    showModal(`
      <h2>Sprachen-Modal nicht verfuegbar</h2>
      <p>Das Item liefert keine Metadaten zurueck.</p>
      <div class="modal-actions">
        <button class="btn" id="btn-cancel-langs-err">Schliessen</button>
      </div>
    `);
    document.getElementById("btn-cancel-langs-err").addEventListener("click", closeModal);
    return;
  }
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
    let r = null;
    try {
      const resp = await fetch(`/api/items/${platform}/${itemId}/active-languages`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ languages: langs }),
      });
      if (!resp.ok) {
        alert(`Speichern fehlgeschlagen (HTTP ${resp.status})`);
        return;
      }
      r = await resp.json();
    } catch (err) {
      alert(`Speichern fehlgeschlagen: ${err.message || err}`);
      return;
    }
    if (!r || !r.ok) { alert("Speichern fehlgeschlagen"); return; }
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

// --- Modal: Bulk-Translate (alle Sprachen) -----------------------------------

async function openTranslateAllModal(it) {
  const platform = it.meta.platform;
  const itemId = it.meta.item_id;
  const targetLangs = it.meta.active_languages.filter(l => l !== it.meta.master_lang);

  const langRows = targetLangs.map(code => {
    const l = state.langByCode[code];
    return `<tr data-lang="${code}">
      <td>${escapeHtml(l?.display || code)} <span class="muted">${escapeHtml(l?.iso || code)}</span></td>
      <td class="status muted">wartet ...</td>
    </tr>`;
  }).join("");

  showModal(`
    <h2>Alle Sprachen uebersetzen</h2>
    <p class="muted">${targetLangs.length} aktive Zielsprachen werden sequenziell uebersetzt.
    Manuell editierte Felder bleiben unangetastet. Pro Sprache ein Claude-CLI-Aufruf
    (~30s pro Sprache) — das kann ein paar Minuten dauern.</p>
    <table class="bulk-translate-table"><tbody>${langRows}</tbody></table>
    <div class="modal-actions">
      <button class="btn" id="btn-bt-cancel">Schliessen</button>
      <button class="btn primary" id="btn-bt-start">Start</button>
    </div>
    <div id="bt-summary" class="muted"></div>
  `);

  // NT-550/551 Pass 12 (Lisbeth 09:51 + 09:56 LOW/MEDIUM FUNCTIONAL):
  // Stream-State lebt im outer Scope, damit btn-bt-cancel und die globale
  // Modal-Close-Logik den aktiven EventSource sauber schliessen koennen.
  // Vorher lief der SSE-Stream im Hintergrund weiter, wenn der User das
  // Modal abgebrochen hat — Callbacks feuerten gegen entfernte DOM-Nodes
  // (bt-summary, btn-bt-start) und ein Restart konnte parallel laufen.
  let es = null;
  let streamDone = false;
  // NT-551 Pass 25 (Lisbeth 14:45 MEDIUM FUNCTIONAL): cleanFresh markiert,
  // dass der Stream sauber durch das 'done'-Event beendet wurde -- also
  // alle asyncio.to_thread-Worker fertig geschrieben haben. Nur dann ist
  // ein renderContent() race-frei. Bei cancel/onerror laufen evtl. noch
  // background-threads -> nur Cache leeren, kein render.
  let cleanFresh = false;
  const stopStream = () => {
    if (es && !streamDone) {
      streamDone = true;
      es.close();
      es = null;
    }
  };

  document.getElementById("btn-bt-cancel").addEventListener("click", () => {
    stopStream();
    closeModal();
  });
  // NT-551 Pass 25 (Lisbeth 14:45 MEDIUM FUNCTIONAL): close-Handler darf
  // renderContent() NUR aufrufen wenn der Stream durch ein 'done'-Event
  // beendet wurde (cleanFresh=true). Bei cancel/onerror koennten noch
  // asyncio.to_thread-Worker auf das fs schreiben -- ein sofortiger
  // fetch wuerde stale Daten cachen.
  //
  // Cache wird IMMER invalidiert (kostet nichts), damit der naechste
  // user-getriggerte renderContent (Tab-Wechsel, manueller Reload) frisch
  // lädt -- dann sind die background-writes garantiert durch.
  document.getElementById("modal").addEventListener(
    "close",
    () => {
      stopStream();
      delete state.itemCache[state.currentItemKey];
      if (cleanFresh) {
        renderContent();
      }
    },
    { once: true },
  );
  // NT-550 Pass 11 (Lisbeth 09:36 MEDIUM FUNCTIONAL): Der Start-Handler wird
  // via AbortController registriert. finalize() ruft startCtrl.abort() bevor
  // der Button als "Schliessen" weitergenutzt wird — sonst feuert ein Klick
  // auf "Schliessen" sowohl den close-Handler als auch den alten
  // Translation-Start-Handler und ein zweiter Batch laeuft an.
  const startBtn = document.getElementById("btn-bt-start");
  const startCtrl = new AbortController();
  startBtn.addEventListener("click", () => {
    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="spinner"></span> uebersetze ...';
    // NT-549 Pass 9 (Lisbeth MEDIUM FUNCTIONAL): SSE statt fetch-Chain.
    // Backend sendet `lang_start` bevor Claude-CLI startet — UI zeigt die
    // aktive Reihe als "uebersetzt ..." an, auch wenn ein einzelner Aufruf
    // im Claude-Timeout haengt. lang_done bringt das Resultat.
    let okCount = 0;
    let errCount = 0;
    let activeRow = null;
    let totalLangs = targetLangs.length;
    const finalize = () => {
      // NT-550/551 Pass 12: Wenn der Stream durch externes Modal-Close
      // (stopStream) bereits weg ist, finalize NICHT ausfuehren — der
      // Summary-Node existiert nicht mehr.
      const summaryNode = document.getElementById("bt-summary");
      if (!summaryNode) return;
      if (activeRow) {
        activeRow.innerHTML = `<span class="flag stale">ABGEBROCHEN</span>`;
        activeRow = null;
      }
      summaryNode.innerHTML =
        `<strong>Fertig:</strong> ${okCount} ok, ${errCount} fehlgeschlagen.`;
      startBtn.innerHTML = "Schliessen";
      startBtn.disabled = false;
      // Pass 11: alten Start-Handler abreissen, bevor "Schliessen" verdraht
      // wird. Sonst feuert ein Klick auf "Schliessen" sowohl onclick (close)
      // als auch den alten addEventListener-Handler (start neuer Batch).
      startCtrl.abort();
      startBtn.onclick = closeModal;
      // NT-551 Pass 17: Cache-Cleanup ist jetzt im open-time close-Listener
      // (siehe oben). Der ehemalige finalize-time Listener wurde entfernt,
      // weil er im cancel-Pfad nicht feuert (stopStream schliesst Stream,
      // server-cancelled-Event kommt nie an, finalize laeuft nicht).
    };
    es = new EventSource(`/api/items/${platform}/${itemId}/translate-stream`);
    es.addEventListener("start", (ev) => {
      const data = JSON.parse(ev.data);
      if (typeof data.n_total === "number") totalLangs = data.n_total;
    });
    es.addEventListener("lang_start", (ev) => {
      const data = JSON.parse(ev.data);
      const row = document.querySelector(`tr[data-lang="${data.lang}"] .status`);
      if (row) row.innerHTML = '<span class="spinner"></span> uebersetzt ...';
      activeRow = row;
    });
    es.addEventListener("lang_done", (ev) => {
      const data = JSON.parse(ev.data);
      const row = document.querySelector(`tr[data-lang="${data.lang}"] .status`);
      if (data.ok) {
        const n = data.fields_translated.length;
        const skipped = data.fields_skipped.length;
        if (row) row.innerHTML = `<span class="flag ok">OK</span> ${n} Felder${skipped ? `, ${skipped} geschuetzt` : ""} (${data.duration_seconds}s)`;
        okCount++;
      } else {
        if (row) row.innerHTML = `<span class="flag stale">FAIL</span> ${escapeHtml(data.error || "Fehler")}`;
        errCount++;
      }
      activeRow = null;
    });
    es.addEventListener("done", () => {
      streamDone = true;
      es.close();
      es = null;
      // NT-551 Pass 25: nur bei done-Event sind alle Worker-Threads fertig
      // geschrieben. cleanFresh=true erlaubt dem close-Handler renderContent().
      cleanFresh = true;
      finalize();
    });
    // NT-550 Pass 13: Server emittiert 'cancelled' wenn er nach
    // request.is_disconnected() den Batch abbricht. Im Normalfall ist der
    // Modal dann schon zu (user hat geschlossen), aber falls der Stream
    // aus anderem Grund cancelled wird, sauber close + finalize. finalize
    // ist NoOp wenn bt-summary weg.
    es.addEventListener("cancelled", () => {
      streamDone = true;
      es.close();
      es = null;
      finalize();
    });
    // NT-549 Pass 10 (Lisbeth 09:24 LOW FUNCTIONAL): es.onerror feuert auch
    // wenn der Stream VOR dem ersten lang_start failed oder zwischen Sprachen
    // (nach Reset von activeRow). Frueher haben wir errCount nur erhoeht wenn
    // activeRow gesetzt war — Folge: Summary "0 fehlgeschlagen" obwohl Batch
    // abgebrochen ist. Jetzt: alle noch nicht verarbeiteten Sprachen werden
    // als Fehler markiert.
    // NT-550 Pass 12 (Lisbeth 09:51 LOW FUNCTIONAL): Math.max(0,...) statt
    // Math.max(1,...). Wenn alle lang_done bereits angekommen sind und nur
    // das abschliessende done-Event verloren ging, soll kein kuenstlicher
    // Fehler dazukommen.
    es.onerror = () => {
      if (streamDone) return;
      streamDone = true;
      es.close();
      es = null;
      if (activeRow) {
        activeRow.innerHTML = `<span class="flag stale">NETZWERK</span>`;
        activeRow = null;
      }
      const remaining = Math.max(0, totalLangs - okCount - errCount);
      errCount += remaining;
      finalize();
      // NT-551 Pass 25: onerror laesst Worker-Threads evtl. noch arbeiten.
      // Cache invalidieren ok (kostet nichts), aber KEIN renderContent --
      // das wuerde race mit Worker-Writes. User klickt naechstes Mal
      // auf das Item -> garantiert frische Daten.
      delete state.itemCache[state.currentItemKey];
    };
  }, { signal: startCtrl.signal });
}


// --- Modal: Steam-Export -----------------------------------------------------

async function openExportModal(it) {
  const platform = it.meta.platform;
  const itemId = it.meta.item_id;
  showModal(`
    <h2>Steam-Loka-JSON Export</h2>
    <p class="muted">Format identisch zum Partner-Backend-Download. Hochladen unter
    Lokalisierung &rarr; Lokalisierten Text hochladen.</p>
    <div class="export-warning">
      ⚠ <strong>Early-Access-Felder sind NICHT in dieser JSON enthalten.</strong>
      Steam pflegt die EA-Q&amp;A in einem separaten Backend-Bereich.
      Verwende dafuer den 📦-Button im EA-Block (laedt ein ZIP mit allen Sprachen
      als Plaintext zum Copy/Paste in Steamworks).
    </div>
    <div id="export-stats" class="muted">Lade Vorschau...</div>
    <div class="export-actions">
      <button class="btn primary" id="btn-export-download">⬇ Datei erzeugen + herunterladen</button>
      <button class="btn" id="btn-export-show">JSON anzeigen</button>
      <button class="btn" id="btn-export-close">Schliessen</button>
    </div>
    <div id="export-history" class="muted"></div>
    <pre id="export-preview" class="export-preview" style="display:none"></pre>
  `);

  document.getElementById("btn-export-close").addEventListener("click", closeModal);

  // Preview-Stats laden
  let previewData = null;
  try {
    const r = await fetch(`/api/items/${platform}/${itemId}/export-preview`).then(r => r.json());
    previewData = r.data;
    const s = r.summary;
    document.getElementById("export-stats").innerHTML = `
      <table class="stats-table">
        <tr><td>Item-ID</td><td><code>${escapeHtml(String(s.item_id))}</code></td></tr>
        <tr><td>Sprachen</td><td>${s.n_languages} (${s.n_languages_with_content} mit Inhalt)</td></tr>
        <tr><td>Felder pro Sprache</td><td>${s.n_fields_per_language}</td></tr>
        <tr><td>Gesamt-Zeichen</td><td>${s.total_chars.toLocaleString("de-DE")}</td></tr>
      </table>`;
  } catch (err) {
    document.getElementById("export-stats").innerHTML = `<span class="error">Fehler: ${escapeHtml(String(err))}</span>`;
  }

  // Bisherige Exports
  try {
    const list = await fetch(`/api/items/${platform}/${itemId}/exports`).then(r => r.json());
    const exports = list.exports || [];
    const hist = document.getElementById("export-history");
    if (exports.length === 0) {
      hist.innerHTML = `<p class="muted">Noch keine Exports archiviert.</p>`;
    } else {
      const rows = exports.slice(0, 5).map(e => `
        <tr>
          <td><a href="/api/items/${platform}/${itemId}/exports/${e.filename}" download>${escapeHtml(e.filename)}</a></td>
          <td class="muted">${e.modified_iso}</td>
          <td class="muted">${(e.size_bytes / 1024).toFixed(1)} KB</td>
        </tr>`).join("");
      hist.innerHTML = `
        <h3 class="export-section-title">Bisherige Exports</h3>
        <table class="exports-table"><tbody>${rows}</tbody></table>`;
    }
  } catch (err) {
    document.getElementById("export-history").innerHTML = `<span class="error">Verlauf konnte nicht geladen werden.</span>`;
  }

  document.getElementById("btn-export-show").addEventListener("click", () => {
    const pre = document.getElementById("export-preview");
    if (pre.style.display === "none") {
      pre.textContent = JSON.stringify(previewData, null, 2);
      pre.style.display = "block";
    } else {
      pre.style.display = "none";
    }
  });

  document.getElementById("btn-export-download").addEventListener("click", async () => {
    const btn = document.getElementById("btn-export-download");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> erzeuge ...';
    try {
      const r = await fetch(`/api/items/${platform}/${itemId}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!r.ok) {
        showToast("Export fehlgeschlagen: " + (await r.text()), "error");
        return;
      }
      const data = await r.json();
      // Download triggern via Browser-Link
      const a = document.createElement("a");
      a.href = data.download_url;
      a.download = data.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      showToast(`Export erzeugt: ${data.filename}`, "ok");
      // Liste neu laden
      const list = await fetch(`/api/items/${platform}/${itemId}/exports`).then(r => r.json());
      const exports = list.exports || [];
      const rows = exports.slice(0, 5).map(e => `
        <tr>
          <td><a href="/api/items/${platform}/${itemId}/exports/${e.filename}" download>${escapeHtml(e.filename)}</a></td>
          <td class="muted">${e.modified_iso}</td>
          <td class="muted">${(e.size_bytes / 1024).toFixed(1)} KB</td>
        </tr>`).join("");
      document.getElementById("export-history").innerHTML = `
        <h3 class="export-section-title">Bisherige Exports</h3>
        <table class="exports-table"><tbody>${rows}</tbody></table>`;
    } catch (err) {
      showToast(`Netzwerk-Fehler: ${err}`, "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  });
}

// --- Modal: Glossar ----------------------------------------------------------

async function openGlossaryModal() {
  if (!state.currentItemKey) { alert("Erst ein Item waehlen oder anlegen."); return; }
  const [platform, itemId] = state.currentItemKey.split(":");
  const g = await fetch(`/api/items/${platform}/${itemId}/glossary`).then(r => r.json());
  const entries = g.entries || [];

  const rowFor = e => `
    <tr>
      <td><input type="text" class="g-term" value="${escapeHtml(e.term || "")}" placeholder="Begriff (DE)"></td>
      <td>
        <select class="g-rule">
          <option value="keep" ${(e.rule || "keep") === "keep" ? "selected" : ""}>woertlich (keep)</option>
          <option value="translate" ${e.rule === "translate" ? "selected" : ""}>uebersetzen + Hinweis</option>
        </select>
      </td>
      <td><input type="text" class="g-note" value="${escapeHtml(e.note || "")}" placeholder="Notiz fuer Translator"></td>
      <td><button class="btn small del" title="Eintrag entfernen">×</button></td>
    </tr>`;

  showModal(`
    <h2>Glossar — Plattform & Item-spezifisch</h2>
    <p class="muted">Begriffe die woertlich uebernommen werden muessen (Spieltitel, Modus-Namen,
    Markennamen) oder besondere Hinweise brauchen. Wird beim Auto-uebersetzen ans LLM angehaengt.</p>
    <table class="glossary-table">
      <thead><tr><th>Begriff</th><th>Regel</th><th>Notiz</th><th></th></tr></thead>
      <tbody id="glossary-body">${entries.map(rowFor).join("")}</tbody>
    </table>
    <button class="btn" id="btn-add-glossary">+ Eintrag hinzufuegen</button>
    <div class="modal-actions">
      <button class="btn" id="btn-cancel-glossary">Abbrechen</button>
      <button class="btn primary" id="btn-save-glossary">Speichern</button>
    </div>
  `);

  function bindRow(row) {
    row.querySelector(".del").addEventListener("click", () => row.remove());
  }
  for (const row of document.querySelectorAll("#glossary-body tr")) bindRow(row);
  document.getElementById("btn-add-glossary").addEventListener("click", () => {
    const tbody = document.getElementById("glossary-body");
    const wrap = document.createElement("tbody");
    wrap.innerHTML = rowFor({});
    const tr = wrap.firstElementChild;
    tbody.appendChild(tr);
    bindRow(tr);
    tr.querySelector(".g-term").focus();
  });
  document.getElementById("btn-cancel-glossary").addEventListener("click", closeModal);
  document.getElementById("btn-save-glossary").addEventListener("click", async () => {
    const newEntries = [];
    for (const row of document.querySelectorAll("#glossary-body tr")) {
      const term = row.querySelector(".g-term").value.trim();
      if (!term) continue;
      newEntries.push({
        term,
        rule: row.querySelector(".g-rule").value,
        note: row.querySelector(".g-note").value.trim(),
      });
    }
    const r = await fetch(`/api/items/${platform}/${itemId}/glossary`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: newEntries }),
    });
    if (!r.ok) { showToast("Glossar speichern fehlgeschlagen: " + (await r.text()), "error"); return; }
    closeModal();
    showToast(`Glossar gespeichert (${newEntries.length} Eintraege)`, "ok");
  });
}

// --- Toast-Helper ------------------------------------------------------------

function showToast(msg, kind = "ok") {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    document.body.appendChild(toast);
  }
  toast.className = `toast ${kind} visible`;
  toast.textContent = msg;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toast.classList.remove("visible"), kind === "error" ? 8000 : 4000);
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
