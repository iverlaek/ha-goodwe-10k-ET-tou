/*
 * goodwe-tou-planner-card
 *
 * A planner for the GoodWe Time-of-Use integration.
 *
 * Two views, chosen with the buttons at the top:
 *   - "Vandaag + morgen" (default): a timeline that starts at the current
 *     quarter hour and ends at midnight at the end of tomorrow. Every window
 *     that is active today and/or tomorrow is drawn as a draggable bar on it.
 *   - "Hele week": a calendar list of the seven days, each showing the windows
 *     that run on that day.
 *
 * Every window is edited in the same row below the timeline:
 *   - an Actief / Inactief button (an inactive window is greyed out);
 *   - a power field (0-100 %) that lives in one of two labelled columns:
 *     "Laden" on the left and "Ontladen" on the right. The field is shown in
 *     the column that matches the window's direction and the empty cell in the
 *     other column is clickable: clicking it moves the field there. A window
 *     therefore only ever has one direction, because the inverter stores a
 *     single signed power per window;
 *   - a start and a stop time;
 *   - seven day chips.
 *
 * The user decides how many of the (at most four) inverter windows are used.
 *
 * Buttons at the bottom:
 *   - "Lezen van omvormer" pulls the live schedule from the inverter;
 *   - "Optimale prijzen berekenen" asks the integration to plan the cheapest
 *     hours and fills the rows with the result, without writing anything;
 *   - "Toepassen" writes the whole plan through goodwe_tou.set_windows.
 *
 * Design rules (see the integration README):
 *   - the timeline sits at the very top and only covers the period from now
 *     until midnight tomorrow;
 *   - tomorrow is greyed out, with a warning triangle, until tomorrow's prices
 *     are known (usually ~15:00); the bar stays visible but grey;
 *   - overlapping windows are impossible: dragging a handle stops at the
 *     neighbouring window;
 *   - a window lives inside a single day; it never crosses midnight.
 *
 * Configure with:
 *
 *   type: custom:goodwe-tou-planner-card
 *   title: Laadplanning                                  # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                           # optional
 *   price_entity: sensor.average_electricity_price       # optional
 *   windows: 4                                           # optional, 1..4
 *   view: now                                            # optional, "now" | "week"
 *   slot_minutes: 15                                     # optional, timeline granularity
 */

