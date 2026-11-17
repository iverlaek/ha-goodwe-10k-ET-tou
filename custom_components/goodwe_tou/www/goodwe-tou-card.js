/*
 * goodwe-tou-card
 *
 * A read-only overview for the GoodWe Time-of-Use integration:
 *
 *   - a calendar of the current week (ma .. zo), listing the windows that run on
 *     each day, taken from the four Time-of-Use windows on the inverter;
 *   - below it a battery state-of-charge graph: the measured SoC for the last
 *     `hours_back` hours, continuing with the *forecast* SoC until the end of
 *     tomorrow. The forecast follows the windows that are on the inverter right
 *     now, so it shows what you get if you leave the plan as it is.
 *
 * The card is read-only; editing happens in the planner card
 * (`custom:goodwe-tou-planner-card`).
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-card
 *   title: GoodWe Time-of-Use                     # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                    # optional entity-id prefix
 *   soc_entity: sensor.battery_state_of_charge    # optional, for the graph
 *   hours_back: 24                                # optional, measured hours shown
 */

const CARD_VERSION = "0.7.1";
const PREFIX_DEFAULT = "goodwe_time_of_use";
const SOC_DEFAULT = "sensor.battery_state_of_charge";
const HOURS_BACK_DEFAULT = 24;
const WINDOW_COUNT = 4;
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
const SVG_NS = "http://www.w3.org/2000/svg";
const CHART_HEIGHT = 190;

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const pad = (value) => String(value).padStart(2, "0");
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
const weekdayKey = (date) => DAY_KEYS[(date.getDay() + 6) % 7];
const fmtHM = (time) => `${pad(time.h)}:${pad(time.m)}`;
const parseHM = (text) => {
  const parts = String(text || "0:0").split(":");
  return {
    h: clamp(Number(parts[0]) || 0, 0, 23),
    m: clamp(Number(parts[1]) || 0, 0, 59),
  };
};
const minutesOf = (time) => time.h * 60 + time.m;
const expandDays = (text) => {
  const set = new Set();
  const value = String(text || "").trim().toLowerCase();
  if (!value || value === "none" || value === "off") return set;
  if (value.includes("all") || value.includes("every") || value.includes("mon-sun")) {
    DAY_KEYS.forEach((key) => set.add(key));
    return set;
  }
  if (value.includes("weekday")) {
    ["mon", "tue", "wed", "thu", "fri"].forEach((key) => set.add(key));
    return set;
  }
  if (value.includes("weekend")) {
    ["sat", "sun"].forEach((key) => set.add(key));
    return set;
  }
  DAY_KEYS.forEach((key) => {
    if (value.includes(key)) set.add(key);
  });
  return set;
};
const svgEl = (tag, attrs) => {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    el.setAttribute(key, String(value));
  }
  return el;
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }

  .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
  .wday { min-width: 0; border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px 6px; background: var(--card-background-color); }
  .wday.today { border-color: var(--primary-color); box-shadow: inset 0 0 0 1px var(--primary-color); }
  .wdayname { font-size: .78rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); margin-bottom: 6px; text-align: center; }
  .wday.today .wdayname { color: var(--primary-color); }
  .witem { display: flex; flex-direction: column; gap: 1px; padding: 5px 6px; border-radius: 8px; margin-bottom: 5px; background: var(--secondary-background-color); }
  .witem:last-child { margin-bottom: 0; }
  .witem.charge { background: rgba(198, 40, 40, .14); }
  .witem.discharge { background: rgba(46, 125, 50, .16); }
  .wtime { font-size: .82rem; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .wpow { font-size: .72rem; color: var(--secondary-text-color); }
  .witem.charge .wpow { color: #c62828; }
  .witem.discharge .wpow { color: #2e7d32; }
  .wempty { font-size: .72rem; color: var(--secondary-text-color); text-align: center; padding: 4px 0; }

  .charthead { display: flex; align-items: baseline; justify-content: space-between; margin: 16px 0 4px; }
  .charttitle { font-size: .92rem; font-weight: 600; color: var(--primary-text-color); }
  .legend { display: flex; gap: 12px; font-size: .74rem; color: var(--secondary-text-color); }
  .legend .swatch { display: inline-block; width: 16px; height: 0; border-top-width: 3px; border-top-style: solid; vertical-align: middle; margin-right: 4px; }
  .legend .measured .swatch { border-top-color: var(--primary-color, #03a9f4); }
  .legend .forecast .swatch { border-top-color: #ff9800; border-top-style: dashed; }

  svg.chart { display: block; width: 100%; overflow: visible; }
  .note { font-size: .74rem; color: var(--secondary-text-color); margin-top: 4px; }
  .error { color: var(--error-color, #d32f2f); font-size: .8rem; margin-top: 8px; }
`;

class GoodweTouCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._history = [];
    this._forecast = [];
    this._lastFetch = 0;
    this._fetching = false;
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._title = this._config.title || "GoodWe Time-of-Use";
    this._socEntity = this._config.soc_entity || SOC_DEFAULT;
    this._hoursBack =
      Number(this._config.hours_back) > 0
        ? Number(this._config.hours_back)
        : HOURS_BACK_DEFAULT;
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._built) return;
    this._renderWeek();
    this._renderChart();
    this._maybeFetch();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 14;
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
        </div>
        <div class="week" id="week"></div>
        <div class="charthead">
          <span class="charttitle">Batterij-SoC</span>
          <span class="legend">
            <span class="measured"><span class="swatch"></span>gemeten</span>
            <span class="forecast"><span class="swatch"></span>voorspeld</span>
          </span>
        </div>
        <svg class="chart" id="chart" preserveAspectRatio="none"></svg>
        <div class="note" id="note"></div>
        <div class="error" id="error"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${this._title} (Version ${CARD_VERSION})`;
    this._weekEl = root.querySelector("#week");
    this._chartEl = root.querySelector("#chart");
    this._noteEl = root.querySelector("#note");
    this._errorEl = root.querySelector("#error");
    this._built = true;

    if (typeof ResizeObserver === "function") {
      this._resizeObserver = new ResizeObserver(() => this._renderChart());
      this._resizeObserver.observe(this._chartEl);
    }
    requestAnimationFrame(() => this._renderChart());
  }

  // -- data --------------------------------------------------------------

  _readWindows() {
    const windows = [];
    for (let n = 1; n <= WINDOW_COUNT; n++) {
      const startState = this._state(this._entity("time", `window_${n}_start`));
      const stopState = this._state(this._entity("time", `window_${n}_stop`));
      const powerState = this._state(this._entity("number", `window_${n}_power`));
      const daysState = this._state(this._entity("text", `window_${n}_days`));
      if (!startState || !stopState || !powerState) continue;
      const signed = Number(powerState.state) || 0;
      if (signed === 0) continue;
      const startHM = parseHM(startState.state);
      let stopHM = parseHM(stopState.state);
      if (minutesOf(stopHM) <= minutesOf(startHM)) continue;
      windows.push({
        n,
        mode: signed < 0 ? "charge" : "discharge",
        pct: clamp(Math.round(Math.abs(signed)), 1, 100),
        startHM,
        stopHM,
        days: expandDays(daysState ? daysState.state : "all"),
      });
    }
    return windows;
  }

  _maybeFetch() {
    if (this._fetching) return;
    const now = Date.now();
    if (this._lastFetch && now - this._lastFetch < 5 * 60 * 1000) return;
    this._lastFetch = now;
    this._fetching = true;
    Promise.all([this._loadHistory(), this._loadForecast()]).finally(() => {
      this._fetching = false;
      this._renderChart();
    });
  }

  async _loadHistory() {
    const hass = this._hass;
    if (!hass || typeof hass.callApi !== "function") return;
    const now = new Date();
    const from = new Date(now.getTime() - this._hoursBack * 3600000);
    const path =
      `history/period/${from.toISOString()}` +
      `?filter_entity_id=${this._socEntity}` +
      `&end_time=${now.toISOString()}&minimal_response&no_attributes`;
    try {
      const result = await hass.callApi("GET", path);
      const series = Array.isArray(result) ? result[0] : result;
      this._history = (series || [])
        .map((entry) => ({
          t: new Date(entry.last_changed || entry.last_updated),
          v: Number(entry.state),
        }))
        .filter((point) => !Number.isNaN(point.t.getTime()) && Number.isFinite(point.v))
        .sort((a, b) => a.t - b.t);
    } catch (err) {
      this._history = [];
    }
  }

  async _loadForecast() {
    if (!this._hass) return;
    try {
      const response = await this._callServiceWithResponse(
        "goodwe_tou",
        "forecast_soc",
        {}
      );
      const points = (response && response.points) || [];
      this._forecast = points
        .map((point) => ({ t: new Date(point.time), v: Number(point.soc) }))
        .filter((point) => !Number.isNaN(point.t.getTime()) && Number.isFinite(point.v))
        .sort((a, b) => a.t - b.t);
    } catch (err) {
      this._forecast = [];
    }
  }

  async _callServiceWithResponse(domain, service, data) {
    const hass = this._hass;
    if (!hass) throw new Error("Home Assistant is niet beschikbaar");
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

  _renderWeek() {
    const host = this._weekEl;
    if (!host) return;
    host.innerHTML = "";
    const windows = this._readWindows();
    const todayKey = weekdayKey(new Date());
    for (let d = 0; d < DAY_KEYS.length; d++) {
      const key = DAY_KEYS[d];
      const day = document.createElement("div");
      day.className = key === todayKey ? "wday today" : "wday";

      const head = document.createElement("div");
      head.className = "wdayname";
      head.textContent = DAY_LABELS[d];
      day.appendChild(head);

      const items = windows
        .filter((window) => window.days.has(key))
        .sort((a, b) => minutesOf(a.startHM) - minutesOf(b.startHM));
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "wempty";
        empty.textContent = "\u2013";
        day.appendChild(empty);
      }
      for (const window of items) {
        const item = document.createElement("div");
        item.className = `witem ${window.mode}`;
        item.title = `Venster ${window.n}`;
        const time = document.createElement("span");
        time.className = "wtime";
        time.textContent = `${fmtHM(window.startHM)}\u2013${fmtHM(window.stopHM)}`;
        const power = document.createElement("span");
        power.className = "wpow";
        power.textContent =
          window.mode === "charge"
            ? `${window.pct}% laden`
            : `${window.pct}% ontladen`;
        item.appendChild(time);
        item.appendChild(power);
        day.appendChild(item);
      }
      host.appendChild(day);
    }
  }

  _renderChart() {
    const host = this._chartEl;
    if (!host) return;
    host.innerHTML = "";

    const width = Math.max(
      240,
      Math.round(host.clientWidth || host.getBoundingClientRect().width || 600)
    );
    host.setAttribute("viewBox", `0 0 ${width} ${CHART_HEIGHT}`);

    const padL = 30;
    const padR = 12;
    const padT = 12;
    const padB = 26;
    const plotW = width - padL - padR;
    const plotH = CHART_HEIGHT - padT - padB;

    const now = new Date();
    const t0 = new Date(now.getTime() - this._hoursBack * 3600000);
    const t1 = startOfDay(addDays(now, 2));
    const span = Math.max(1, t1.getTime() - t0.getTime());
    const x = (moment) => padL + ((moment.getTime() - t0.getTime()) / span) * plotW;
    const y = (value) => padT + (1 - clamp(value, 0, 100) / 100) * plotH;

    // Shade the forecast part (now -> end of tomorrow).
    host.appendChild(
      svgEl("rect", {
        x: x(now),
        y: padT,
        width: Math.max(0, padL + plotW - x(now)),
        height: plotH,
        fill: "var(--secondary-background-color)",
        opacity: 0.45,
      })
    );

    // Horizontal grid + y labels.
    for (const value of [0, 25, 50, 75, 100]) {
      const strong = value % 50 === 0;
      host.appendChild(
        svgEl("line", {
          x1: padL,
          y1: y(value),
          x2: padL + plotW,
          y2: y(value),
          stroke: "var(--divider-color)",
          "stroke-width": strong ? 1 : 0.5,
          opacity: strong ? 0.9 : 0.5,
        })
      );
      const label = svgEl("text", {
        x: padL - 6,
        y: y(value) + 4,
        "text-anchor": "end",
        "font-size": "10",
        fill: "var(--secondary-text-color)",
      });
      label.textContent = String(value);
      host.appendChild(label);
    }

    // Day boundaries.
    for (const [moment, name] of [
      [startOfDay(now), "vandaag"],
      [startOfDay(addDays(now, 1)), "morgen"],
    ]) {
      const px = x(moment);
      if (px < padL || px > padL + plotW) continue;
      host.appendChild(
        svgEl("line", {
          x1: px,
          y1: padT,
          x2: px,
          y2: padT + plotH,
          stroke: "var(--divider-color)",
          "stroke-dasharray": "3 3",
        })
      );
      const label = svgEl("text", {
        x: px + 3,
        y: CHART_HEIGHT - 8,
        "font-size": "10",
        fill: "var(--secondary-text-color)",
      });
      label.textContent = name;
      host.appendChild(label);
    }

    // "Now" marker.
    const nowX = x(now);
    host.appendChild(
      svgEl("line", {
        x1: nowX,
        y1: padT,
        x2: nowX,
        y2: padT + plotH,
        stroke: "var(--error-color, #d32f2f)",
        "stroke-width": 1,
        opacity: 0.55,
      })
    );

    this._drawSeries(
      host,
      this._history.filter((point) => point.t >= t0 && point.t <= now),
      x,
      y,
      "var(--primary-color, #03a9f4)",
      false
    );
    this._drawSeries(
      host,
      this._forecast.filter((point) => point.t >= now && point.t <= t1),
      x,
      y,
      "#ff9800",
      true
    );

    if (this._noteEl) {
      if (!this._lastFetch) {
        this._noteEl.textContent = "Gegevens laden\u2026";
      } else if (!this._history.length && !this._forecast.length) {
        this._noteEl.textContent =
          "Geen SoC-gegevens: controleer soc_entity en de recorder.";
      } else if (!this._forecast.length) {
        this._noteEl.textContent =
          "Voorspelling niet beschikbaar (service goodwe_tou.forecast_soc).";
      } else {
        this._noteEl.textContent = "";
      }
    }
  }

  _drawSeries(host, points, x, y, color, dashed) {
    if (!points.length) return;
    if (points.length === 1) {
      host.appendChild(
        svgEl("circle", { cx: x(points[0].t), cy: y(points[0].v), r: 2.5, fill: color })
      );
      return;
    }
    const coords = points
      .map((point) => `${x(point.t).toFixed(1)},${y(point.v).toFixed(1)}`)
      .join(" ");
    const attrs = {
      points: coords,
      fill: "none",
      stroke: color,
      "stroke-width": 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    };
    if (dashed) attrs["stroke-dasharray"] = "5 4";
    host.appendChild(svgEl("polyline", attrs));
  }

  _showError(err) {
    if (!this._errorEl) return;
    this._errorEl.textContent = (err && err.message) || "Actie mislukt";
    clearTimeout(this._errorTimer);
    this._errorTimer = setTimeout(() => {
      this._errorEl.textContent = "";
    }, 6000);
  }
}

if (!customElements.get("goodwe-tou-card")) {
  customElements.define("goodwe-tou-card", GoodweTouCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goodwe-tou-card",
  name: "GoodWe Time-of-Use",
  description:
    "A read-only calendar of this week's Time-of-Use windows with a battery state-of-charge graph (measured + forecast).",
  preview: false,
});

console.info(`%c GOODWE-TOU-CARD %c v${CARD_VERSION} `, "background:#2e7d32;color:#fff;font-weight:bold", "background:#333;color:#fff");
