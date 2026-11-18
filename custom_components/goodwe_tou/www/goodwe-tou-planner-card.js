/*
 * goodwe-tou-planner-card
 *
 * A planner for the GoodWe Time-of-Use integration.
 *
 * One 24-hour timeline (00:00-24:00) for a chosen day, with a week strip on
 * top to pick the day. The four inverter windows are drawn as four compact,
 * log-like rows ("sliders") on that same axis:
 *
 *   - each row shows the window number, a Laden/Ontladen button, the power
 *     percentage, the start and stop time, and the days it runs on;
 *   - the bar under the row is the slider: drag the left/right handle to move
 *     the start/stop. Windows snap to quarter hours and can never overlap:
 *     a handle stops at the neighbouring window;
 *   - an unconfigured window is grey and stays collapsed until it is set
 *     active; a window that has already passed today is greyed, tagged
 *     "afgelopen" and moved to the bottom of the list.
 *
 * The timeline's grey background is the inverter's **general mode**: outside
 * the windows the GoodWe is not in a Time-of-Use block. A summary band above
 * the rows shows this at a glance (grey = general mode, red = charging,
 * green = discharging), together with what the inverter is doing *now*.
 *
 * The number of a window is the number in the inverter; the card only reorders
 * the rows on screen (expired/inactive windows drop down) and writes every
 * window back to its own number.
 *
 * Buttons at the bottom:
 *   - "Lezen van omvormer" pulls the live schedule from the inverter;
 *   - "Optimale prijzen berekenen" asks the integration to plan the cheapest
 *     hours and fills the rows with the result, without writing anything;
 *   - "Toepassen" writes the whole plan through goodwe_tou.set_windows.
 *
 * Configure with:
 *
 *   type: custom:goodwe-tou-planner-card
 *   title: Laadplanning                                  # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                           # optional
 *   price_entity: sensor.average_electricity_price       # optional
 *   slot_minutes: 15                                     # optional, timeline granularity
 */

const CARD_VERSION = "1.4.1";
const PREFIX_DEFAULT = "goodwe_time_of_use";
const MAX_WINDOWS = 4;
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_MINUTES = 1440;

// Everything the card says, per language. The Dutch table mirrors the original
// card text exactly; the English table is the translation. A value may be a
// function when it takes an argument (a day label, a window number).
const LANGS = {
  nl: {
    title: "Laadplanning",
    autoEco: "Auto/ECO Mode",
    manualOverride: "Manual override",
    read: "Lezen van omvormer",
    calc: "Optimale prijzen berekenen",
    apply: "Toepassen",
    dayLabels: ["ma", "di", "wo", "do", "vr", "za", "zo"],
    dayFull: [
      "Maandag",
      "Dinsdag",
      "Woensdag",
      "Donderdag",
      "Vrijdag",
      "Zaterdag",
      "Zondag",
    ],
    noDays: "geen",
    everyDay: "elke dag",
    weekdays: "ma\u2013vr",
    weekend: "za\u2013zo",
    todaySuffix: " (vandaag)",
    hint: "sleep een balk om de tijd te wijzigen; vensters mogen elkaar niet overlappen.",
    charge: "laden",
    discharge: "ontladen",
    generalMode: "algemene modus",
    windowN: (n) => `venster ${n}`,
    notOn: (d) => `niet op ${d}`,
    inactive: "inactief",
    expired: "afgelopen",
    active: "actief",
    activate: "activeren",
    notActiveOn: (d) => `niet actief op ${d}`,
    greyGeneral: "Grijs = algemene modus (buiten de vensters)",
    nowPrefix: "Nu: ",
    whyTimes: "Waarom deze tijden",
    windowPill: (n) => `Venster ${n}`,
    planPill: "Plan",
    ecoTitle: "ECO-modus aan en automatische herberekening actief",
    manualTitle: "Automatische herberekening pauzeren; vensters blijven staan",
    haUnavailable: "Home Assistant is niet beschikbaar",
    actionFailed: "Actie mislukt",
  },
  en: {
    title: "Charge planning",
    autoEco: "Auto/ECO Mode",
    manualOverride: "Manual override",
    read: "Read from inverter",
    calc: "Set cheapest hours",
    apply: "Apply",
    dayLabels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    dayFull: [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
      "Sunday",
    ],
    noDays: "none",
    everyDay: "every day",
    weekdays: "Mon\u2013Fri",
    weekend: "Sat\u2013Sun",
    todaySuffix: " (today)",
    hint: "drag a bar to change the time; windows may not overlap.",
    charge: "charge",
    discharge: "discharge",
    generalMode: "general mode",
    windowN: (n) => `window ${n}`,
    notOn: (d) => `not on ${d}`,
    inactive: "inactive",
    expired: "expired",
    active: "active",
    activate: "activate",
    notActiveOn: (d) => `not active on ${d}`,
    greyGeneral: "Grey = general mode (outside the windows)",
    nowPrefix: "Now: ",
    whyTimes: "Why these times",
    windowPill: (n) => `Window ${n}`,
    planPill: "Plan",
    ecoTitle: "ECO mode on and automatic re-check active",
    manualTitle: "Pause automatic re-check; windows stay as they are",
    haUnavailable: "Home Assistant is not available",
    actionFailed: "Action failed",
  },
};

