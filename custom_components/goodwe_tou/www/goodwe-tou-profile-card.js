/*
 * goodwe-tou-profile-card
 *
 * Shows the measured quarter-hour consumption profile the planner uses as the
 * house load: the average power in each of the 96 quarter hours of the day,
 * split into the house load, the grid import and the grid export. That is the
 * real daily rhythm — the "duck curve" of a home with solar — instead of the
 * single flat standby number the planner used to assume.
 *
 * The profile is built by the integration from the recorder's 5-minute
 * statistics (see profile.py and the coordinator), averaged per quarter of the
 * local day over the last days. It is fetched through the
 * `goodwe_tou.get_profile` service, so the card shows all 96 × 3 values even
 * though they would not fit in a state attribute. It refreshes whenever the
 * profile sensor changes. Hovering the graph reads the house load, import and
 * export at the nearest quarter hour.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-profile-card
 *   title: Verbruiksprofiel                     # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                  # optional entity-id prefix
 *   sensor: sensor.goodwe_time_of_use_verbruiksprofiel   # optional, refresh trigger
 *   language: en                                # optional, overrides the language
 */

const CARD_VERSION = "1.9.0";
const PREFIX_DEFAULT = "goodwe_time_of_use";

// Everything the card says, per language. A value may be a function when it
// takes an argument (a count, a value).
const LANGS = {
  nl: {
    title: "Verbruiksprofiel",
    coverage: (days, window) => `${days} van ${window} dagen gemeten`,
    coverageShort: (days) => `${days} dagen gemeten`,
    loadFailed: "Kon het verbruiksprofiel niet laden",
    haUnavailable: "Home Assistant is niet beschikbaar",
    empty: "Nog geen verbruiksprofiel beschikbaar.",
    disabled: "Het verbruiksprofiel staat uit in de instellingen.",
    note: (window) =>
      `Gemiddelde per kwartier over de laatste ${window} dagen (zoveel als de recorder bewaart). De planning gebruikt de huislast-curve; inkoop en verkoop zijn ter vergelijking.`,
    load: "Huisverbruik",
    import: "Inkoop",
    export: "Verkoop",
    perDay: "Per dag",
    loadKwh: "Huisverbruik",
    importKwh: "Inkoop",
    exportKwh: "Verkoop",
    period: (days) => `Periode (${days} dagen)`,
    peak: "Piek",
    avg: "Gemiddeld",
    updated: "Bijgewerkt",
    noValue: "\u2013",
    hour: "uur",
    kw: "kW",
  },
  en: {
    title: "Consumption profile",
    coverage: (days, window) => `${days} of ${window} days measured`,
    coverageShort: (days) => `${days} days measured`,
    loadFailed: "Could not load the consumption profile",
    haUnavailable: "Home Assistant is not available",
    empty: "No consumption profile available yet.",
    disabled: "The consumption profile is switched off in the settings.",
    note: (window) =>
      `Average per quarter hour over the last ${window} days (as much as the recorder keeps). The planner uses the house-load curve; import and export are shown for comparison.`,
    load: "House load",
    import: "Import",
    export: "Export",
    perDay: "Per day",
    loadKwh: "House load",
    importKwh: "Import",
    exportKwh: "Export",
    period: (days) => `Period (${days} days)`,
    peak: "Peak",
    avg: "Average",
    updated: "Updated",
    noValue: "\u2013",
    hour: "h",
    kw: "kW",
  },
};

// Map any language tag (nl, nl-NL, en-GB, ...) onto a language we ship.
const resolveLangCode = (value) => {
  const code = String(value || "").toLowerCase();
  if (code.startsWith("en")) return "en";
  return "nl";
};

const pad = (value) => String(value).padStart(2, "0");
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const SVG_NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs) => {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") el.setAttribute("class", String(value));
    else el.setAttribute(key, String(value));
  }
  return el;
};

