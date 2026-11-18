/*
 * goodwe-tou-deviation-card
 *
 * Shows how far reality drifted from the plan, and what the integration learned
 * from it. The planner assumes a house load; this card compares that assumption
 * with what the house really used, hour by hour, together with the variables
 * that influence it: the outdoor temperature, the feels-like (apparent)
 * temperature, the heating degree hours and the PV forecast error.
 *
 * The log and the house-load model fitted from it live in the integration
 * (persisted across restarts) and are fetched through the
 * `goodwe_tou.get_deviations` service, so the card can show more samples than fit
 * in a state attribute. It refreshes whenever the deviation sensor changes.
 *
 * The raw temperature, humidity, wind and load history stays in Home Assistant's
 * recorder database; only these derived samples are kept by the integration, so
 * they can be retained much longer.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-deviation-card
 *   title: Afwijking                          # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                # optional entity-id prefix
 *   sensor: sensor.goodwe_time_of_use_afwijking   # optional, refresh trigger
 *   limit: 200                                # optional, samples fetched
 *   language: en                              # optional, overrides the language
 */

const CARD_VERSION = "1.4.0";
const PREFIX_DEFAULT = "goodwe_time_of_use";
const LIMIT_DEFAULT = 200;

// Everything the card says, per language. A value may be a function when it
// takes an argument (a count, a value).
const LANGS = {
  nl: {
    title: "Afwijking",
    samples: (n) => `${n} uurmetingen`,
    samplesOf: (shown, total) => `${shown} van ${total} uurmetingen`,
    empty: "Nog geen afwijkingsmetingen verzameld.",
    emptyFiltered: "Geen metingen voor deze selectie.",
    loadFailed: "Kon het afwijkingslogboek niet laden",
    haUnavailable: "Home Assistant is niet beschikbaar",
    note: (limit) =>
      `Nieuwste ${limit} uurmetingen. Alleen de afgeleide waarden worden bewaard; de ruwe sensorgeschiedenis blijft in de recorder.`,
    model: "Geleerd huisverbruik",
    modelNone: "Nog aan het leren",
    modelWaiting: (n, need) => `${n} van ${need} uurmetingen voor een eigen model.`,
    base: "Basis",
    coeff: "Per graad",
    fit: "Metingen",
    r2: "Verklaarde variantie",
    now: "Nu gerekend",
    nowValue: (kw) => `${kw.toFixed(2)} kW`,
    today: "Vandaag",
    actual: "Werkelijk",
    planned: "Gepland",
    deviation: "Afwijking",
    degreeDays: "Graaddagen",
    feelsLike: "Gevoelstemperatuur",
    pvError: "PV-fout",
    filter: "Periode",
    all: "Alles",
    todayOnly: "Vandaag",
    time: "Tijd",
    outdoor: "Buiten",
    feels: "Gevoel",
    degree: "Graden",
    pv: "PV",
    columnActual: "Werkelijk (kWh)",
    columnPlanned: "Gepland (kWh)",
    columnDeviation: "Afwijking (kWh)",
    columnOutdoor: "Buiten (\u00b0C)",
    columnFeels: "Gevoel (\u00b0C)",
    columnDegree: "Graden (\u00b0Ch)",
    columnPv: "PV-fout (kWh)",
    noValue: "\u2013",
  },
  en: {
    title: "Deviation",
    samples: (n) => `${n} hourly samples`,
    samplesOf: (shown, total) => `${shown} of ${total} hourly samples`,
    empty: "No deviation samples collected yet.",
    emptyFiltered: "No samples for this selection.",
    loadFailed: "Could not load the deviation log",
    haUnavailable: "Home Assistant is not available",
    note: (limit) =>
      `Newest ${limit} hourly samples. Only the derived values are kept; the raw sensor history stays in the recorder.`,
    model: "Learned house load",
    modelNone: "Still learning",
    modelWaiting: (n, need) => `${n} of ${need} hourly samples for its own model.`,
    base: "Base",
    coeff: "Per degree",
    fit: "Samples",
    r2: "Explained variance",
    now: "Assumed now",
    nowValue: (kw) => `${kw.toFixed(2)} kW`,
    today: "Today",
    actual: "Actual",
    planned: "Planned",
    deviation: "Deviation",
    degreeDays: "Degree days",
    feelsLike: "Feels-like",
    pvError: "PV error",
    filter: "Period",
    all: "All",
    todayOnly: "Today",
    time: "Time",
    outdoor: "Outdoor",
    feels: "Feels",
    degree: "Degrees",
    pv: "PV",
    columnActual: "Actual (kWh)",
    columnPlanned: "Planned (kWh)",
    columnDeviation: "Deviation (kWh)",
    columnOutdoor: "Outdoor (\u00b0C)",
    columnFeels: "Feels (\u00b0C)",
    columnDegree: "Degrees (\u00b0Ch)",
    columnPv: "PV error (kWh)",
    noValue: "\u2013",
  },
};