// Map any language tag (nl, nl-NL, en-GB, ...) onto a language we ship.
const resolveLangCode = (value) => {
  const code = String(value || "").toLowerCase();
  if (code.startsWith("en")) return "en";
  return "nl";
};

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const pad = (value) => String(value).padStart(2, "0");
const escapeHtml = (value) =>
  String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const startOfDay = (date) => {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
};
const addDays = (date, days) => {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
};
// Monday = 0 ... Sunday = 6.
const weekdayIndex = (date) => (date.getDay() + 6) % 7;
const hm = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const fmtHM = (time) => `${pad(time.h)}:${pad(time.m)}`;
const parseHM = (text) => {
  const parts = String(text || "0:0").split(":");
  return {
    h: clamp(Number(parts[0]) || 0, 0, 23),
    m: clamp(Number(parts[1]) || 0, 0, 59),
  };
};
const minutesOf = (time) => time.h * 60 + time.m;
const timeOf = (minutes) => ({
  h: Math.floor(minutes / 60),
  m: minutes % 60,
});

const expandDays = (text) => {
  const value = String(text || "").toLowerCase();
  if (value.includes("all") || value.includes("every") || value.includes("mon-sun")) {
    return new Set(DAY_KEYS);
  }
  if (value.includes("weekday")) return new Set(DAY_KEYS.slice(0, 5));
  if (value.includes("weekend")) return new Set(DAY_KEYS.slice(5));
  if (!value || value === "none" || value === "off" || value === "-") return new Set();
  return new Set(DAY_KEYS.filter((key) => value.includes(key)));
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .modes { display: flex; gap: 6px; }
  .modebtn { font: inherit; font-size: .78rem; padding: 5px 10px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--secondary-text-color); cursor: pointer; }
  .modebtn.active { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 600; }

  .week { display: flex; gap: 4px; margin-bottom: 8px; }
  .daybtn { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 5px 0 4px; border: 1px solid var(--divider-color); border-radius: 8px; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .74rem; cursor: pointer; }
  .daybtn .dcount { font-size: .62rem; opacity: .85; }
  .daybtn.today { border-color: var(--primary-color); color: var(--primary-color); }
  .daybtn.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }

  .hint { font-size: .76rem; color: var(--secondary-text-color); margin-bottom: 8px; min-height: 1.1em; }

  .ruler { position: relative; height: 16px; }
  .ruler .tick { position: absolute; top: 9px; bottom: 0; width: 1px; background: var(--divider-color); opacity: .6; }
  .ruler .tick.major { top: 3px; opacity: 1; }
  .ruler .tlabel { position: absolute; top: 0; transform: translateX(-50%); font-size: .62rem; color: var(--secondary-text-color); }

  .prices { position: relative; height: 128px; margin: 2px 0 6px; display: grid; grid-template-columns: 34px 1fr; gap: 4px; }
  .prices .yaxis { position: relative; }
  .prices .ylab { position: absolute; right: 2px; transform: translateY(50%); font-size: .62rem; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .prices .plot { position: relative; border-radius: 6px; background: var(--secondary-background-color); overflow: hidden; }
  .prices .grid { position: absolute; left: 0; right: 0; height: 1px; background: var(--divider-color); opacity: .45; }
  .prices .grid.zero { opacity: .9; background: var(--secondary-text-color); }
  .pbar { position: absolute; bottom: 0; border-radius: 2px 2px 0 0; opacity: .85; }
  ha-card.with-axis .ruler,
  ha-card.with-axis .band,
  ha-card.with-axis .wtrack { margin-left: 38px; }

  .band { position: relative; height: 16px; border-radius: 6px; background: var(--secondary-background-color); overflow: hidden; margin-bottom: 4px; }
  .band .seg { position: absolute; top: 0; bottom: 0; }
  .band .seg.charge { background: linear-gradient(180deg, #ef5350, #b71c1c); }
  .band .seg.discharge { background: linear-gradient(180deg, #66bb6a, #1b5e20); }
  .band .now { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-color); }
  .bandcap { display: flex; justify-content: space-between; gap: 10px; font-size: .72rem; color: var(--secondary-text-color); margin-bottom: 10px; }
  .bandcap .nowmode { font-weight: 600; color: var(--primary-text-color); }

  .rows { display: flex; flex-direction: column; }
  .wrow { padding: 6px 0; border-top: 1px solid var(--divider-color); }
  .wrow:first-child { border-top: none; }
  .wrow.off { opacity: .5; }
  .wrow.dim { opacity: .6; }
  .wrow.expired { opacity: .6; }

  .wmeta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: .78rem; }
  .wnum { font-weight: 700; color: var(--primary-text-color); min-width: 12px; }
  button.mode { font: inherit; font-size: .72rem; padding: 2px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--secondary-text-color); cursor: pointer; }
  button.mode.charge { background: #c62828; border-color: #c62828; color: #fff; }
  button.mode.discharge { background: #2e7d32; border-color: #2e7d32; color: #fff; }
  button.mode:disabled { cursor: default; }
  .pct { width: 50px; background: transparent; border: 1px solid var(--divider-color); border-radius: 6px; color: var(--primary-text-color); font: inherit; font-size: .74rem; padding: 1px 4px; text-align: right; }
  .wmeta input[type=time] { background: transparent; border: 1px solid var(--divider-color); border-radius: 6px; color: var(--primary-text-color); font: inherit; font-size: .74rem; padding: 1px 3px; }
  .wmeta .dash { color: var(--secondary-text-color); }
  .spacer { flex: 1 1 auto; }
  .wtag { font-size: .7rem; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .wtag.expired { color: var(--warning-color, #ff9800); }
  button.toggle { font: inherit; font-size: .7rem; padding: 2px 9px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--secondary-text-color); cursor: pointer; }
  button.toggle.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.daysbtn { font: inherit; font-size: .7rem; padding: 1px 6px; border-radius: 6px; border: 1px dashed var(--divider-color); background: transparent; color: var(--secondary-text-color); cursor: pointer; }

  .wtrack { position: relative; height: 16px; margin-top: 4px; border-radius: 6px; background: var(--secondary-background-color); overflow: hidden; }
  .wtrack .bar { position: absolute; top: 2px; bottom: 2px; border-radius: 4px; background: var(--disabled-color, #9e9e9e); min-width: 8px; }
  .wtrack .bar.charge { background: linear-gradient(180deg, #ef5350, #b71c1c); }
  .wtrack .bar.discharge { background: linear-gradient(180deg, #66bb6a, #1b5e20); }
  .wtrack .bar.expired { filter: grayscale(.75); opacity: .7; }
  .wtrack .blabel { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: .62rem; font-weight: 600; color: #fff; white-space: nowrap; pointer-events: none; text-shadow: 0 1px 2px rgba(0,0,0,.5); overflow: hidden; }
  .wtrack .handle { position: absolute; top: 0; bottom: 0; width: 10px; border-radius: 3px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.3); cursor: ew-resize; touch-action: none; }
  .wtrack .handle.left { left: 0; }
  .wtrack .handle.right { right: 0; }
  .wtrack .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: .68rem; color: var(--secondary-text-color); }

  .wdays { display: none; gap: 3px; margin-top: 6px; }
  .wdays.show { display: flex; }
  .chip { flex: 1; padding: 3px 0; border-radius: 5px; border: 1px solid var(--divider-color); background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .66rem; cursor: pointer; }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }

  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .actions button { flex: 1 1 30%; padding: 9px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; cursor: pointer; }
  .actions button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .actions button:active { transform: translateY(1px); }
  .error { color: var(--error-color, #d32f2f); font-size: .8rem; margin-top: 8px; min-height: 1em; }

  .reasons { display: none; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--divider-color); }
  .reasons.show { display: block; }
  .reasons h4 { margin: 0 0 8px; font-size: .85rem; font-weight: 600; color: var(--primary-text-color); }
  .reasons .note { font-size: .78rem; color: var(--secondary-text-color); margin-bottom: 8px; }
  .reasons .note + .note { margin-top: -4px; }
  .reasons ul { list-style: none; margin: 0; padding: 0; }
  .reasons li { display: flex; gap: 8px; align-items: flex-start; font-size: .8rem; color: var(--primary-text-color); padding: 5px 0; }
  .reasons li + li { border-top: 1px solid var(--divider-color); }
  .reasons .pill { flex: none; font-size: .7rem; font-weight: 600; padding: 2px 7px; border-radius: 10px; color: var(--text-primary-color, #fff); background: var(--disabled-color, #9e9e9e); }
  .reasons .pill.charge { background: var(--primary-color); }
  .reasons .pill.discharge { background: var(--warning-color, #ff9800); }
  .reasons .why { flex: 1 1 auto; }
`;

class GoodweTouPlannerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._dirty = false;
    this._drag = null;
    this._segs = [];
    this._rows = [];
    this._priceSlots = [];
    this._slot = 15;
    this._now = new Date();
    this._todayIdx = weekdayIndex(this._now);
    this._day = this._todayIdx;
    this._dayChosen = false;
    this._order = [];
    this._orderKey = "";
    this._reasons = [];
    this._notes = [];
    this._summary = "";
    this._lang = "nl";
    this._langOverride = null;
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._langOverride = this._config.language || null;
    this._title = this._config.title || null;
    this._priceEntity = this._config.price_entity || null;
    this._slot =
      Number(this._config.slot_minutes) > 0 ? Number(this._config.slot_minutes) : 15;
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

    this._now = new Date();
    this._todayIdx = weekdayIndex(this._now);
    if (!this._dayChosen) this._day = this._todayIdx;

    const priceState = this._priceEntity ? this._state(this._priceEntity) : undefined;
    const priceSig = priceState ? priceState.last_updated : null;
    if (priceSig !== this._priceSig) {
      this._priceSig = priceSig;
      this._priceSlots = this._detectPrices();
    }
    if (!this._dirty) this._readWindows();
    this._renderModes();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 12;
  }

  // -- language ----------------------------------------------------------

  // Card `language:` override, else the language the integration speaks (on the
  // activity sensor), else Home Assistant's own language, else Dutch.
  _resolveLang(hass) {
    if (this._langOverride) return resolveLangCode(this._langOverride);
    const activity = this._state(this._entity("sensor", "activity_log"));
    const attr = activity && activity.attributes && activity.attributes.language;
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

  _dayLabel(index) {
    return this._t("dayLabels")[index];
  }

  _dayFull(index) {
    return this._t("dayFull")[index];
  }

  _daysToText(set) {
    if (!set || !set.size) return this._t("noDays");
    if (set.size === 7) return this._t("everyDay");
    if (set.size === 5 && DAY_KEYS.slice(0, 5).every((key) => set.has(key))) {
      return this._t("weekdays");
    }
    if (set.size === 2 && DAY_KEYS.slice(5).every((key) => set.has(key))) {
      return this._t("weekend");
    }
    return DAY_KEYS.filter((key) => set.has(key))
      .map((key) => this._dayLabel(DAY_KEYS.indexOf(key)))
      .join(", ");
  }

  _entity(domain, suffix) {
    return `${domain}.${this._prefix}_${suffix}`;
  }

  _state(entityId) {
    return this._hass ? this._hass.states[entityId] : undefined;
  }

  _selectedKey() {
    return DAY_KEYS[this._day];
  }

  _isToday() {
    return this._day === this._todayIdx;
  }

  _nowMinutes() {
    const now = this._now || new Date();
    return now.getHours() * 60 + now.getMinutes();
  }

  _dayStart() {
    const base = startOfDay(this._now || new Date());
    return addDays(base, this._day - weekdayIndex(base));
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
          <div class="modes">
            <button id="auto_eco" class="modebtn" type="button">${this._t("autoEco")}</button>
            <button id="manual" class="modebtn" type="button">${this._t("manualOverride")}</button>
          </div>
        </div>
        <div class="week" id="week"></div>
        <div class="hint" id="hint"></div>
        <div class="ruler" id="ruler"></div>
        <div class="prices" id="prices">
          <div class="yaxis" id="yaxis"></div>
          <div class="plot" id="priceplot"></div>
        </div>
        <div class="band" id="band"></div>
        <div class="bandcap" id="bandcap"></div>
        <div class="rows" id="rows"></div>
        <div class="actions">
          <button id="read" type="button">${this._t("read")}</button>
          <button id="calc" type="button">${this._t("calc")}</button>
          <button id="apply" class="primary" type="button">${this._t("apply")}</button>
        </div>
        <div class="reasons" id="reasons"></div>
        <div class="error" id="error"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${
      this._title || this._t("title")
    } (Version ${CARD_VERSION})`;
    this._weekEl = root.querySelector("#week");
    this._hintEl = root.querySelector("#hint");
    this._rulerEl = root.querySelector("#ruler");
    this._pricesEl = root.querySelector("#prices");
    this._axisEl = root.querySelector("#yaxis");
    this._plotEl = root.querySelector("#priceplot");
    this._cardEl = root.querySelector("ha-card");
    this._bandEl = root.querySelector("#band");
    this._bandcapEl = root.querySelector("#bandcap");
    this._rowsEl = root.querySelector("#rows");
    this._reasonsEl = root.querySelector("#reasons");
    this._errorEl = root.querySelector("#error");

    this._dayBtns = [];
    for (let d = 0; d < DAY_KEYS.length; d++) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "daybtn";
      const label = document.createElement("span");
      label.textContent = this._dayLabel(d);
      const count = document.createElement("span");
      count.className = "dcount";
      btn.appendChild(label);
      btn.appendChild(count);
      btn.title = this._dayFull(d);
      btn.addEventListener("click", () => {
        this._day = d;
        this._dayChosen = true;
        this._render();
      });
      this._weekEl.appendChild(btn);
      this._dayBtns.push({ btn, count });
    }

    this._rows = [];
    for (let n = 1; n <= MAX_WINDOWS; n++) {
      this._rows.push(this._makeRow(this._rowsEl, n));
    }

    const autoEcoEl = root.querySelector("#auto_eco");
    autoEcoEl.addEventListener("click", () =>
      this._callService("button", "press", {
        entity_id: this._entity("button", "auto_eco_mode"),
      }).catch((err) => this._showError(err))
    );
    const manualEl = root.querySelector("#manual");
    manualEl.addEventListener("click", () =>
      this._callService("button", "press", {
        entity_id: this._entity("button", "manual_override"),
      }).catch((err) => this._showError(err))
    );

    root.querySelector("#read").addEventListener("click", () => this._read());
    root.querySelector("#calc").addEventListener("click", () => this._calc());
    root.querySelector("#apply").addEventListener("click", () => this._apply());

    this._built = true;
  }

  _makeRow(host, n) {
    const index = n - 1;
    const row = document.createElement("div");
    row.className = "wrow";
    row.innerHTML = `
      <div class="wmeta">
        <span class="wnum">${n}</span>
        <button class="mode" type="button"></button>
        <input class="pct" type="number" min="1" max="100" step="5">
        <span class="pcpct">%</span>
        <input class="start" type="time" step="900">
        <span class="dash">&ndash;</span>
        <input class="stop" type="time" step="900">
        <button class="daysbtn" type="button"></button>
        <span class="spacer"></span>
        <span class="wtag"></span>
        <button class="toggle" type="button">${this._t("active")}</button>
      </div>
      <div class="wtrack"><div class="empty"></div></div>
      <div class="wdays"></div>
    `;
    const refs = {
      row,
      index,
      modeBtn: row.querySelector(".mode"),
      pctInput: row.querySelector(".pct"),
      startInput: row.querySelector(".start"),
      stopInput: row.querySelector(".stop"),
      daysBtn: row.querySelector(".daysbtn"),
      tag: row.querySelector(".wtag"),
      toggleBtn: row.querySelector(".toggle"),
      track: row.querySelector(".wtrack"),
      empty: row.querySelector(".empty"),
      days: row.querySelector(".wdays"),
      chips: {},
    };

    refs.modeBtn.addEventListener("click", () => this._toggleMode(index));
    refs.pctInput.addEventListener("change", () => this._setPct(index));
    refs.startInput.addEventListener("change", () => this._setStart(index));
    refs.stopInput.addEventListener("change", () => this._setStop(index));
    refs.daysBtn.addEventListener("click", () => refs.days.classList.toggle("show"));
    refs.toggleBtn.addEventListener("click", () => this._toggleActive(index));
    refs.track.addEventListener("pointerdown", (event) => this._startDrag(event, index));

    DAY_KEYS.forEach((key, dayIndex) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = this._dayLabel(dayIndex);
      chip.title = this._dayFull(dayIndex);
      chip.addEventListener("click", () => this._toggleDay(index, key));
      refs.days.appendChild(chip);
      refs.chips[key] = chip;
    });

    host.appendChild(row);
    return refs;
  }

  // -- data --------------------------------------------------------------

  _readWindows() {
    this._segs = [];
    for (let n = 1; n <= MAX_WINDOWS; n++) {
      this._segs.push(this._readWindow(n));
    }
  }

  _readWindow(n) {
    const startState = this._state(this._entity("time", `window_${n}_start`));
    const stopState = this._state(this._entity("time", `window_${n}_stop`));
    const powerState = this._state(this._entity("number", `window_${n}_power`));
    const daysState = this._state(this._entity("text", `window_${n}_days`));

    const seg = {
      n,
      enabled: false,
      mode: "charge",
      pct: 100,
      days: new Set(),
      startHM: { h: 0, m: 0 },
      stopHM: { h: 1, m: 0 },
    };
    if (!startState || !stopState || !powerState) return seg;

    seg.startHM = parseHM(startState.state);
    seg.stopHM = parseHM(stopState.state);
    if (minutesOf(seg.stopHM) <= minutesOf(seg.startHM)) {
      seg.stopHM = timeOf(clamp(minutesOf(seg.startHM) + this._slot, 0, 1439));
    }
    seg.days = expandDays(daysState ? daysState.state : "all");

    const signed = Number(powerState.state) || 0;
    if (signed < 0) {
      seg.mode = "charge";
      seg.pct = clamp(Math.round(-signed), 1, 100);
      seg.enabled = true;
    } else if (signed > 0) {
      seg.mode = "discharge";
      seg.pct = clamp(Math.round(signed), 1, 100);
      seg.enabled = true;
    }
    return seg;
  }

  _detectPrices() {
    const state = this._priceEntity ? this._state(this._priceEntity) : undefined;
    if (!state) return [];
    const attributes = state.attributes || {};
    let raw = [];
    if (Array.isArray(attributes.prices) && attributes.prices.length) {
      raw = attributes.prices;
    } else {
      raw = []
        .concat(attributes.prices_today || [])
        .concat(attributes.prices_tomorrow || [])
        .concat(attributes.raw_today || [])
        .concat(attributes.raw_tomorrow || []);
    }
    const slots = [];
    for (const item of raw) {
      if (!item) continue;
      const rawTime = item.time !== undefined ? item.time : item.start;
      const rawPrice = item.price !== undefined ? item.price : item.value;
      if (rawTime === undefined || rawPrice === undefined) continue;
      const moment = rawTime instanceof Date ? rawTime : new Date(rawTime);
      const price = Number(rawPrice);
      if (Number.isNaN(moment.getTime()) || Number.isNaN(price)) continue;
      slots.push({ t: moment, price });
    }
    slots.sort((a, b) => a.t - b.t);
    return slots;
  }

  // Windows that are on, and run on the selected day.
  _activeDayIndices() {
    const key = this._selectedKey();
    const indices = [];
    for (let i = 0; i < MAX_WINDOWS; i++) {
      const seg = this._segs[i];
      if (seg.enabled && seg.days.has(key)) indices.push(i);
    }
    return indices;
  }

  _isExpired(seg) {
    return (
      this._isToday() &&
      seg.enabled &&
      seg.days.has(this._selectedKey()) &&
      minutesOf(seg.stopHM) <= this._nowMinutes()
    );
  }

  _computeOrder() {
    const key = this._selectedKey();
    const active = [];
    const expired = [];
    const other = [];
    const off = [];
    for (let i = 0; i < MAX_WINDOWS; i++) {
      const seg = this._segs[i];
      if (!seg.enabled) {
        off.push(i);
        continue;
      }
      if (!seg.days.has(key)) {
        other.push(i);
        continue;
      }
      if (this._isExpired(seg)) expired.push(i);
      else active.push(i);
    }
    const byStart = (a, b) =>
      minutesOf(this._segs[a].startHM) - minutesOf(this._segs[b].startHM);
    active.sort(byStart);
    expired.sort(byStart);
    this._order = [...active, ...expired, ...other, ...off];
  }

  // -- rendering ---------------------------------------------------------

  _render() {
    if (!this._segs.length) return;
    this._computeOrder();
    this._renderWeek();
    this._renderAxis();
    this._renderPrices();
    this._renderBand();
    for (let i = 0; i < MAX_WINDOWS; i++) this._renderRow(i);
    this._reorderRows();
    this._renderHint();
    this._renderReasons();
  }

  _renderWeek() {
    for (let d = 0; d < DAY_KEYS.length; d++) {
      const { btn, count } = this._dayBtns[d];
      let n = 0;
      for (const seg of this._segs) {
        if (seg.enabled && seg.days.has(DAY_KEYS[d])) n++;
      }
      count.textContent = n ? String(n) : "\u2013";
      btn.classList.toggle("on", d === this._day);
      btn.classList.toggle("today", d === this._todayIdx);
    }
  }

  _renderHint() {
    const label = `${this._dayFull(this._day)}${
      this._isToday() ? this._t("todaySuffix") : ""
    }`;
    this._hintEl.className = "hint";
    this._hintEl.textContent = `${label} \u00b7 ${this._t("hint")}`;
  }

  _renderAxis() {
    const host = this._rulerEl;
    host.innerHTML = "";
    for (let h = 0; h <= 24; h++) {
      const left = (h / 24) * 100;
      const tick = document.createElement("div");
      tick.className = `tick${h % 6 === 0 ? " major" : ""}`;
      tick.style.left = `${left}%`;
      host.appendChild(tick);
      if (h < 24 && h % 2 === 0) {
        const label = document.createElement("div");
        label.className = "tlabel";
        label.style.left = `${left}%`;
        label.textContent = pad(h);
        host.appendChild(label);
      }
    }
  }

  _renderPrices() {
    const host = this._pricesEl;
    const plot = this._plotEl;
    const axis = this._axisEl;
    const dayStart = this._dayStart();
    const next = addDays(dayStart, 1);
    const visible = (this._priceSlots || []).filter(
      (slot) => slot.t >= dayStart && slot.t < next
    );
    if (!visible.length) {
      host.style.display = "none";
      if (this._cardEl) this._cardEl.classList.remove("with-axis");
      return;
    }
    host.style.display = "grid";
    if (this._cardEl) this._cardEl.classList.add("with-axis");
    plot.innerHTML = "";
    axis.innerHTML = "";

    // Absolute scale in euros, anchored on 0 so the left axis reads in whole
    // cents (5, 10, 15, ... and -5 when prices go negative).
    const prices = visible.map((slot) => slot.price);
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    const step = 0.05;
    const axisLow = Math.min(0, Math.floor(low / step + 1e-9) * step);
    let axisHigh = Math.ceil(high / step - 1e-9) * step;
    if (axisHigh <= axisLow) axisHigh = axisLow + step;
    const range = axisHigh - axisLow;
    const yPct = (value) => ((value - axisLow) / range) * 100;

    for (
      let cents = Math.round(axisLow * 100);
      cents <= Math.round(axisHigh * 100);
      cents += 5
    ) {
      const bottom = yPct(cents / 100);
      const label = document.createElement("div");
      label.className = "ylab";
      label.style.bottom = `${bottom}%`;
      label.textContent = `${cents}\u00a2`;
      axis.appendChild(label);

      const grid = document.createElement("div");
      grid.className = `grid${cents === 0 ? " zero" : ""}`;
      grid.style.bottom = `${bottom}%`;
      plot.appendChild(grid);
    }

    for (const slot of visible) {
      const minutes = slot.t.getHours() * 60 + slot.t.getMinutes();
      const ratio = range > 0 ? (slot.price - axisLow) / range : 0.5;
      const bar = document.createElement("div");
      bar.className = "pbar";
      bar.style.left = `${(minutes / DAY_MINUTES) * 100}%`;
      bar.style.width = `${Math.max(0.2, (this._slot / DAY_MINUTES) * 100)}%`;
      bar.style.height = `${Math.max(1, yPct(slot.price))}%`;
      bar.style.background = `hsl(${(1 - ratio) * 120}, 62%, 45%)`;
      bar.title = `${hm(slot.t)} \u00b7 ${slot.price.toFixed(3)}`;
      plot.appendChild(bar);
    }
  }

  _nowModeText() {
    if (!this._isToday()) return "\u2013";
    const nowMin = this._nowMinutes();
    for (const i of this._activeDayIndices()) {
      const seg = this._segs[i];
      if (minutesOf(seg.startHM) <= nowMin && nowMin < minutesOf(seg.stopHM)) {
        return `${this._t("windowN", i + 1)} ${
          seg.mode === "charge" ? this._t("charge") : this._t("discharge")
        }`;
      }
    }
    return this._t("generalMode");
  }

  _renderBand() {
    const host = this._bandEl;
    host.innerHTML = "";
    for (const i of this._activeDayIndices()) {
      const seg = this._segs[i];
      const from = minutesOf(seg.startHM);
      const to = minutesOf(seg.stopHM);
      const el = document.createElement("div");
      el.className = `seg ${seg.mode}`;
      el.style.left = `${(from / DAY_MINUTES) * 100}%`;
      el.style.width = `${Math.max(0.4, ((to - from) / DAY_MINUTES) * 100)}%`;
      el.title =
        `${i + 1}: ${
          seg.mode === "charge" ? this._t("charge") : this._t("discharge")
        } ` + `${fmtHM(seg.startHM)}\u2013${fmtHM(seg.stopHM)}`;
      host.appendChild(el);
    }
    if (this._isToday()) {
      const now = document.createElement("div");
      now.className = "now";
      now.style.left = `${(this._nowMinutes() / DAY_MINUTES) * 100}%`;
      host.appendChild(now);
    }
    this._bandcapEl.innerHTML =
      `<span>${escapeHtml(this._t("greyGeneral"))}</span>` +
      `<span class="nowmode">${escapeHtml(
        this._t("nowPrefix") + this._nowModeText()
      )}</span>`;
  }

  _renderRow(i) {
    const r = this._rows[i];
    const seg = this._segs[i];
    const focused = this.shadowRoot.activeElement;
    const applies = seg.days.has(this._selectedKey());
    const expired = this._isExpired(seg);

    r.row.classList.toggle("off", !seg.enabled);
    r.row.classList.toggle("dim", seg.enabled && !applies);
    r.row.classList.toggle("expired", expired);

    r.modeBtn.textContent =
      seg.mode === "charge" ? this._t("charge") : this._t("discharge");
    r.modeBtn.className = `mode ${seg.mode}`;
    r.modeBtn.disabled = !seg.enabled;

    if (focused !== r.pctInput) r.pctInput.value = String(seg.pct);
    if (focused !== r.startInput) r.startInput.value = fmtHM(seg.startHM);
    if (focused !== r.stopInput) r.stopInput.value = fmtHM(seg.stopHM);
    r.pctInput.disabled = !seg.enabled;
    r.startInput.disabled = !seg.enabled;
    r.stopInput.disabled = !seg.enabled;

    r.daysBtn.textContent = `${this._daysToText(seg.days)} \u25be`;

    r.tag.className = "wtag";
    if (!seg.enabled) {
      r.tag.textContent = this._t("inactive");
    } else if (expired) {
      r.tag.textContent = this._t("expired");
      r.tag.classList.add("expired");
    } else if (!applies) {
      r.tag.textContent = this._t("notOn", this._dayLabel(this._day));
    } else {
      r.tag.textContent = "";
    }

    r.toggleBtn.textContent = seg.enabled ? this._t("active") : this._t("activate");
    r.toggleBtn.classList.toggle("on", seg.enabled);

    for (const key of DAY_KEYS) {
      r.chips[key].classList.toggle("on", seg.days.has(key));
    }
    if (!seg.enabled) r.days.classList.remove("show");

    this._paintTrack(i);
  }

  _paintTrack(i) {
    const r = this._rows[i];
    const seg = this._segs[i];
    const applies = seg.days.has(this._selectedKey());
    const expired = this._isExpired(seg);
    let bar = r.track.querySelector(".bar");

    if (!seg.enabled || !applies) {
      if (bar) bar.remove();
      r.empty.textContent = !seg.enabled
        ? this._t("inactive")
        : this._t("notActiveOn", this._dayLabel(this._day));
      r.empty.style.display = "flex";
      return;
    }

    const from = minutesOf(seg.startHM);
    const to = minutesOf(seg.stopHM);
    if (!bar) {
      bar = document.createElement("div");
      bar.innerHTML =
        '<span class="handle left"></span>' +
        '<span class="blabel"></span>' +
        '<span class="handle right"></span>';
      r.track.appendChild(bar);
    }
    bar.className = `bar ${seg.mode}${expired ? " expired" : ""}`;
    bar.style.left = `${(from / DAY_MINUTES) * 100}%`;
    bar.style.width = `${Math.max(0.5, ((to - from) / DAY_MINUTES) * 100)}%`;
    bar.querySelector(".blabel").textContent =
      `${fmtHM(seg.startHM)}\u2013${fmtHM(seg.stopHM)}`;
    r.empty.style.display = "none";
  }

  _reorderRows() {
    const key = this._order.join(",");
    if (key === this._orderKey) return;
    this._orderKey = key;
    if (this._drag) return;
    for (const i of this._order) this._rowsEl.appendChild(this._rows[i].row);
  }

  _renderReasons() {
    const el = this._reasonsEl;
    if (!el) return;
    if (!this._reasons.length && !this._notes.length) {
      el.className = "reasons";
      el.innerHTML = "";
      return;
    }
    el.className = "reasons show";
    const notes = this._notes
      .map((note) => `<div class="note">${escapeHtml(note)}</div>`)
      .join("");
    const items = this._reasons
      .map((reason) => {
        const mode = reason.mode === "discharge" ? "discharge" : "charge";
        const label =
          reason.window > 0
            ? this._t("windowPill", reason.window)
            : this._t("planPill");
        return `<li><span class="pill ${mode}">${label}</span><span class="why">${escapeHtml(
          reason.text || ""
        )}</span></li>`;
      })
      .join("");
    el.innerHTML = `
      <h4>${this._t("whyTimes")}</h4>
      ${notes}
      ${items ? `<ul>${items}</ul>` : ""}
    `;
  }

  _renderModes() {
    const autoEl = this.shadowRoot.querySelector("#auto_eco");
    const manualEl = this.shadowRoot.querySelector("#manual");
    if (!autoEl || !manualEl) return;
    const eco = this._state(this._entity("switch", "eco_mode"));
    const auto = this._state(this._entity("switch", "auto_herberekening"));
    const ecoOn = !!eco && eco.state === "on";
    const autoOn = !!auto && auto.state === "on";
    // "Auto/ECO Mode" is active only when the inverter runs ECO *and* the
    // re-check is on; "Manual override" is active when the re-check is paused.
    autoEl.classList.toggle("active", ecoOn && autoOn);
    manualEl.classList.toggle("active", !autoOn);
    autoEl.title = this._t("ecoTitle");
    manualEl.title = this._t("manualTitle");
  }

  // -- editing -----------------------------------------------------------

  _toggleActive(index) {
    const seg = this._segs[index];
    if (!seg) return;
    seg.enabled = !seg.enabled;
    if (seg.enabled && !seg.days.size) {
      seg.days = new Set([this._selectedKey()]);
    }
    this._dirty = true;
    this._render();
  }

  _toggleMode(index) {
    const seg = this._segs[index];
    if (!seg || !seg.enabled) return;
    seg.mode = seg.mode === "charge" ? "discharge" : "charge";
    this._dirty = true;
    this._render();
  }

  _setPct(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    seg.pct = clamp(Math.round(Number(row.pctInput.value)) || 0, 1, 100);
    this._dirty = true;
    this._renderRow(index);
  }

  _setStart(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const value = minutesOf(parseHM(row.startInput.value));
    const stop = minutesOf(seg.stopHM);
    const bounds = this._dragBounds(index);
    seg.startHM = timeOf(clamp(value, bounds.lo, Math.min(stop - this._slot, bounds.hi)));
    this._dirty = true;
    this._renderRow(index);
    this._renderBand();
  }

  _setStop(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const value = minutesOf(parseHM(row.stopInput.value));
    const start = minutesOf(seg.startHM);
    const bounds = this._dragBounds(index);
    seg.stopHM = timeOf(clamp(value, Math.max(start + this._slot, bounds.lo), bounds.hi));
    this._dirty = true;
    this._renderRow(index);
    this._renderBand();
  }

  _toggleDay(index, key) {
    const seg = this._segs[index];
    if (!seg) return;
    if (seg.days.has(key)) seg.days.delete(key);
    else seg.days.add(key);
    this._dirty = true;
    this._render();
  }

  // -- dragging ----------------------------------------------------------

  // How far a handle may move before it hits the neighbouring window on the
  // selected day, so two windows can never overlap.
  _dragBounds(index) {
    const key = this._selectedKey();
    const start = minutesOf(this._segs[index].startHM);
    const stop = minutesOf(this._segs[index].stopHM);
    let lo = 0;
    let hi = DAY_MINUTES;
    for (let j = 0; j < MAX_WINDOWS; j++) {
      if (j === index) continue;
      const other = this._segs[j];
      if (!other.enabled || !other.days.has(key)) continue;
      const otherStart = minutesOf(other.startHM);
      const otherStop = minutesOf(other.stopHM);
      if (otherStop <= start) lo = Math.max(lo, otherStop);
      if (otherStart >= stop) hi = Math.min(hi, otherStart);
    }
    if (hi < start + this._slot) hi = start + this._slot;
    if (lo > stop - this._slot) lo = stop - this._slot;
    return { lo, hi };
  }

  _startDrag(event, index) {
    const handle = event.target.closest(".handle");
    if (!handle) return;
    const seg = this._segs[index];
    if (!seg || !seg.enabled || !seg.days.has(this._selectedKey())) return;
    event.preventDefault();
    this._drag = {
      index,
      which: handle.classList.contains("left") ? "left" : "right",
    };
    window.addEventListener("pointermove", this._onPointerMove);
    window.addEventListener("pointerup", this._onPointerUp);
    window.addEventListener("pointercancel", this._onPointerUp);
  }

  _onPointerMove(event) {
    if (!this._drag) return;
    const { index, which } = this._drag;
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const rect = row.track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const raw = Math.round((ratio * DAY_MINUTES) / this._slot) * this._slot;
    const bounds = this._dragBounds(index);
    const start = minutesOf(seg.startHM);
    const stop = minutesOf(seg.stopHM);
    if (which === "left") {
      seg.startHM = timeOf(clamp(raw, bounds.lo, stop - this._slot));
    } else {
      seg.stopHM = timeOf(clamp(raw, start + this._slot, bounds.hi));
    }
    this._dirty = true;
    this._renderRow(index);
    this._renderBand();
  }

  _onPointerUp() {
    this._drag = null;
    window.removeEventListener("pointermove", this._onPointerMove);
    window.removeEventListener("pointerup", this._onPointerUp);
    window.removeEventListener("pointercancel", this._onPointerUp);
  }

  // -- actions -----------------------------------------------------------

  // Always four windows, in the inverter's own order, so a window keeps its
  // number even when the rows are reordered on screen.
  _collectWindows() {
    const windows = [];
    for (let i = 0; i < MAX_WINDOWS; i++) {
      const seg = this._segs[i];
      const days = DAY_KEYS.filter((key) => seg.days.has(key));
      if (seg.enabled && days.length) {
        windows.push({
          start: `${fmtHM(seg.startHM)}:00`,
          stop: `${fmtHM(seg.stopHM)}:00`,
          power: seg.mode === "charge" ? -seg.pct : seg.pct,
          days: days.join(","),
        });
      } else {
        windows.push({ start: "00:00:00", stop: "00:00:00", power: 0, days: "none" });
      }
    }
    return windows;
  }

  _read() {
    this._dirty = false;
    this._callService("button", "press", {
      entity_id: this._entity("button", "read_from_inverter"),
    }).catch((err) => this._showError(err));
  }

  _calc() {
    this._callServiceWithResponse("goodwe_tou", "calculate_cheapest_hours", {})
      .then((response) => {
        const windows = (response && response.windows) || [];
        this._reasons = (response && response.reasons) || [];
        this._notes = (response && response.notes) || [];
        this._summary = (response && response.summary) || "";
        this._applyPlan(windows);
        this._day = this._todayIdx;
        this._dayChosen = false;
        this._orderKey = "";
        this._dirty = true;
        this._render();
      })
      .catch((err) => this._showError(err));
  }

  _applyPlan(windows) {
    for (const seg of this._segs) {
      seg.enabled = false;
      seg.mode = "charge";
      seg.pct = 100;
      seg.days = new Set();
    }
    windows.slice(0, MAX_WINDOWS).forEach((window, index) => {
      const seg = this._segs[index];
      if (!seg) return;
      seg.startHM = parseHM(window.start);
      seg.stopHM = parseHM(window.stop);
      const power = Number(window.power) || 0;
      if (power < 0) {
        seg.mode = "charge";
        seg.pct = clamp(Math.round(-power), 1, 100);
        seg.enabled = true;
      } else if (power > 0) {
        seg.mode = "discharge";
        seg.pct = clamp(Math.round(power), 1, 100);
        seg.enabled = true;
      }
      seg.days = expandDays(window.days || "all");
    });
  }

  _apply() {
    const windows = this._collectWindows();
    this._callService("goodwe_tou", "set_windows", { windows })
      .then(() => {
        this._dirty = false;
      })
      .catch((err) => this._showError(err));
  }

  _callService(domain, service, data) {
    if (!this._hass) return Promise.resolve();
    return this._hass.callService(domain, service, data);
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

  _showError(err) {
    if (!this._errorEl) return;
    this._errorEl.textContent = (err && err.message) || this._t("actionFailed");
    clearTimeout(this._errorTimer);
    this._errorTimer = setTimeout(() => {
      this._errorEl.textContent = "";
    }, 8000);
  }
}

if (!customElements.get("goodwe-tou-planner-card")) {
  customElements.define("goodwe-tou-planner-card", GoodweTouPlannerCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goodwe-tou-planner-card",
  name: "GoodWe Time-of-Use planner",
  description:
    "Plan the Time-of-Use windows on a 24-hour timeline with one slider per window, a Laden/Ontladen button, and a clear view of the inverter's general mode.",
  preview: false,
});

console.info(
  `%c GOODWE-TOU-PLANNER %c v${CARD_VERSION} `,
  "background:#2e7d32;color:#fff;font-weight:bold",
  "background:#333;color:#fff"
);