const formatTime = (iso) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso || "");
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (sameDay) return hm;
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${hm}`;
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; gap: 8px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .count { font-size: .78rem; color: var(--secondary-text-color); }

  .panels { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
  .panel { flex: 1 1 150px; border: 1px solid var(--divider-color); border-radius: 10px; padding: 9px 11px; background: var(--card-background-color); }
  .ptitle { font-size: .74rem; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); margin-bottom: 6px; }
  .kv { display: flex; justify-content: space-between; gap: 10px; font-size: .84rem; line-height: 1.7; }
  .kv .k { color: var(--secondary-text-color); }
  .kv .v { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }

  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 2px 0 8px; font-size: .8rem; color: var(--secondary-text-color); }
  .legend .item { display: inline-flex; align-items: center; gap: 6px; }
  .legend .swatch { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }

  .chartwrap { position: relative; border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 6px 2px; }
  svg { width: 100%; height: auto; display: block; cursor: crosshair; }
  .gridline { stroke: var(--divider-color); stroke-width: 1; }
  .axislabel { fill: var(--secondary-text-color); font-size: 10px; }
  .loadfill { fill: rgba(33, 150, 243, .14); }
  .loadline { fill: none; stroke: #2196f3; stroke-width: 2; }
  .importline { fill: none; stroke: #e53935; stroke-width: 2; }
  .exportline { fill: none; stroke: #43a047; stroke-width: 2; }
  .crosshair { stroke: var(--primary-text-color); stroke-width: 1; opacity: .5; }
  .hoverdot { stroke: var(--card-background-color, #fff); stroke-width: 1.5; }
  .tooltip {
    position: absolute;
    pointer-events: none;
    transform: translate(-50%, -115%);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    padding: 3px 7px;
    font-size: .74rem;
    font-variant-numeric: tabular-nums;
    white-space: pre;
    text-align: left;
    box-shadow: 0 1px 4px rgba(0, 0, 0, .3);
    opacity: 0;
    transition: opacity .08s linear;
    z-index: 2;
  }
  .tooltip.show { opacity: 1; }
  .tooltip.below { transform: translate(-50%, 10px); }

  .empty { padding: 14px 4px; color: var(--secondary-text-color); font-size: .86rem; }
  .note { margin-top: 8px; font-size: .74rem; color: var(--secondary-text-color); }
`;

class GoodweTouProfileCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._profile = null;
    this._signal = undefined;
    this._error = "";
    this._lang = "nl";
    this._langOverride = null;
    this._hoverPos = null;
    this._geom = null;
    this._hoverSvg = null;
    this._hoverGroup = null;
    this._tipEl = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._langOverride = this._config.language || null;
    this._title = this._config.title || null;
    this._sensor =
      this._config.sensor || `sensor.${this._prefix}_verbruiksprofiel`;
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    const lang = this._resolveLang(hass);
    if (lang !== this._lang) {
      this._lang = lang;
      this._built = false;
    }
    if (!this._built) this._build();
    if (!this._built) return;
    this._refreshIfChanged();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 8;
  }

  // -- language ----------------------------------------------------------

  // Card `language:` override, else the language the integration speaks (on the
  // profile sensor), else Home Assistant's own language, else Dutch.
  _resolveLang(hass) {
    if (this._langOverride) return resolveLangCode(this._langOverride);
    const sensor = hass && hass.states ? hass.states[this._sensor] : undefined;
    const attr = sensor && sensor.attributes && sensor.attributes.language;
    if (attr) return resolveLangCode(attr);
    const locale = hass && hass.locale && hass.locale.language;
    if (locale) return resolveLangCode(locale);
    return "nl";
  }

  _t(key, ...args) {
    const table = LANGS[this._lang] || LANGS.nl;
    const value = table[key];
    return typeof value === "function" ? value(...args) : value;
  }

  // -- formatting --------------------------------------------------------

  _num(value, digits = 2) {
    const n = Number(value);
    if (!Number.isFinite(n)) return this._t("noValue");
    return n.toFixed(digits);
  }

  // -- construction ------------------------------------------------------

  _build() {
    if (!this._hass || !this.shadowRoot) return;
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="head">
          <div class="title"></div>
          <div class="count" id="count"></div>
        </div>
        <div class="panels" id="panels"></div>
        <div class="legend">
          <span class="item"><span class="swatch" style="background:#2196f3"></span>${this._t("load")}</span>
          <span class="item"><span class="swatch" style="background:#e53935"></span>${this._t("import")}</span>
          <span class="item"><span class="swatch" style="background:#43a047"></span>${this._t("export")}</span>
        </div>
        <div class="chartwrap" id="chartwrap"></div>
        <div class="note" id="note"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${
      this._title || this._t("title")
    } (Version ${CARD_VERSION})`;
    this._countEl = root.querySelector("#count");
    this._panelsEl = root.querySelector("#panels");
    this._chartEl = root.querySelector("#chartwrap");
    this._noteEl = root.querySelector("#note");

    // Hover: read the quarter-hour values under the pointer.
    this._chartEl.addEventListener("mousemove", (event) => this._onMove(event));
    this._chartEl.addEventListener("mouseleave", () => this._onLeave());
    this._chartEl.addEventListener(
      "touchmove",
      (event) => this._onMove(event.touches && event.touches[0]),
      { passive: true }
    );
    this._chartEl.addEventListener("touchend", () => this._onLeave());

    this._built = true;
    this._render();
  }

  // -- data --------------------------------------------------------------

  _refreshIfChanged() {
    const state = this._hass ? this._hass.states[this._sensor] : undefined;
    const signal = state ? state.last_updated : null;
    if (signal !== this._signal || this._signal === undefined) {
      this._signal = signal;
      this._fetch();
    }
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      const response = await this._callServiceWithResponse(
        "goodwe_tou",
        "get_profile",
        {}
      );
      this._profile = response || null;
      this._error = "";
    } catch (err) {
      this._profile = null;
      this._error = (err && err.message) || this._t("loadFailed");
    }
    this._render();
  }

  async _callServiceWithResponse(domain, service, data) {
    const hass = this._hass;
    if (!hass) throw new Error(this._t("haUnavailable"));
    if (typeof hass.callWS === "function") {
      const result = await hass.callWS({
        type: "call_service",
        domain,
        service,
        service_data: data,
        return_response: true,
      });
      return result && result.response !== undefined ? result.response : result;
    }
    const result = await hass.callService(domain, service, data, undefined, true, true);
    return result && result.response !== undefined ? result.response : result;
  }

  // -- rendering ---------------------------------------------------------

  _series(profile, key) {
    const values = (profile && profile[key]) || [];
    const out = [];
    for (let i = 0; i < values.length; i++) {
      const n = Number(values[i]);
      out.push(Number.isFinite(n) ? n : 0);
    }
    return out;
  }

  _render() {
    if (!this._built) return;

    const sensor =
      this._hass && this._hass.states ? this._hass.states[this._sensor] : undefined;
    const attrs = (sensor && sensor.attributes) || {};
    const profile = this._profile || (attrs.load ? attrs : null);
    const enabled = profile ? profile.enabled !== false : attrs.enabled !== false;
    const windowDays = (profile && profile.window_days) || attrs.window_days || 14;
    const days = (profile && profile.days) || attrs.days || 0;

    this._panelsEl.innerHTML = "";

    if (!enabled) {
      this._countEl.textContent = "";
      this._clearHoverState();
      this._chartEl.innerHTML = `<div class="empty">${this._t("disabled")}</div>`;
      this._noteEl.textContent = "";
      return;
    }

    const load = this._series(profile, "load");
    const imports = this._series(profile, "import");
    const exports = this._series(profile, "export");

    if (!load.length && !imports.length && !exports.length) {
      this._countEl.textContent = "";
      this._clearHoverState();
      this._chartEl.innerHTML = `<div class="empty">${
        this._error || this._t("empty")
      }</div>`;
      this._noteEl.textContent = "";
      return;
    }

    this._countEl.textContent = days
      ? this._t("coverage", days, windowDays)
      : "";

    this._renderPanels(profile, load, imports, exports, days);
    this._renderChart(load, imports, exports);

    const note = this._t("note", windowDays);
    const updated = profile && profile.updated ? ` · ${this._t("updated")} ${formatTime(profile.updated)}` : "";
    this._noteEl.textContent = note + updated;
  }

  _renderPanels(profile, load, imports, exports, days = 0) {
    const loadKwh = (profile && profile.load_daily_kwh) ?? this._sumKwh(load);
    const importKwh = (profile && profile.import_daily_kwh) ?? this._sumKwh(imports);
    const exportKwh = (profile && profile.export_daily_kwh) ?? this._sumKwh(exports);
    const loadPeak = load.length ? Math.max(...load) : 0;
    const loadAvg = load.length
      ? load.reduce((a, b) => a + b, 0) / load.length
      : 0;

    const panel = (title, rows) => {
      const el = document.createElement("div");
      el.className = "panel";
      const head = document.createElement("div");
      head.className = "ptitle";
      head.textContent = title;
      el.appendChild(head);
      for (const [k, v] of rows) {
        const row = document.createElement("div");
        row.className = "kv";
        const kEl = document.createElement("span");
        kEl.className = "k";
        kEl.textContent = k;
        const vEl = document.createElement("span");
        vEl.className = "v";
        vEl.textContent = v;
        row.appendChild(kEl);
        row.appendChild(vEl);
        el.appendChild(row);
      }
      return el;
    };

    this._panelsEl.appendChild(
      panel(this._t("perDay"), [
        [this._t("loadKwh"), `${this._num(loadKwh)} kWh`],
        [this._t("importKwh"), `${this._num(importKwh)} kWh`],
        [this._t("exportKwh"), `${this._num(exportKwh)} kWh`],
      ])
    );
    if (days > 0) {
      this._panelsEl.appendChild(
        panel(this._t("period", days), [
          [this._t("loadKwh"), `${this._num(loadKwh * days, 0)} kWh`],
          [this._t("importKwh"), `${this._num(importKwh * days, 0)} kWh`],
          [this._t("exportKwh"), `${this._num(exportKwh * days, 0)} kWh`],
        ])
      );
    }
    this._panelsEl.appendChild(
      panel(this._t("load"), [
        [this._t("peak"), `${this._num(loadPeak)} kW`],
        [this._t("avg"), `${this._num(loadAvg)} kW`],
      ])
    );
  }

  _sumKwh(values) {
    // 96 quarter hours → each quarter is 0.25 h.
    return values.reduce((a, b) => a + b, 0) * 0.25;
  }

  _polyline(values, x0, y0, w, h, yMax) {
    const n = values.length;
    if (!n) return "";
    const stepX = w / Math.max(1, n - 1);
    const pts = [];
    for (let i = 0; i < n; i++) {
      const x = x0 + i * stepX;
      const y = y0 + h - (values[i] / yMax) * h;
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return pts.join(" ");
  }

  _renderChart(load, imports, exports) {
    const W = 960;
    const H = 300;
    const padL = 40;
    const padR = 10;
    const padT = 12;
    const padB = 24;
    const x0 = padL;
    const y0 = padT;
    const w = W - padL - padR;
    const h = H - padT - padB;

    const peak = Math.max(
      0.5,
      ...load,
      ...imports,
      ...exports
    );
    // Round the top up to a tidy step so the axis labels are whole numbers.
    const step = peak > 6 ? 2 : peak > 2 ? 1 : 0.5;
    const yMax = Math.ceil(peak / step) * step;

    const grid = [];
    for (let t = 0; t <= yMax + 1e-9; t += step) {
      const y = y0 + h - (t / yMax) * h;
      grid.push(
        `<line class="gridline" x1="${x0}" y1="${y.toFixed(1)}" x2="${(
          x0 + w
        ).toFixed(1)}" y2="${y.toFixed(1)}" />`
      );
      grid.push(
        `<text class="axislabel" x="${x0 - 6}" y="${(y + 3).toFixed(
          1
        )}" text-anchor="end">${this._num(t, t < 10 ? 1 : 0)}</text>`
      );
    }
    const hourTicks = [0, 6, 12, 18, 24];
    for (const hour of hourTicks) {
      const x = x0 + (hour / 24) * w;
      grid.push(
        `<line class="gridline" x1="${x.toFixed(1)}" y1="${y0}" x2="${x.toFixed(
          1
        )}" y2="${(y0 + h).toFixed(1)}" />`
      );
      grid.push(
        `<text class="axislabel" x="${x.toFixed(1)}" y="${(
          y0 + h + 15
        ).toFixed(1)}" text-anchor="middle">${hour}:00</text>`
      );
    }

    const loadPts = this._polyline(load, x0, y0, w, h, yMax);
    const importPts = this._polyline(imports, x0, y0, w, h, yMax);
    const exportPts = this._polyline(exports, x0, y0, w, h, yMax);
    const loadArea = loadPts
      ? `${x0},${y0 + h} ${loadPts} ${x0 + w},${y0 + h}`
      : "";

    this._chartEl.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        ${grid.join("")}
        ${loadArea ? `<polygon class="loadfill" points="${loadArea}" />` : ""}
        ${
          exportPts
            ? `<polyline class="exportline" points="${exportPts}" />`
            : ""
        }
        ${
          importPts
            ? `<polyline class="importline" points="${importPts}" />`
            : ""
        }
        ${loadPts ? `<polyline class="loadline" points="${loadPts}" />` : ""}
        <g id="hover"></g>
      </svg>
      <div class="tooltip" id="tip"></div>
    `;

    // Remember the geometry so the hover handler can map the pointer back to a
    // quarter hour and read all three series at that moment.
    const n = Math.max(load.length, imports.length, exports.length);
    const svg = this._chartEl.querySelector("svg");
    this._hoverSvg = svg;
    this._hoverGroup = svg ? svg.querySelector("#hover") : null;
    this._tipEl = this._chartEl.querySelector("#tip");
    this._geom = { W, H, x0, y0, w, h, yMax, n, load, imports, exports };
    if (this._hoverPos) this._updateHover();
    else this._hideHover();
  }

  // -- hover -------------------------------------------------------------

  _onMove(event) {
    if (!event) return;
    this._hoverPos = { clientX: event.clientX, clientY: event.clientY };
    this._updateHover();
  }

  _onLeave() {
    this._hoverPos = null;
    this._hideHover();
  }

  _clearHoverState() {
    this._geom = null;
    this._hoverSvg = null;
    this._hoverGroup = null;
    this._tipEl = null;
  }

  _hideHover() {
    if (this._hoverGroup) this._hoverGroup.replaceChildren();
    if (this._tipEl) this._tipEl.classList.remove("show");
  }

  // Snap the pointer to the nearest quarter hour and show all three values.
  _updateHover() {
    const geom = this._geom;
    const svg = this._hoverSvg;
    const tip = this._tipEl;
    if (!geom || !svg || !tip || !this._hoverPos) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const box = this._chartEl.getBoundingClientRect();

    const vx = ((this._hoverPos.clientX - rect.left) / rect.width) * geom.W;
    const stepX = geom.w / Math.max(1, geom.n - 1);
    const idx = clamp(Math.round((vx - geom.x0) / stepX), 0, geom.n - 1);
    const px = geom.x0 + idx * stepX;

    const loadV = geom.load[idx] ?? 0;
    const importV = geom.imports[idx] ?? 0;
    const exportV = geom.exports[idx] ?? 0;
    const yOf = (value) => geom.y0 + geom.h - (value / geom.yMax) * geom.h;
    const pyLoad = yOf(loadV);

    const group = this._hoverGroup;
    if (group) {
      group.replaceChildren();
      group.appendChild(
        svgEl("line", {
          x1: px,
          y1: geom.y0,
          x2: px,
          y2: geom.y0 + geom.h,
          class: "crosshair",
        })
      );
      for (const [value, color] of [
        [loadV, "#2196f3"],
        [importV, "#e53935"],
        [exportV, "#43a047"],
      ]) {
        group.appendChild(
          svgEl("circle", { cx: px, cy: yOf(value), r: 3.5, fill: color, class: "hoverdot" })
        );
      }
    }

    const minutes = idx * 15;
    const time = `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
    tip.textContent =
      `${time}\n` +
      `${this._t("load")}  ${this._num(loadV)} ${this._t("kw")}\n` +
      `${this._t("import")}  ${this._num(importV)} ${this._t("kw")}\n` +
      `${this._t("export")}  ${this._num(exportV)} ${this._t("kw")}`;

    const cx = (px / geom.W) * rect.width + (rect.left - box.left);
    const cy = (pyLoad / geom.H) * rect.height + (rect.top - box.top);
    tip.style.left = `${clamp(cx, 72, Math.max(72, box.width - 72))}px`;
    tip.style.top = `${cy}px`;
    tip.classList.toggle("below", cy < 70);
    tip.classList.add("show");
  }

  static getStubConfig() {
    return { type: "custom:goodwe-tou-profile-card" };
  }
}

if (!customElements.get("goodwe-tou-profile-card")) {
  customElements.define("goodwe-tou-profile-card", GoodweTouProfileCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "goodwe-tou-profile-card")) {
  window.customCards.push({
    type: "goodwe-tou-profile-card",
    name: "GoodWe Time-of-Use consumption profile",
    description:
      "The measured quarter-hour house load, grid import and export (duck curve).",
  });
}