// The integration blends the fitted model with the configured defaults until
// this many samples are in (see deviation.MIN_SAMPLES).
const MIN_SAMPLES = 24;

// Map any language tag (nl, nl-NL, en-GB, ...) onto a language we ship.
const resolveLangCode = (value) => {
  const code = String(value || "").toLowerCase();
  if (code.startsWith("en")) return "en";
  return "nl";
};

const pad = (value) => String(value).padStart(2, "0");

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

const isToday = (iso) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; gap: 8px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .count { font-size: .78rem; color: var(--secondary-text-color); }

  .panels { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
  .panel { flex: 1 1 240px; border: 1px solid var(--divider-color); border-radius: 10px; padding: 9px 11px; background: var(--card-background-color); }
  .ptitle { font-size: .74rem; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); margin-bottom: 6px; }
  .kv { display: flex; justify-content: space-between; gap: 10px; font-size: .84rem; line-height: 1.7; }
  .kv .k { color: var(--secondary-text-color); }
  .kv .v { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .kv .v.pos { color: #c62828; }
  .kv .v.neg { color: #2e7d32; }
  .muted { font-size: .84rem; color: var(--secondary-text-color); }

  .filters { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }
  .filters .flabel { font-size: .74rem; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg button { border: none; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .8rem; padding: 4px 10px; cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--divider-color); }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }

  .tablewrap { max-height: 380px; overflow: auto; border: 1px solid var(--divider-color); border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: .8rem; font-variant-numeric: tabular-nums; }
  thead th { position: sticky; top: 0; background: var(--card-background-color); color: var(--secondary-text-color); font-weight: 600; text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  thead th:first-child { text-align: left; }
  tbody td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--divider-color); color: var(--primary-text-color); white-space: nowrap; }
  tbody td:first-child { text-align: left; color: var(--secondary-text-color); }
  tbody tr:last-child td { border-bottom: none; }
  td.pos { color: #c62828; }
  td.neg { color: #2e7d32; }

  .empty { padding: 14px 4px; color: var(--secondary-text-color); font-size: .86rem; }
  .note { margin-top: 8px; font-size: .74rem; color: var(--secondary-text-color); }
`;

class GoodweTouDeviationCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entries = [];
    this._learned = null;
    this._today = null;
    this._signal = undefined;
    this._period = "all";
    this._error = "";
    this._lang = "nl";
    this._langOverride = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._langOverride = this._config.language || null;
    this._title = this._config.title || null;
    this._sensor = this._config.sensor || `sensor.${this._prefix}_afwijking`;
    this._limit =
      Number(this._config.limit) > 0 ? Number(this._config.limit) : LIMIT_DEFAULT;
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
    return 12;
  }

  // -- language ----------------------------------------------------------

  // Card `language:` override, else the language the integration speaks (on the
  // deviation sensor), else Home Assistant's own language, else Dutch.
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

  _signedKwh(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return this._t("noValue");
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}`;
  }

  _deviationClass(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return "";
    // More use than planned is a cost (red); less is a saving (green).
    return n > 0 ? "pos" : "neg";
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
        <div class="panels">
          <div class="panel">
            <div class="ptitle">${this._t("model")}</div>
            <div id="model"></div>
          </div>
          <div class="panel">
            <div class="ptitle">${this._t("today")}</div>
            <div id="today"></div>
          </div>
        </div>
        <div class="filters">
          <span class="flabel">${this._t("filter")}</span>
          <span class="seg" id="periodseg"></span>
        </div>
        <div class="tablewrap">
          <table>
            <thead>
              <tr>
                <th>${this._t("time")}</th>
                <th>${this._t("columnActual")}</th>
                <th>${this._t("columnPlanned")}</th>
                <th>${this._t("columnDeviation")}</th>
                <th>${this._t("columnOutdoor")}</th>
                <th>${this._t("columnFeels")}</th>
                <th>${this._t("columnDegree")}</th>
                <th>${this._t("columnPv")}</th>
              </tr>
            </thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
        <div class="note" id="note"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${
      this._title || this._t("title")
    } (Version ${CARD_VERSION})`;
    this._countEl = root.querySelector("#count");
    this._modelEl = root.querySelector("#model");
    this._todayEl = root.querySelector("#today");
    this._rowsEl = root.querySelector("#rows");
    this._noteEl = root.querySelector("#note");

    const seg = root.querySelector("#periodseg");
    this._periodBtns = [];
    for (const [value, label] of [
      ["all", this._t("all")],
      ["today", this._t("todayOnly")],
    ]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.value = value;
      button.addEventListener("click", () => {
        this._period = value;
        this._render();
      });
      seg.appendChild(button);
      this._periodBtns.push(button);
    }

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
        "get_deviations",
        { limit: this._limit }
      );
      this._entries = (response && response.entries) || [];
      this._learned = (response && response.learned) || null;
      this._today = (response && response.today) || null;
      this._error = "";
    } catch (err) {
      this._entries = [];
      this._learned = null;
      this._today = null;
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

  _render() {
    if (!this._built) return;

    for (const button of this._periodBtns) {
      button.classList.toggle("on", button.dataset.value === this._period);
    }

    const sensor =
      this._hass && this._hass.states ? this._hass.states[this._sensor] : undefined;
    const attrs = (sensor && sensor.attributes) || {};
    const learned = this._learned || attrs.learned || null;
    const today = this._today || attrs.today || null;
    const effective = attrs.effective_kw;

    this._renderModel(learned, effective);
    this._renderToday(today);

    const entries = this._entries.filter(
      (entry) => this._period !== "today" || isToday(entry.time)
    );
    this._countEl.textContent =
      entries.length === this._entries.length
        ? this._t("samples", this._entries.length)
        : this._t("samplesOf", entries.length, this._entries.length);

    this._rowsEl.innerHTML = "";
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      if (this._error) {
        empty.textContent = this._error;
      } else if (this._entries.length) {
        empty.textContent = this._t("emptyFiltered");
      } else {
        empty.textContent = this._t("empty");
      }
      const cell = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.appendChild(empty);
      cell.appendChild(td);
      this._rowsEl.appendChild(cell);
    }
    for (const entry of entries) {
      this._rowsEl.appendChild(this._renderRow(entry));
    }

    if (this._noteEl) {
      this._noteEl.textContent = this._error ? "" : this._t("note", this._limit);
    }
  }

  _renderModel(learned, effective) {
    this._modelEl.innerHTML = "";
    const samples = learned ? Number(learned.samples) || 0 : 0;
    if (!learned || samples < 1) {
      const waiting = document.createElement("div");
      waiting.className = "muted";
      waiting.textContent = this._t("modelWaiting", samples, MIN_SAMPLES);
      this._modelEl.appendChild(waiting);
    } else {
      this._modelEl.appendChild(
        this._kv(this._t("base"), `${this._num(learned.base_kw)} kW`)
      );
      this._modelEl.appendChild(
        this._kv(
          this._t("coeff"),
          `${this._num(learned.temp_coeff)} kW/\u00b0C`
        )
      );
      this._modelEl.appendChild(
        this._kv(this._t("fit"), String(samples))
      );
      this._modelEl.appendChild(
        this._kv(this._t("r2"), this._num(learned.r2, 3))
      );
    }
    if (Number.isFinite(Number(effective))) {
      this._modelEl.appendChild(
        this._kv(this._t("now"), this._t("nowValue", Number(effective)))
      );
    }
  }

  _renderToday(today) {
    this._todayEl.innerHTML = "";
    if (!today) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = this._t("noValue");
      this._todayEl.appendChild(empty);
      return;
    }
    const rows = [
      [this._t("actual"), `${this._num(today.actual_kwh)} kWh`, ""],
      [this._t("planned"), `${this._num(today.planned_kwh)} kWh`, ""],
      [
        this._t("deviation"),
        `${this._signedKwh(today.deviation_kwh)} kWh`,
        this._deviationClass(today.deviation_kwh),
      ],
      [this._t("degreeDays"), this._num(today.degree_days), ""],
    ];
    if (today.feels_like !== null && today.feels_like !== undefined) {
      rows.push([
        this._t("feelsLike"),
        `${this._num(today.feels_like, 1)} \u00b0C`,
        "",
      ]);
    }
    if (today.pv_error_kwh !== null && today.pv_error_kwh !== undefined) {
      rows.push([
        this._t("pvError"),
        `${this._signedKwh(today.pv_error_kwh)} kWh`,
        this._deviationClass(-Number(today.pv_error_kwh)),
      ]);
    }
    for (const [key, value, cls] of rows) {
      this._todayEl.appendChild(this._kv(key, value, cls));
    }
  }

  _kv(key, value, cls = "") {
    const row = document.createElement("div");
    row.className = "kv";
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = key;
    const v = document.createElement("span");
    v.className = cls ? `v ${cls}` : "v";
    v.textContent = value;
    row.appendChild(k);
    row.appendChild(v);
    return row;
  }

  _renderRow(entry) {
    const row = document.createElement("tr");
    const cells = [
      [formatTime(entry.time), ""],
      [this._num(entry.actual_kwh), ""],
      [this._num(entry.planned_kwh), ""],
      [this._signedKwh(entry.deviation_kwh), this._deviationClass(entry.deviation_kwh)],
      [this._num(entry.outdoor_temp, 1), ""],
      [this._num(entry.feels_like, 1), ""],
      [this._num(entry.degree_hours, 2), ""],
      [
        entry.pv_error_kwh === null || entry.pv_error_kwh === undefined
          ? this._t("noValue")
          : this._signedKwh(entry.pv_error_kwh),
        this._deviationClass(-Number(entry.pv_error_kwh)),
      ],
    ];
    for (const [text, cls] of cells) {
      const td = document.createElement("td");
      if (cls) td.className = cls;
      td.textContent = text;
      row.appendChild(td);
    }
    return row;
  }
}

if (!customElements.get("goodwe-tou-deviation-card")) {
  customElements.define("goodwe-tou-deviation-card", GoodweTouDeviationCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goodwe-tou-deviation-card",
  name: "GoodWe Time-of-Use deviation",
  description:
    "Plan-versus-reality deviation log with the weather variables that influence it, and the house-load model learned from it.",
  preview: false,
});

console.info(`%c GOODWE-TOU-DEVIATION-CARD %c v${CARD_VERSION} `, "background:#2e7d32;color:#fff;font-weight:bold", "background:#333;color:#fff");
