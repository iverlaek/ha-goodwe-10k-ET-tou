/*
 * goodwe-tou-planner-card
 *
 * A "now -> midnight tomorrow" planner for the GoodWe Time-of-Use integration.
 *
 * It lays the four Time-of-Use windows out on a single timeline that starts at
 * the current quarter hour and ends at midnight at the end of tomorrow. Each
 * window is a draggable bar with a start and a stop handle, a charge/discharge
 * toggle and a manual power percentage.
 *
 * Design rules (see the integration README):
 *   - only the period from now until midnight tomorrow is shown;
 *   - tomorrow is greyed out until tomorrow's prices are known;
 *   - overlapping windows are impossible: dragging a handle stops at the
 *     neighbouring window;
 *   - a window that crosses midnight is split into two inverter windows on
 *     apply (the inverter stores one window per day).
 *
 * Configure with:
 *
 *   type: custom:goodwe-tou-planner-card
 *   title: Laadplanning                                  # optional
 *   prefix: goodwe_time_of_use                           # optional
 *   price_entity: sensor.average_electricity_price       # optional
 *
 * Everything is planned locally; nothing is written until "Toepassen" is
 * pressed (or "Goedkoopste uren", which calls the integration service).
 */

const PREFIX_DEFAULT = "goodwe_time_of_use";
const WINDOW_COUNT = 4;
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const MIN = 60000;

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

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
const roundSlot = (date, slot) => {
  const floored = floorSlot(date, slot);
  return date.getTime() - floored.getTime() >= (slot * MIN) / 2
    ? addMinutes(floored, slot)
    : floored;
};