const CARD_VERSION = "0.6.3";
const PREFIX_DEFAULT = "goodwe_time_of_use";
const MAX_WINDOWS = 4;
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABELS = ["ma", "di", "wo", "do", "vr", "za", "zo"];
const DAY_FULL = [
  "Maandag",
  "Dinsdag",
  "Woensdag",
  "Donderdag",
  "Vrijdag",
  "Zaterdag",
  "Zondag",
];
const MIN = 60000;

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
const addMinutes = (date, minutes) => new Date(date.getTime() + minutes * MIN);
const addHours = (date, hours) => new Date(date.getTime() + hours * 3600000);
const floorSlot = (date, slot) => {
  const copy = new Date(date);
  copy.setSeconds(0, 0);
  const minutes = copy.getMinutes();
  copy.setMinutes(minutes - (minutes % slot));
  return copy;
};
const weekdayKey = (date) => DAY_KEYS[(date.getDay() + 6) % 7];
const hm = (date) =>
  `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const fmtHM = (time) => `${pad(time.h)}:${pad(time.m)}`;
const parseHM = (text) => {
  const parts = String(text || "0:0").split(":");
  return {
    h: clamp(Number(parts[0]) || 0, 0, 23),
    m: clamp(Number(parts[1]) || 0, 0, 59),
  };
};
const withHM = (day, time) => {
  const copy = new Date(day);
  copy.setHours(time.h, time.m, 0, 0);
  return copy;
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
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
  .title { font-size: 1.15rem; font-weight: 600; color: var(--primary-text-color); }
  .eco { display: flex; align-items: center; gap: 8px; font-size: .85rem; color: var(--secondary-text-color); }
  .switch { position: relative; width: 44px; height: 24px; flex: none; }
  .switch input { opacity: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; }
  .switch .track { position: absolute; inset: 0; border-radius: 12px; background: var(--disabled-color, #9e9e9e); transition: background .15s; pointer-events: none; }
  .switch .track::after { content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: transform .15s; }
  .switch input:checked + .track { background: var(--primary-color); }
  .switch input:checked + .track::after { transform: translateX(20px); }

  .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 10px 0 6px; }
  .seg { display: flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg button { font: inherit; font-size: .8rem; padding: 6px 12px; border: none; background: transparent; color: var(--secondary-text-color); cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--divider-color); }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .count { display: flex; align-items: center; gap: 6px; font-size: .8rem; color: var(--secondary-text-color); }
  .count .seg button { min-width: 30px; padding: 6px 0; }

  .hint { font-size: .78rem; color: var(--secondary-text-color); margin-bottom: 10px; min-height: 1.1em; }
  .hint.warn { color: var(--warning-color, #ff9800); }

  .axis { position: relative; margin-bottom: 4px; }
  .axis .dayrow { position: relative; height: 20px; }
  .axis .dayband { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: .74rem; font-weight: 600; color: var(--secondary-text-color); background: var(--secondary-background-color); border-radius: 6px; overflow: hidden; }
  .axis .dayband.grey { background: var(--disabled-color, #9e9e9e); color: var(--text-primary-color, #fff); opacity: .5; }
  .axis .warnmark { position: absolute; top: 50%; transform: translate(-50%, -50%); font-size: 1rem; line-height: 1; color: var(--warning-color, #ff9800); text-shadow: 0 0 3px var(--card-background-color, #fff); cursor: help; }
  .axis .tickrow { position: relative; height: 16px; margin-top: 2px; }
  .axis .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--divider-color); opacity: .7; }
  .axis .tick.major { width: 2px; opacity: 1; }
  .axis .ticklabel { position: absolute; top: 0; transform: translateX(-50%); font-size: .68rem; color: var(--secondary-text-color); }

  .prices { position: relative; height: 44px; margin: 2px 0 8px; border-radius: 8px; background: var(--secondary-background-color); overflow: hidden; }
  .pbar { position: absolute; bottom: 0; border-radius: 2px 2px 0 0; opacity: .85; }
  .prices .pgrey { position: absolute; top: 0; bottom: 0; background: repeating-linear-gradient(45deg, rgba(128,128,128,.22) 0 6px, transparent 6px 12px); pointer-events: none; }

  .row { padding: 10px 0; border-top: 1px solid var(--divider-color); transition: opacity .15s; }
  .row:first-child { border-top: none; }
  .row.off { opacity: .5; }
  .row.collapsed { padding: 5px 0; opacity: .6; }
  .row.collapsed .rowtop { margin-bottom: 0; }
  .row.collapsed .track,
  .row.collapsed .times,
  .row.collapsed .cols,
  .row.collapsed .days { display: none; }
  .rowtop { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .wlabel { font-weight: 600; font-size: .88rem; color: var(--primary-text-color); }
  .badge { font-size: .78rem; font-variant-numeric: tabular-nums; padding: 2px 9px; border-radius: 10px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .badge.charge { background: #c62828; color: #fff; }
  .badge.discharge { background: #2e7d32; color: #fff; }
  .spacer { flex: 1; }
  button.active { font: inherit; font-size: .78rem; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--secondary-text-color); cursor: pointer; }
  button.active.on { background: #2e7d32; border-color: #2e7d32; color: #fff; }

  .row .track { position: relative; height: 34px; border-radius: 8px; background: var(--secondary-background-color); overflow: hidden; margin-bottom: 6px; }
  .row .track .grey { position: absolute; top: 0; bottom: 0; background: repeating-linear-gradient(45deg, rgba(128,128,128,.16) 0 6px, transparent 6px 12px); pointer-events: none; }
  .row .track .divider { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--divider-color); opacity: .8; pointer-events: none; }
  .row .track .bars { position: absolute; inset: 0; }
  .bar { position: absolute; top: 5px; bottom: 5px; border-radius: 6px; background: var(--disabled-color, #9e9e9e); display: flex; align-items: center; justify-content: center; min-width: 6px; }
  .bar.charge { background: linear-gradient(180deg, #ef5350, #b71c1c); }
  .bar.discharge { background: linear-gradient(180deg, #66bb6a, #1b5e20); }
  .barlabel { font-size: .7rem; font-weight: 600; color: #fff; white-space: nowrap; pointer-events: none; text-shadow: 0 1px 2px rgba(0,0,0,.45); }
  .handle { position: absolute; top: 50%; width: 14px; height: 26px; transform: translateY(-50%); border-radius: 6px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.28); cursor: ew-resize; touch-action: none; }
  .handle.left { left: -6px; }
  .handle.right { right: -6px; }
  .row .track .empty { position: absolute; inset: 0; display: none; align-items: center; justify-content: center; font-size: .78rem; color: var(--secondary-text-color); }

  .times { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  input[type=time] { flex: 1; min-width: 0; background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 8px; padding: 5px 8px; font: inherit; }
  .dash { color: var(--secondary-text-color); }

  .colheads { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0 2px; font-size: .74rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
  .colheads .ch { text-align: center; padding: 5px 0; border-radius: 8px; }
  .colheads .ch.charge { color: #c62828; background: rgba(198, 40, 40, .12); }
  .colheads .ch.discharge { color: #2e7d32; background: rgba(46, 125, 50, .14); }

  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 6px; }
  .cell { min-width: 0; display: flex; align-items: center; }
  .cell .sld { display: none; flex: 1; align-items: center; gap: 8px; font-size: .8rem; color: var(--secondary-text-color); }
  .cell.active .sld { display: flex; }
  .cell .sld input[type=range] { flex: 1; min-width: 0; }
  .cell.charge .sld input[type=range] { accent-color: #c62828; }
  .cell.discharge .sld input[type=range] { accent-color: #2e7d32; }
  .cell .sld > b { width: 44px; text-align: right; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .cell .placeholder { display: flex; flex: 1; align-items: center; justify-content: center; height: 34px; border: 1px dashed var(--divider-color); border-radius: 8px; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .78rem; cursor: pointer; }
  .cell.active .placeholder { display: none; }
  .cell .placeholder:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .row.off .cell .placeholder { opacity: .55; }

  .days { display: flex; gap: 4px; margin-top: 8px; }
  .chip { flex: 1; padding: 5px 0; border-radius: 6px; border: 1px solid var(--divider-color); background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .72rem; cursor: pointer; }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  /* In "Vandaag + morgen" every window runs on today and tomorrow, so the day
     chips are only needed in the whole-week view. */
  ha-card:not(.view-week) .row .days { display: none; }

  .week { display: none; }
  .wday { padding: 8px 0; border-top: 1px solid var(--divider-color); }
  .wday:first-child { border-top: none; }
  .wdayname { font-size: .82rem; font-weight: 600; color: var(--primary-text-color); margin-bottom: 4px; }
  .witem { display: flex; align-items: center; gap: 10px; font-size: .8rem; padding: 4px 8px; border-radius: 6px; background: var(--secondary-background-color); margin-top: 4px; }
  .witem .wname { font-weight: 600; color: var(--primary-text-color); min-width: 66px; }
  .witem .wtime { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); }
  .witem .wpow { margin-left: auto; font-variant-numeric: tabular-nums; }
  .witem.charge .wpow { color: #c62828; }
  .witem.discharge .wpow { color: #2e7d32; }
  .wempty { font-size: .78rem; color: var(--secondary-text-color); padding: 2px 8px; }

  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .actions button { flex: 1 1 30%; padding: 10px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; cursor: pointer; }
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

  ha-card.view-week .axis,
  ha-card.view-week .prices,
  ha-card.view-week .row .track { display: none !important; }
  ha-card.view-week .week { display: block; }
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
    this._tomorrowKnown = true;
    this._slot = 15;
    this._count = MAX_WINDOWS;
    this._view = "now";
    this._reasons = [];
    this._notes = [];
    this._summary = "";
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._title = this._config.title || "Laadplanning";
    this._priceEntity = this._config.price_entity || null;
    this._slot =
      Number(this._config.slot_minutes) > 0 ? Number(this._config.slot_minutes) : 15;
    this._count = clamp(
      Number(this._config.windows) || MAX_WINDOWS,
      1,
      MAX_WINDOWS
    );
    this._view = this._config.view === "week" ? "week" : "now";
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._built) return;

    this._updateBounds();
    const priceState = this._priceEntity ? this._state(this._priceEntity) : undefined;
    const priceSig = priceState ? priceState.last_updated : null;
    if (priceSig !== this._priceSig) {
      this._priceSig = priceSig;
      const prices = this._detectPrices();
      this._priceSlots = prices.slots;
      this._tomorrowKnown = prices.tomorrowKnown;
    }
    if (!this._dirty) this._readWindows();
    this._renderEco();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 12;
  }

  _entity(domain, suffix) {
    return `${domain}.${this._prefix}_${suffix}`;
  }

  _state(entityId) {
    return this._hass ? this._hass.states[entityId] : undefined;
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
          <label class="eco">
            <span>ECO</span>
            <span class="switch">
              <input type="checkbox" id="eco">
              <span class="track"></span>
            </span>
          </label>
        </div>
        <div class="axis" id="axis"></div>
        <div class="prices" id="prices"></div>
        <div class="toolbar">
          <div class="seg" id="viewseg">
            <button data-view="now">Vandaag + morgen</button>
            <button data-view="week">Hele week (7 dagen)</button>
          </div>
          <div class="count" id="countseg"><span>Aantal vensters</span></div>
        </div>
        <div class="hint" id="hint"></div>
        <div class="colheads">
          <span class="ch charge">Laden</span>
          <span class="ch discharge">Ontladen</span>
        </div>
        <div class="rows" id="rows"></div>
        <div class="week" id="week"></div>
        <div class="actions">
          <button id="read">Lezen van omvormer</button>
          <button id="calc">Optimale prijzen berekenen</button>
          <button id="apply" class="primary">Toepassen</button>
        </div>
        <div class="reasons" id="reasons"></div>
        <div class="error" id="error"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${this._title} (Version ${CARD_VERSION})`;
    this._card = root.querySelector("ha-card");
    this._axisEl = root.querySelector("#axis");
    this._pricesEl = root.querySelector("#prices");
    this._weekEl = root.querySelector("#week");
    this._hintEl = root.querySelector("#hint");
    this._reasonsEl = root.querySelector("#reasons");
    this._errorEl = root.querySelector("#error");

    this._viewBtns = Array.from(root.querySelectorAll("#viewseg button"));
    for (const button of this._viewBtns) {
      button.addEventListener("click", () => {
        this._view = button.dataset.view;
        this._render();
      });
    }

    const countEl = root.querySelector("#countseg");
    const countSeg = document.createElement("div");
    countSeg.className = "seg";
    this._countBtns = [];
    for (let n = 1; n <= MAX_WINDOWS; n++) {
      const button = document.createElement("button");
      button.textContent = String(n);
      button.dataset.count = String(n);
      button.addEventListener("click", () => {
        this._count = n;
        this._render();
      });
      countSeg.appendChild(button);
      this._countBtns.push(button);
    }
    countEl.appendChild(countSeg);

    const rowsEl = root.querySelector("#rows");
    this._rows = [];
    for (let n = 1; n <= MAX_WINDOWS; n++) {
      this._rows.push(this._makeRow(rowsEl, n));
    }

    const ecoEl = root.querySelector("#eco");
    ecoEl.addEventListener("change", () =>
      this._callService("switch", ecoEl.checked ? "turn_on" : "turn_off", {
        entity_id: this._entity("switch", "eco_mode"),
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
    row.className = "row";
    row.innerHTML = `
      <div class="rowtop">
        <span class="wlabel">Venster ${n}</span>
        <span class="badge"></span>
        <span class="spacer"></span>
        <button class="active"></button>
      </div>
      <div class="track">
        <div class="grey"></div>
        <div class="divider"></div>
        <div class="bars"></div>
        <div class="empty"></div>
      </div>
      <div class="times">
        <input type="time" class="start">
        <span class="dash">&ndash;</span>
        <input type="time" class="stop">
      </div>
      <div class="cols">
        <div class="cell charge" data-dir="charge">
          <label class="sld charge"><input type="range" min="0" max="100" step="5" class="charge"><b class="chargeval">0%</b></label>
          <button class="placeholder" title="Klik om dit venster te laten laden">laden</button>
        </div>
        <div class="cell discharge" data-dir="discharge">
          <label class="sld discharge"><input type="range" min="0" max="100" step="5" class="discharge"><b class="dischargeval">0%</b></label>
          <button class="placeholder" title="Klik om dit venster te laten ontladen">ontladen</button>
        </div>
      </div>
      <div class="days"></div>
    `;
    const refs = {
      row,
      badge: row.querySelector(".badge"),
      activeBtn: row.querySelector(".active"),
      track: row.querySelector(".track"),
      grey: row.querySelector(".grey"),
      divider: row.querySelector(".divider"),
      bars: row.querySelector(".bars"),
      empty: row.querySelector(".empty"),
      startInput: row.querySelector("input.start"),
      stopInput: row.querySelector("input.stop"),
      chargeCell: row.querySelector(".cell.charge"),
      dischargeCell: row.querySelector(".cell.discharge"),
      chargeInput: row.querySelector("input.charge"),
      dischargeInput: row.querySelector("input.discharge"),
      chargeVal: row.querySelector(".chargeval"),
      dischargeVal: row.querySelector(".dischargeval"),
      chips: {},
    };

    refs.activeBtn.addEventListener("click", () => this._toggleActive(index));
    refs.startInput.addEventListener("change", () => this._setStart(index));
    refs.stopInput.addEventListener("change", () => this._setStop(index));
    refs.chargeInput.addEventListener("input", () => this._setCharge(index));
    refs.dischargeInput.addEventListener("input", () => this._setDischarge(index));
    for (const cell of [refs.chargeCell, refs.dischargeCell]) {
      cell
        .querySelector(".placeholder")
        .addEventListener("click", () => this._setMode(index, cell.dataset.dir));
    }
    refs.bars.addEventListener("pointerdown", (event) => this._startDrag(event, index));

    const daysEl = row.querySelector(".days");
    DAY_KEYS.forEach((key, dayIndex) => {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = DAY_LABELS[dayIndex];
      chip.title = DAY_FULL[dayIndex];
      chip.addEventListener("click", () => this._toggleDay(index, key));
      daysEl.appendChild(chip);
      refs.chips[key] = chip;
    });

    host.appendChild(row);
    return refs;
  }

  // -- time bounds / data ------------------------------------------------

  _updateBounds() {
    const now = new Date();
    this._now = now;
    this._t0 = floorSlot(now, this._slot);
    this._todayDate = startOfDay(now);
    this._tomorrowDate = startOfDay(addDays(now, 1));
    this._tMid = this._tomorrowDate;
    this._t1 = startOfDay(addDays(now, 2));
  }

  // In "Vandaag + morgen" a window always runs on both today and tomorrow, so
  // the stored days are ignored and mapped to the real weekdays of those days.
  _effectiveDays(seg) {
    if (this._view === "now") {
      return new Set([weekdayKey(this._todayDate), weekdayKey(this._tomorrowDate)]);
    }
    return seg.days;
  }

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
      charge: 100,
      discharge: 0,
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
      seg.charge = clamp(Math.round(-signed), 1, 100);
      seg.discharge = 0;
      seg.enabled = true;
    } else if (signed > 0) {
      seg.discharge = clamp(Math.round(signed), 1, 100);
      seg.charge = 0;
      seg.enabled = true;
    } else {
      seg.enabled = false;
    }
    return seg;
  }

  _detectPrices() {
    const state = this._priceEntity ? this._state(this._priceEntity) : undefined;
    if (!state) return { slots: [], tomorrowKnown: true };
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
    const tomorrow = this._tMid || startOfDay(addDays(new Date(), 1));
    const tomorrowKnown = slots.some((slot) => slot.t >= tomorrow);
    return { slots, tomorrowKnown };
  }

  _mode(seg) {
    if (seg.charge > 0) return "charge";
    if (seg.discharge > 0) return "discharge";
    return "off";
  }

  _pct(moment) {
    return (
      ((moment.getTime() - this._t0.getTime()) /
        (this._t1.getTime() - this._t0.getTime())) *
      100
    );
  }

  // -- rendering ---------------------------------------------------------

  _render() {
    if (!this._segs.length) return;
    this._card.classList.toggle("view-week", this._view === "week");
    for (const button of this._viewBtns) {
      button.classList.toggle("on", button.dataset.view === this._view);
    }
    for (const button of this._countBtns) {
      button.classList.toggle("on", Number(button.dataset.count) === this._count);
    }

    if (this._view === "week") {
      this._hintEl.className = "hint";
      this._hintEl.textContent =
        "Hele week: elk venster loopt op de gekozen dagen (chips onderaan elk venster).";
    } else {
      const todayLabel = DAY_LABELS[(this._todayDate.getDay() + 6) % 7];
      const tomorrowLabel = DAY_LABELS[(this._tomorrowDate.getDay() + 6) % 7];
      const base =
        `Vandaag + morgen: elk venster geldt automatisch voor ` +
        `${todayLabel} en ${tomorrowLabel}.`;
      if (this._tomorrowKnown) {
        this._hintEl.className = "hint";
        this._hintEl.textContent = `${base} Prijzen t/m morgen bekend.`;
      } else {
        this._hintEl.className = "hint warn";
        this._hintEl.textContent =
          `${base} \u26a0 Prijzen van morgen zijn nog niet bekend ` +
          "(meestal rond 15:00); de tijdbalk is daar grijs.";
      }
    }

    if (this._view === "now") {
      this._renderAxis();
      this._renderPrices();
    }
    for (let i = 0; i < this._rows.length; i++) this._renderRow(i);
    if (this._view === "week") this._renderWeek();
    this._renderReasons();
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
          reason.window > 0 ? `Venster ${reason.window}` : "Plan";
        return `<li><span class="pill ${mode}">${label}</span><span class="why">${escapeHtml(
          reason.text || ""
        )}</span></li>`;
      })
      .join("");
    el.innerHTML = `
      <h4>Waarom deze tijden</h4>
      ${notes}
      ${items ? `<ul>${items}</ul>` : ""}
    `;
  }

  _renderRow(i) {
    const row = this._rows[i];
    const seg = this._segs[i];
    const mode = this._mode(seg);
    const active = this.shadowRoot.activeElement;

    row.row.style.display = i < this._count ? "" : "none";
    row.row.classList.toggle("off", !seg.enabled);
    row.row.classList.toggle("collapsed", !seg.enabled);

    row.badge.textContent = !seg.enabled
      ? "inactief"
      : mode === "charge"
        ? `${seg.charge}% laden`
        : mode === "discharge"
          ? `${seg.discharge}% ontladen`
          : "geen vermogen";
    row.badge.className = `badge ${seg.enabled && mode !== "off" ? mode : ""}`;

    row.activeBtn.textContent = seg.enabled ? "Actief" : "Activeren";
    row.activeBtn.classList.toggle("on", seg.enabled);

    const showCharge = seg.enabled && mode === "charge";
    const showDischarge = seg.enabled && mode === "discharge";
    row.chargeCell.classList.toggle("active", showCharge);
    row.dischargeCell.classList.toggle("active", showDischarge);

    if (active !== row.startInput) row.startInput.value = fmtHM(seg.startHM);
    if (active !== row.stopInput) row.stopInput.value = fmtHM(seg.stopHM);
    if (active !== row.chargeInput) row.chargeInput.value = String(seg.charge);
    if (active !== row.dischargeInput) row.dischargeInput.value = String(seg.discharge);
    row.chargeVal.textContent = `${seg.charge}%`;
    row.dischargeVal.textContent = `${seg.discharge}%`;

    const effectiveDays = this._effectiveDays(seg);
    for (const key of DAY_KEYS) {
      row.chips[key].classList.toggle("on", effectiveDays.has(key));
    }

    const mid = this._pct(this._tMid);
    row.divider.style.left = `${mid}%`;
    if (this._tomorrowKnown) {
      row.grey.style.display = "none";
    } else {
      row.grey.style.display = "block";
      row.grey.style.left = `${mid}%`;
      row.grey.style.width = `${100 - mid}%`;
    }

    this._renderBars(i);
  }

  _renderBars(i) {
    const row = this._rows[i];
    const seg = this._segs[i];
    row.bars.innerHTML = "";
    if (this._view !== "now") return;

    if (!seg.enabled) {
      row.empty.textContent = "niet actief";
      row.empty.style.display = "flex";
      return;
    }
    const mode = this._mode(seg);
    const effectiveDays = this._effectiveDays(seg);
    let drew = false;
    for (const [tag, dayDate] of [
      ["today", this._todayDate],
      ["tomorrow", this._tomorrowDate],
    ]) {
      if (!effectiveDays.has(weekdayKey(dayDate))) continue;
      const start = withHM(dayDate, seg.startHM);
      const stop = withHM(dayDate, seg.stopHM);
      const from = Math.max(start.getTime(), this._t0.getTime());
      const to = Math.min(stop.getTime(), this._t1.getTime());
      if (to <= from) continue;
      drew = true;
      const left = this._pct(new Date(from));
      const width = Math.max(0.5, this._pct(new Date(to)) - left);
      const bar = document.createElement("div");
      bar.className = `bar ${mode === "off" ? "" : mode}`;
      bar.style.left = `${left}%`;
      bar.style.width = `${width}%`;
      bar.innerHTML =
        `<span class="handle left" data-i="${i}" data-day="${tag}"></span>` +
        `<span class="barlabel">${fmtHM(seg.startHM)}&ndash;${fmtHM(seg.stopHM)}</span>` +
        `<span class="handle right" data-i="${i}" data-day="${tag}"></span>`;
      row.bars.appendChild(bar);
    }
    row.empty.textContent = "niet in deze periode";
    row.empty.style.display = drew ? "none" : "flex";
  }

  _renderWeek() {
    const host = this._weekEl;
    host.innerHTML = "";
    const todayKey = weekdayKey(this._todayDate);
    const tomorrowKey = weekdayKey(this._tomorrowDate);
    for (let d = 0; d < DAY_KEYS.length; d++) {
      const key = DAY_KEYS[d];
      const day = document.createElement("div");
      day.className = "wday";

      const head = document.createElement("div");
      head.className = "wdayname";
      let suffix = "";
      if (key === todayKey) suffix = " \u00b7 vandaag";
      else if (key === tomorrowKey) suffix = " \u00b7 morgen";
      head.textContent = DAY_FULL[d] + suffix;
      day.appendChild(head);

      let count = 0;
      for (let i = 0; i < this._count; i++) {
        const seg = this._segs[i];
        if (!seg.enabled || !this._effectiveDays(seg).has(key)) continue;
        const mode = this._mode(seg);
        if (mode === "off") continue;
        const item = document.createElement("div");
        item.className = `witem ${mode}`;
        item.innerHTML =
          `<span class="wname">Venster ${i + 1}</span>` +
          `<span class="wtime">${fmtHM(seg.startHM)}&ndash;${fmtHM(seg.stopHM)}</span>` +
          `<span class="wpow">${
            mode === "charge" ? `${seg.charge}% laden` : `${seg.discharge}% ontladen`
          }</span>`;
        day.appendChild(item);
        count++;
      }
      if (!count) {
        const empty = document.createElement("div");
        empty.className = "wempty";
        empty.textContent = "geen vensters";
        day.appendChild(empty);
      }
      host.appendChild(day);
    }
  }

  _renderEco() {
    const ecoEl = this.shadowRoot.querySelector("#eco");
    if (!ecoEl) return;
    if (this.shadowRoot.activeElement === ecoEl) return;
    const state = this._state(this._entity("switch", "eco_mode"));
    ecoEl.checked = !!state && state.state === "on";
  }

  _renderAxis() {
    const axis = this._axisEl;
    axis.innerHTML = "";
    const dayrow = document.createElement("div");
    dayrow.className = "dayrow";
    const bands = [
      [this._t0, this._tMid, "Vandaag", false],
      [this._tMid, this._t1, "Morgen", !this._tomorrowKnown],
    ];
    for (const [from, to, label, grey] of bands) {
      const el = document.createElement("div");
      el.className = `dayband${grey ? " grey" : ""}`;
      el.style.left = `${this._pct(from)}%`;
      el.style.width = `${this._pct(to) - this._pct(from)}%`;
      el.textContent = label;
      dayrow.appendChild(el);
    }
    if (!this._tomorrowKnown) {
      const mid = this._pct(this._tMid);
      const mark = document.createElement("div");
      mark.className = "warnmark";
      mark.style.left = `${mid + (100 - mid) / 2}%`;
      mark.textContent = "\u26a0";
      mark.title = "Prijzen van morgen zijn nog niet bekend";
      dayrow.appendChild(mark);
    }
    axis.appendChild(dayrow);

    const tickrow = document.createElement("div");
    tickrow.className = "tickrow";
    const spanHours = (this._t1 - this._t0) / 3600000;
    const step = spanHours <= 26 ? 2 : 3;
    let cursor = addHours(floorSlot(this._t0, 60), 1);
    for (; cursor < this._t1; cursor = addHours(cursor, 1)) {
      const line = document.createElement("div");
      line.className = "tick";
      line.style.left = `${this._pct(cursor)}%`;
      tickrow.appendChild(line);
      if (cursor.getHours() % step === 0) {
        const label = document.createElement("div");
        label.className = "ticklabel";
        label.style.left = `${this._pct(cursor)}%`;
        label.textContent = pad(cursor.getHours());
        tickrow.appendChild(label);
      }
    }
    const midnight = document.createElement("div");
    midnight.className = "tick major";
    midnight.style.left = `${this._pct(this._tMid)}%`;
    tickrow.appendChild(midnight);
    axis.appendChild(tickrow);
  }

  _renderPrices() {
    const host = this._pricesEl;
    if (!this._priceSlots || !this._priceSlots.length) {
      host.style.display = "none";
      return;
    }
    const visible = this._priceSlots.filter(
      (slot) => slot.t >= this._t0 && slot.t < this._t1
    );
    if (!visible.length) {
      host.style.display = "none";
      return;
    }
    host.style.display = "block";
    host.innerHTML = "";
    const prices = visible.map((slot) => slot.price);
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    for (const slot of visible) {
      const next = addMinutes(slot.t, this._slot);
      const ratio = high > low ? (slot.price - low) / (high - low) : 0.5;
      const bar = document.createElement("div");
      bar.className = "pbar";
      bar.style.left = `${this._pct(slot.t)}%`;
      bar.style.width = `${Math.max(0.2, this._pct(next) - this._pct(slot.t))}%`;
      bar.style.height = `${18 + ratio * 82}%`;
      bar.style.background = `hsl(${(1 - ratio) * 120}, 62%, 45%)`;
      bar.title = `${hm(slot.t)} \u00b7 ${slot.price.toFixed(3)}`;
      host.appendChild(bar);
    }
    if (!this._tomorrowKnown) {
      const mid = this._pct(this._tMid);
      const veil = document.createElement("div");
      veil.className = "pgrey";
      veil.style.left = `${mid}%`;
      veil.style.width = `${100 - mid}%`;
      host.appendChild(veil);
    }
  }

  // -- editing -----------------------------------------------------------

  _toggleActive(index) {
    const seg = this._segs[index];
    if (!seg) return;
    seg.enabled = !seg.enabled;
    if (seg.enabled && seg.charge === 0 && seg.discharge === 0) {
      seg.charge = 100;
    }
    this._dirty = true;
    this._render();
  }

  _setMode(index, dir) {
    const seg = this._segs[index];
    if (!seg) return;
    const current = this._mode(seg);
    const carried =
      current === "charge"
        ? seg.charge
        : current === "discharge"
          ? seg.discharge
          : 0;
    const pct = clamp(carried || 100, 1, 100);
    if (dir === "charge") {
      seg.charge = pct;
      seg.discharge = 0;
    } else {
      seg.discharge = pct;
      seg.charge = 0;
    }
    seg.enabled = true;
    this._dirty = true;
    this._render();
  }

  _setCharge(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    seg.charge = clamp(Number(row.chargeInput.value) || 0, 0, 100);
    if (seg.charge > 0) {
      seg.discharge = 0;
      seg.enabled = true;
    }
    this._dirty = true;
    this._renderRow(index);
  }

  _setDischarge(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    seg.discharge = clamp(Number(row.dischargeInput.value) || 0, 0, 100);
    if (seg.discharge > 0) {
      seg.charge = 0;
      seg.enabled = true;
    }
    this._dirty = true;
    this._renderRow(index);
  }

  _setStart(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const value = minutesOf(parseHM(row.startInput.value));
    const stop = minutesOf(seg.stopHM);
    seg.startHM = timeOf(clamp(value, 0, stop - this._slot));
    this._dirty = true;
    this._renderRow(index);
  }

  _setStop(index) {
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const value = minutesOf(parseHM(row.stopInput.value));
    const start = minutesOf(seg.startHM);
    seg.stopHM = timeOf(clamp(value, start + this._slot, 1439));
    this._dirty = true;
    this._renderRow(index);
  }

  _toggleDay(index, key) {
    const seg = this._segs[index];
    if (!seg) return;
    if (seg.days.has(key)) seg.days.delete(key);
    else seg.days.add(key);
    this._dirty = true;
    this._render();
  }

  _startDrag(event, index) {
    const handle = event.target.closest(".handle");
    if (!handle) return;
    const seg = this._segs[index];
    if (!seg || !seg.enabled) return;
    event.preventDefault();
    this._drag = {
      index,
      which: handle.classList.contains("left") ? "left" : "right",
      day: handle.dataset.day === "tomorrow" ? this._tomorrowDate : this._todayDate,
    };
    window.addEventListener("pointermove", this._onPointerMove);
    window.addEventListener("pointerup", this._onPointerUp);
    window.addEventListener("pointercancel", this._onPointerUp);
  }

  _onPointerMove(event) {
    if (!this._drag) return;
    const { index, which, day } = this._drag;
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const rect = row.track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const absolute =
      this._t0.getTime() + ratio * (this._t1.getTime() - this._t0.getTime());
    const rounded =
      Math.round((absolute - day.getTime()) / MIN / this._slot) * this._slot;
    const start = minutesOf(seg.startHM);
    const stop = minutesOf(seg.stopHM);
    if (which === "left") {
      seg.startHM = timeOf(clamp(rounded, 0, stop - this._slot));
    } else {
      seg.stopHM = timeOf(clamp(rounded, start + this._slot, 1439));
    }
    this._dirty = true;
    this._renderRow(index);
  }

  _onPointerUp() {
    this._drag = null;
    window.removeEventListener("pointermove", this._onPointerMove);
    window.removeEventListener("pointerup", this._onPointerUp);
    window.removeEventListener("pointercancel", this._onPointerUp);
  }

  // -- actions -----------------------------------------------------------

  _collectWindows() {
    const windows = [];
    for (let i = 0; i < this._count; i++) {
      const seg = this._segs[i];
      const mode = this._mode(seg);
      if (!seg.enabled || mode === "off") continue;
      const days = DAY_KEYS.filter((key) => this._effectiveDays(seg).has(key));
      if (!days.length) continue;
      const power = mode === "charge" ? -seg.charge : seg.discharge;
      windows.push({
        start: `${fmtHM(seg.startHM)}:00`,
        stop: `${fmtHM(seg.stopHM)}:00`,
        power,
        days: days.join(","),
      });
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
        this._dirty = true;
        this._render();
      })
      .catch((err) => this._showError(err));
  }

  _applyPlan(windows) {
    for (const seg of this._segs) {
      seg.enabled = false;
      seg.charge = 100;
      seg.discharge = 0;
      seg.days = new Set();
    }
    windows.slice(0, MAX_WINDOWS).forEach((window, index) => {
      const seg = this._segs[index];
      if (!seg) return;
      seg.startHM = parseHM(window.start);
      seg.stopHM = parseHM(window.stop);
      const power = Number(window.power) || 0;
      if (power < 0) {
        seg.charge = clamp(Math.round(-power), 1, 100);
        seg.discharge = 0;
        seg.enabled = true;
      } else if (power > 0) {
        seg.discharge = clamp(Math.round(power), 1, 100);
        seg.charge = 0;
        seg.enabled = true;
      }
      seg.days = expandDays(window.days || "all");
    });
    if (windows.length) {
      this._count = clamp(windows.length, 1, MAX_WINDOWS);
    }
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
    if (!hass) throw new Error("Home Assistant is niet beschikbaar");
    // The WebSocket command is the reliable way to ask for a service response.
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
    const result = await hass.callService(
      domain,
      service,
      data,
      undefined,
      true,
      true
    );
    return result && result.response !== undefined ? result.response : result;
  }

  _showError(err) {
    if (!this._errorEl) return;
    this._errorEl.textContent = (err && err.message) || "Actie mislukt";
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
    "Plan the Time-of-Use windows on a now-to-midnight-tomorrow timeline or a whole-week calendar, with separate charge/discharge sliders.",
  preview: false,
});

console.info(
  `%c GOODWE-TOU-PLANNER %c v${CARD_VERSION} `,
  "background:#2e7d32;color:#fff;font-weight:bold",
  "background:#333;color:#fff"
);