const weekdayKey = (date) => DAY_KEYS[(date.getDay() + 6) % 7];
const hm = (date) =>
  `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
const parseHM = (text) => {
  const parts = String(text || "0:0").split(":");
  return { h: Number(parts[0]) || 0, m: Number(parts[1]) || 0 };
};
const withHM = (day, hour, minute) => {
  const copy = new Date(day);
  copy.setHours(hour, minute, 0, 0);
  return copy;
};

const expandDays = (text) => {
  const value = String(text || "").toLowerCase();
  if (value.includes("all") || value.includes("every") || value.includes("mon-sun")) {
    return DAY_KEYS.slice();
  }
  if (value.includes("weekday")) return ["mon", "tue", "wed", "thu", "fri"];
  if (value.includes("weekend")) return ["sat", "sun"];
  if (!value || value === "none" || value === "off" || value === "-") return [];
  return DAY_KEYS.filter((key) => value.includes(key));
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
  .hint { font-size: .78rem; color: var(--secondary-text-color); margin-bottom: 10px; min-height: 1.1em; }
  .hint.warn { color: var(--warning-color, #ff9800); }

  .axis { position: relative; margin-bottom: 4px; }
  .axis .dayrow { position: relative; height: 20px; }
  .axis .dayband { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: .74rem; font-weight: 600; color: var(--secondary-text-color); background: var(--secondary-background-color); border-radius: 6px; overflow: hidden; }
  .axis .dayband.grey { opacity: .45; }
  .axis .tickrow { position: relative; height: 16px; margin-top: 2px; }
  .axis .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--divider-color); opacity: .7; }
  .axis .tick.major { width: 2px; opacity: 1; }
  .axis .ticklabel { position: absolute; top: 0; transform: translateX(-50%); font-size: .68rem; color: var(--secondary-text-color); }

  .prices { position: relative; height: 44px; margin: 2px 0 8px; border-radius: 8px; background: var(--secondary-background-color); overflow: hidden; }
  .pbar { position: absolute; bottom: 0; border-radius: 2px 2px 0 0; opacity: .85; }

  .row { padding: 8px 0; border-top: 1px solid var(--divider-color); }
  .row:first-child { border-top: none; }
  .rowtop { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .wlabel { font-weight: 600; font-size: .88rem; color: var(--primary-text-color); }
  .badge { font-size: .78rem; font-variant-numeric: tabular-nums; padding: 2px 9px; border-radius: 10px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .badge.charge { background: #c62828; color: #fff; }
  .badge.discharge { background: #2e7d32; color: #fff; }
  .spacer { flex: 1; }
  button.mode { font: inherit; font-size: .8rem; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); cursor: pointer; }
  button.mode.charge { background: #c62828; border-color: #c62828; color: #fff; }
  button.mode.discharge { background: #2e7d32; border-color: #2e7d32; color: #fff; }
  .pctwrap { display: flex; align-items: center; gap: 2px; font-size: .8rem; color: var(--secondary-text-color); }
  .pctwrap input { width: 52px; font: inherit; padding: 3px 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); text-align: right; }
  .pctwrap input:disabled { opacity: .4; }
  button.off { font: inherit; font-size: .8rem; line-height: 1; padding: 4px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--secondary-text-color); cursor: pointer; }

  .track { position: relative; height: 40px; border-radius: 8px; background: var(--secondary-background-color); overflow: hidden; }
  .track .grey { position: absolute; top: 0; bottom: 0; background: repeating-linear-gradient(45deg, rgba(128,128,128,.16) 0 6px, transparent 6px 12px); pointer-events: none; }
  .track .divider { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--divider-color); opacity: .8; pointer-events: none; }
  .bar { position: absolute; top: 6px; bottom: 6px; border-radius: 6px; background: var(--disabled-color, #9e9e9e); display: flex; align-items: center; justify-content: center; min-width: 6px; }
  .bar.charge { background: linear-gradient(180deg, #ef5350, #b71c1c); }
  .bar.discharge { background: linear-gradient(180deg, #66bb6a, #1b5e20); }
  .barlabel { font-size: .7rem; font-weight: 600; color: #fff; white-space: nowrap; pointer-events: none; text-shadow: 0 1px 2px rgba(0,0,0,.45); }
  .handle { position: absolute; top: 50%; width: 14px; height: 30px; transform: translateY(-50%); border-radius: 6px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.28); cursor: ew-resize; touch-action: none; }
  .handle.left { left: -6px; }
  .handle.right { right: -6px; }
  .empty { position: absolute; inset: 0; display: none; align-items: center; justify-content: center; font-size: .78rem; color: var(--secondary-text-color); }

  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .actions button { flex: 1; padding: 10px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; cursor: pointer; }
  .actions button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .actions button:active { transform: translateY(1px); }
  .error { color: var(--error-color, #d32f2f); font-size: .8rem; margin-top: 8px; min-height: 1em; }
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
    this._priceSlots = [];
    this._tomorrowKnown = true;
    this._slot = 15;
    this._onDragMove = this._onDragMove.bind(this);
    this._onDragUp = this._onDragUp.bind(this);
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._title = this._config.title || "Laadplanning";
    this._priceEntity = this._config.price_entity || null;
    this._slot = Number(this._config.slot_minutes) > 0 ? Number(this._config.slot_minutes) : 15;
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._built) return;

    const priceState = this._priceEntity ? this._state(this._priceEntity) : undefined;
    const priceSig = priceState ? priceState.last_updated : null;
    if (priceSig !== this._priceSig) {
      this._priceSig = priceSig;
      this._prices = this._detectPrices();
      this._priceSlots = this._prices.slots;
      this._tomorrowKnown = this._prices.tomorrowKnown;
    }
    if (!this._dirty) this._readEntities();
    this._renderEco();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 10;
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
        <div class="hint" id="hint"></div>
        <div class="axis" id="axis"></div>
        <div class="prices" id="prices"></div>
        <div class="rows" id="rows"></div>
        <div class="actions">
          <button id="read">Lezen</button>
          <button id="cheapest">Goedkoopste uren</button>
          <button id="apply" class="primary">Toepassen</button>
        </div>
        <div class="error" id="error"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = this._title;
    this._axisEl = root.querySelector("#axis");
    this._pricesEl = root.querySelector("#prices");
    this._hintEl = root.querySelector("#hint");
    this._errorEl = root.querySelector("#error");

    const rowsEl = root.querySelector("#rows");
    this._rows = [];
    for (let n = 1; n <= WINDOW_COUNT; n++) {
      this._rows.push(this._makeRow(rowsEl, n));
    }

    const ecoEl = root.querySelector("#eco");
    ecoEl.addEventListener("change", () =>
      this._callService("switch", ecoEl.checked ? "turn_on" : "turn_off", {
        entity_id: this._entity("switch", "eco_mode"),
      }).catch((err) => this._showError(err))
    );

    root.querySelector("#read").addEventListener("click", () => this._read());
    root.querySelector("#cheapest").addEventListener("click", () => this._cheapest());
    root.querySelector("#apply").addEventListener("click", () => this._apply());

    this._built = true;
  }

  _makeRow(host, n) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      <div class="rowtop">
        <span class="wlabel">Venster ${n}</span>
        <span class="badge"></span>
        <span class="spacer"></span>
        <button class="mode"></button>
        <label class="pctwrap"><input type="number" min="1" max="100" step="5">%</label>
        <button class="off" title="Venster uitzetten">&#10005;</button>
      </div>
      <div class="track">
        <div class="grey"></div>
        <div class="divider"></div>
        <div class="bar"><span class="handle left"></span><span class="handle right"></span><span class="barlabel"></span></div>
        <div class="empty">niet actief</div>
      </div>
    `;
    const refs = {
      n,
      row,
      badge: row.querySelector(".badge"),
      modeBtn: row.querySelector(".mode"),
      pctInput: row.querySelector(".pctwrap input"),
      offBtn: row.querySelector(".off"),
      track: row.querySelector(".track"),
      grey: row.querySelector(".grey"),
      divider: row.querySelector(".divider"),
      bar: row.querySelector(".bar"),
      barLabel: row.querySelector(".barlabel"),
      handleL: row.querySelector(".handle.left"),
      handleR: row.querySelector(".handle.right"),
      empty: row.querySelector(".empty"),
    };

    refs.modeBtn.addEventListener("click", () => this._toggleMode(n - 1));
    refs.offBtn.addEventListener("click", () => this._disable(n - 1));
    refs.pctInput.addEventListener("change", () => {
      const value = clamp(Number(refs.pctInput.value) || 100, 1, 100);
      const seg = this._segs[n - 1];
      if (seg) seg.power = value;
      this._dirty = true;
      this._render();
    });
    this._attachDrag(refs, "left");
    this._attachDrag(refs, "right");

    host.appendChild(row);
    return refs;
  }

  _attachDrag(row, which) {
    const handle = which === "left" ? row.handleL : row.handleR;
    handle.addEventListener("pointerdown", (event) => {
      const index = row.n - 1;
      const seg = this._segs[index];
      if (!seg || !seg.active) return;
      if (!this._tomorrowKnown && seg.start >= this._tMid) return;
      event.preventDefault();
      event.stopPropagation();
      try {
        handle.setPointerCapture(event.pointerId);
      } catch (err) {
        /* ignore */
      }
      this._drag = { index, which };
      handle.addEventListener("pointermove", this._onDragMove);
      handle.addEventListener("pointerup", this._onDragUp);
      handle.addEventListener("pointercancel", this._onDragUp);
    });
  }

  _onDragMove(event) {
    if (!this._drag) return;
    const { index, which } = this._drag;
    const seg = this._segs[index];
    const row = this._rows[index];
    if (!seg || !row) return;
    const rect = row.track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    let moment = new Date(this._t0.getTime() + ratio * (this._t1 - this._t0));
    moment = roundSlot(moment, this._slot);
    const bounds = this._bounds(seg);
    if (which === "left") {
      const high = addMinutes(seg.stop, -this._slot).getTime();
      seg.start = new Date(clamp(moment.getTime(), bounds.lo.getTime(), high));
    } else {
      const low = addMinutes(seg.start, this._slot).getTime();
      seg.stop = new Date(clamp(moment.getTime(), low, bounds.hi.getTime()));
    }
    this._dirty = true;
    this._render();
  }

  _onDragUp(event) {
    const handle = event.currentTarget;
    handle.removeEventListener("pointermove", this._onDragMove);
    handle.removeEventListener("pointerup", this._onDragUp);
    handle.removeEventListener("pointercancel", this._onDragUp);
    try {
      handle.releasePointerCapture(event.pointerId);
    } catch (err) {
      /* ignore */
    }
    this._drag = null;
  }

  _bounds(seg) {
    let lo = new Date(this._t0);
    let hi = this._tomorrowKnown ? new Date(this._t1) : new Date(this._tMid);
    for (const other of this._segs) {
      if (other === seg || !other.active) continue;
      if (other.stop <= seg.start) {
        lo = new Date(Math.max(lo.getTime(), other.stop.getTime()));
      }
      if (other.start >= seg.stop) {
        hi = new Date(Math.min(hi.getTime(), other.start.getTime()));
      }
    }
    if (lo.getTime() > hi.getTime()) lo = new Date(hi);
    return { lo, hi };
  }

  // -- data --------------------------------------------------------------

  _readEntities() {
    const now = new Date();
    this._t0 = floorSlot(now, this._slot);
    this._tMid = startOfDay(addDays(now, 1));
    this._t1 = startOfDay(addDays(now, 2));
    const todayKey = weekdayKey(now);
    const tomorrowKey = weekdayKey(addDays(now, 1));
    this._segs = [];
    for (let n = 1; n <= WINDOW_COUNT; n++) {
      this._segs.push(this._readWindow(n, todayKey, tomorrowKey));
    }
  }

  _readWindow(n, todayKey, tomorrowKey) {
    const startState = this._state(this._entity("time", `window_${n}_start`));
    const stopState = this._state(this._entity("time", `window_${n}_stop`));
    const powerState = this._state(this._entity("number", `window_${n}_power`));
    const daysState = this._state(this._entity("text", `window_${n}_days`));
    const base = {
      slot: n,
      active: false,
      mode: "off",
      power: 100,
      start: this._t0,
      stop: addMinutes(this._t0, this._slot),
    };
    if (!startState || !stopState || !powerState) return base;

    const days = daysState ? daysState.state : "all";
    const daySet = expandDays(days);
    let dayDate = null;
    if (daySet.includes(todayKey)) dayDate = startOfDay(this._t0);
    else if (daySet.includes(tomorrowKey)) dayDate = startOfDay(this._tMid);

    const signed = Number(powerState.state) || 0;
    let power = Math.abs(signed);
    power = power > 0 ? Math.min(100, power) : 100;
    const mode = signed < 0 ? "charge" : signed > 0 ? "discharge" : "off";
    if (dayDate === null) return { ...base, mode, power };

    const start = parseHM(startState.state);
    const stop = parseHM(stopState.state);
    let startAt = withHM(dayDate, start.h, start.m);
    let stopAt = withHM(dayDate, stop.h, stop.m);
    if (stopAt <= startAt) return { ...base, mode, power };
    if (stopAt <= this._t0) return { ...base, mode, power };
    if (startAt < this._t0) startAt = new Date(this._t0);
    if (stopAt > this._t1) stopAt = new Date(this._t1);
    return { slot: n, active: true, mode, power, start: startAt, stop: stopAt };
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

  // -- rendering ---------------------------------------------------------

  _render() {
    if (!this._segs.length) return;
    this._renderAxis();
    this._renderPrices();
    const pct = (moment) =>
      ((moment.getTime() - this._t0.getTime()) / (this._t1.getTime() - this._t0.getTime())) * 100;

    this._hintEl.className = this._tomorrowKnown ? "hint" : "hint warn";
    this._hintEl.textContent = this._tomorrowKnown
      ? "Prijzen t/m morgen bekend."
      : "Morgen is nog grijs: prijzen van morgen zijn nog niet bekend (meestal rond 15:00).";

    const activeElement = this.shadowRoot.activeElement;
    for (let i = 0; i < this._rows.length; i++) {
      const row = this._rows[i];
      const seg = this._segs[i];
      const midPct = pct(this._tMid);

      row.divider.style.left = `${midPct}%`;
      if (this._tomorrowKnown) {
        row.grey.style.display = "none";
      } else {
        row.grey.style.display = "block";
        row.grey.style.left = `${midPct}%`;
        row.grey.style.width = `${100 - midPct}%`;
      }

      if (seg.active) {
        const left = pct(seg.start);
        const width = Math.max(0.5, pct(seg.stop) - left);
        row.bar.style.display = "flex";
        row.empty.style.display = "none";
        row.bar.style.left = `${left}%`;
        row.bar.style.width = `${width}%`;
        row.bar.className = `bar ${seg.mode === "off" ? "" : seg.mode}`;
        row.barLabel.textContent = `${hm(seg.start)} \u2013 ${hm(seg.stop)}`;
      } else {
        row.bar.style.display = "none";
        row.empty.style.display = "flex";
      }

      const label =
        seg.mode === "charge"
          ? `${seg.power}% laden`
          : seg.mode === "discharge"
            ? `${seg.power}% ontladen`
            : seg.active
              ? "uit"
              : "niet actief";
      row.badge.textContent = label;
      row.badge.className = `badge ${seg.mode === "off" ? "" : seg.mode}`;

      row.modeBtn.textContent =
        seg.mode === "charge" ? "Laden" : seg.mode === "discharge" ? "Ontladen" : "Uit";
      row.modeBtn.className = `mode ${seg.mode === "off" ? "" : seg.mode}`;
      if (activeElement !== row.pctInput) row.pctInput.value = String(seg.power);
      row.pctInput.disabled = seg.mode === "off";
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
    const pct = (moment) =>
      ((moment.getTime() - this._t0.getTime()) / (this._t1.getTime() - this._t0.getTime())) * 100;

    const dayrow = document.createElement("div");
    dayrow.className = "dayrow";
    const bands = [
      [this._t0, this._tMid, "Vandaag", false],
      [this._tMid, this._t1, "Morgen", !this._tomorrowKnown],
    ];
    for (const [from, to, label, grey] of bands) {
      const el = document.createElement("div");
      el.className = `dayband${grey ? " grey" : ""}`;
      el.style.left = `${pct(from)}%`;
      el.style.width = `${pct(to) - pct(from)}%`;
      el.textContent = label;
      dayrow.appendChild(el);
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
      line.style.left = `${pct(cursor)}%`;
      tickrow.appendChild(line);
      if (cursor.getHours() % step === 0) {
        const label = document.createElement("div");
        label.className = "ticklabel";
        label.style.left = `${pct(cursor)}%`;
        label.textContent = String(cursor.getHours()).padStart(2, "0");
        tickrow.appendChild(label);
      }
    }
    const midnight = document.createElement("div");
    midnight.className = "tick major";
    midnight.style.left = `${pct(this._tMid)}%`;
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
    const pct = (moment) =>
      ((moment.getTime() - this._t0.getTime()) / (this._t1.getTime() - this._t0.getTime())) * 100;
    for (const slot of visible) {
      const next = addMinutes(slot.t, this._slot);
      const ratio = high > low ? (slot.price - low) / (high - low) : 0.5;
      const bar = document.createElement("div");
      bar.className = "pbar";
      bar.style.left = `${pct(slot.t)}%`;
      bar.style.width = `${Math.max(0.2, pct(next) - pct(slot.t))}%`;
      bar.style.height = `${18 + ratio * 82}%`;
      bar.style.background = `hsl(${(1 - ratio) * 120}, 62%, 45%)`;
      bar.title = `${hm(slot.t)} \u00b7 ${slot.price.toFixed(3)}`;
      host.appendChild(bar);
    }
  }

  // -- editing -----------------------------------------------------------

  _toggleMode(index) {
    const seg = this._segs[index];
    if (!seg) return;
    if (!seg.active) {
      const slot = this._freeSlot();
      if (!slot) {
        this._showError("Geen vrije ruimte meer op de tijdlijn.");
        return;
      }
      seg.start = slot.start;
      seg.stop = slot.stop;
      seg.active = true;
      seg.mode = "charge";
    } else if (seg.mode === "charge") {
      seg.mode = "discharge";
    } else {
      seg.mode = "charge";
    }
    this._dirty = true;
    this._render();
  }

  _disable(index) {
    const seg = this._segs[index];
    if (!seg || !seg.active) return;
    seg.mode = "off";
    this._dirty = true;
    this._render();
  }

  _freeSlot() {
    const high = this._tomorrowKnown ? this._t1 : this._tMid;
    const busy = this._segs
      .filter((seg) => seg.active && seg.mode !== "off")
      .map((seg) => [seg.start.getTime(), seg.stop.getTime()])
      .sort((a, b) => a[0] - b[0]);
    let cursor = this._t0.getTime();
    const gaps = [];
    for (const [from, to] of busy) {
      if (from > cursor) gaps.push([cursor, from]);
      cursor = Math.max(cursor, to);
    }
    if (high.getTime() > cursor) gaps.push([cursor, high.getTime()]);
    const gap =
      gaps.find((g) => g[1] - g[0] >= 60 * MIN) ||
      gaps.find((g) => g[1] - g[0] >= this._slot * MIN);
    if (!gap) return null;
    return {
      start: new Date(gap[0]),
      stop: new Date(Math.min(gap[0] + 60 * MIN, gap[1])),
    };
  }

  // -- actions -----------------------------------------------------------

  _collectWindows() {
    const windows = [];
    for (const seg of this._segs) {
      if (!seg.active || seg.mode === "off" || seg.stop <= seg.start) continue;
      const power = seg.mode === "charge" ? -Math.abs(seg.power) : Math.abs(seg.power);
      let cursor = new Date(seg.start);
      while (cursor < seg.stop) {
        const boundary = new Date(
          Math.min(seg.stop.getTime(), startOfDay(addDays(cursor, 1)).getTime())
        );
        const stopTime = boundary.getHours() === 0 && boundary.getMinutes() === 0
          ? "23:59:00"
          : `${hm(boundary)}:00`;
        windows.push({
          start: `${hm(cursor)}:00`,
          stop: stopTime,
          power,
          days: weekdayKey(cursor),
        });
        cursor = boundary;
      }
    }
    windows.sort((a, b) => {
      const dayDiff = DAY_KEYS.indexOf(a.days) - DAY_KEYS.indexOf(b.days);
      return dayDiff !== 0 ? dayDiff : a.start.localeCompare(b.start);
    });
    return windows;
  }

  _read() {
    this._dirty = false;
    this._callService("button", "press", {
      entity_id: this._entity("button", "read_from_inverter"),
    }).catch((err) => this._showError(err));
  }

  _cheapest() {
    this._dirty = false;
    this._callService("goodwe_tou", "set_cheapest_hours", {}).catch((err) =>
      this._showError(err)
    );
  }

  _apply() {
    const windows = this._collectWindows();
    if (windows.length > WINDOW_COUNT) {
      this._showError(
        `Te veel vensters (${windows.length}); de omvormer heeft er maximaal ${WINDOW_COUNT}. ` +
          "Maak een venster korter of zet er een uit."
      );
      return;
    }
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
    "Plan the four Time-of-Use windows from now until midnight tomorrow, with draggable charge/discharge windows.",
  preview: false,
});

console.info(
  "%c GOODWE-TOU-PLANNER %c v0.3.0 ",
  "background:#2e7d32;color:#fff;font-weight:bold",
  "background:#333;color:#fff"
);
