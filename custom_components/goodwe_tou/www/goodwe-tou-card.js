/*
 * goodwe-tou-card
 *
 * A single tile for the GoodWe Time-of-Use integration: four windows, each with
 * a start/stop time, a red-to-green slider (-100 % charge .. +100 % discharge)
 * and weekday chips, plus Read / Cheapest hours / Apply actions.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-card
 *   title: GoodWe Time-of-Use          # optional
 *   prefix: goodwe_time_of_use         # optional entity-id prefix
 */

const PREFIX_DEFAULT = "goodwe_time_of_use";
const WINDOW_COUNT = 4;
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"];

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .eco { display: flex; align-items: center; gap: 8px; font-size: .85rem; color: var(--secondary-text-color); }
  .switch { position: relative; width: 44px; height: 24px; flex: none; }
  .switch input { opacity: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; }
  .switch .track { position: absolute; inset: 0; border-radius: 12px; background: var(--disabled-color, #9e9e9e); transition: background .15s; pointer-events: none; }
  .switch .track::after { content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: transform .15s; }
  .switch input:checked + .track { background: var(--primary-color); }
  .switch input:checked + .track::after { transform: translateX(20px); }
  .row { padding: 12px 0; border-top: 1px solid var(--divider-color); }
  .row:first-child { border-top: none; }
  .rowhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .wlabel { font-weight: 600; color: var(--primary-text-color); }
  .badge { font-size: .82rem; font-variant-numeric: tabular-nums; padding: 2px 10px; border-radius: 10px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .badge.charge { background: #d32f2f; color: #fff; }
  .badge.discharge { background: #2e7d32; color: #fff; }
  .times { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  input[type=time] { flex: 1; min-width: 0; background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 8px; font: inherit; }
  .dash { color: var(--secondary-text-color); }
  input[type=range] { -webkit-appearance: none; appearance: none; width: 100%; height: 10px; border-radius: 6px; outline: none; margin: 6px 0 0; background: linear-gradient(90deg, #d32f2f 0%, #9e9e9e 50%, #2e7d32 100%); }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 22px; height: 22px; border-radius: 50%; background: #fff; border: 3px solid var(--thumb, #9e9e9e); cursor: pointer; }
  input[type=range]::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: #fff; border: 3px solid var(--thumb, #9e9e9e); cursor: pointer; }
  .scale { display: flex; justify-content: space-between; font-size: .7rem; color: var(--secondary-text-color); margin-top: 2px; }
  .days { display: flex; gap: 6px; margin-top: 10px; }
  .chip { flex: 1; padding: 6px 0; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .8rem; cursor: pointer; }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .actions button { flex: 1; padding: 10px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; cursor: pointer; }
  .actions button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .actions button:active { transform: translateY(1px); }
  .error { color: var(--error-color, #d32f2f); font-size: .8rem; margin-top: 8px; }
`;

class GoodweTouCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._rows = [];
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._title = this._config.title || "GoodWe Time-of-Use";
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
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
    return this._hass && this._hass.states[entityId];
  }

  _build() {
    if (!this._hass || !this.shadowRoot) return;
    this._built = true;
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
        <div class="rows"></div>
        <div class="actions">
          <button id="read">Read</button>
          <button id="cheapest">Cheapest hours</button>
          <button id="apply" class="primary">Apply</button>
        </div>
        <div class="error" id="error"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = this._title;

    const rowsEl = root.querySelector(".rows");
    this._rows = [];
    for (let n = 1; n <= WINDOW_COUNT; n++) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div class="rowhead">
          <span class="wlabel">Window ${n}</span>
          <span class="badge"></span>
        </div>
        <div class="times">
          <input type="time" data-start>
          <span class="dash">&ndash;</span>
          <input type="time" data-stop>
        </div>
        <input type="range" min="-100" max="100" step="1" value="0" data-power>
        <div class="scale"><span>charge</span><span>off</span><span>discharge</span></div>
        <div class="days"></div>
      `;

      const startEl = row.querySelector("[data-start]");
      const stopEl = row.querySelector("[data-stop]");
      const powerEl = row.querySelector("[data-power]");
      const badgeEl = row.querySelector(".badge");
      const daysEl = row.querySelector(".days");

      const entities = {
        start: this._entity("time", `window_${n}_start`),
        stop: this._entity("time", `window_${n}_stop`),
        power: this._entity("number", `window_${n}_power`),
        days: this._entity("text", `window_${n}_days`),
      };

      startEl.addEventListener("change", () => this._callTime(entities.start, startEl.value));
      stopEl.addEventListener("change", () => this._callTime(entities.stop, stopEl.value));
      powerEl.addEventListener("input", () => this._paintBadge(badgeEl, powerEl));
      powerEl.addEventListener("change", () =>
        this._callService("number", "set_value", { entity_id: entities.power, value: Number(powerEl.value) })
      );

      const chips = {};
      DAY_KEYS.forEach((key, index) => {
        const chip = document.createElement("button");
        chip.className = "chip";
        chip.textContent = DAY_LABELS[index];
        chip.addEventListener("click", () => this._toggleDay(n, entities.days, key));
        daysEl.appendChild(chip);
        chips[key] = chip;
      });

      rowsEl.appendChild(row);
      this._rows.push({ startEl, stopEl, powerEl, badgeEl, chips, entities });
    }

    const ecoEl = root.querySelector("#eco");
    ecoEl.addEventListener("change", () =>
      this._callService(
        "switch",
        ecoEl.checked ? "turn_on" : "turn_off",
        { entity_id: this._entity("switch", "eco_mode") }
      )
    );

    root.querySelector("#read").addEventListener("click", () =>
      this._press(this._entity("button", "read_from_inverter"))
    );
    root.querySelector("#apply").addEventListener("click", () =>
      this._press(this._entity("button", "apply_to_inverter"))
    );
    root.querySelector("#cheapest").addEventListener("click", () =>
      this._press(this._entity("button", "set_cheapest_hours"))
    );
  }

  _update() {
    const root = this.shadowRoot;
    if (!root || !this._rows.length) return;

    const eco = this._state(this._entity("switch", "eco_mode"));
    const ecoEl = root.querySelector("#eco");
    if (ecoEl && ecoEl !== root.activeElement) {
      ecoEl.checked = !!eco && eco.state === "on";
    }

    for (const row of this._rows) {
      const power = this._state(row.entities.power);
      const value = power ? Math.round(Number(power.state)) : 0;
      if (row.powerEl !== root.activeElement) row.powerEl.value = String(value);
      this._paintBadge(row.badgeEl, row.powerEl);

      const start = this._state(row.entities.start);
      if (start && row.startEl !== root.activeElement) {
        row.startEl.value = (start.state || "").slice(0, 5);
      }
      const stop = this._state(row.entities.stop);
      if (stop && row.stopEl !== root.activeElement) {
        row.stopEl.value = (stop.state || "").slice(0, 5);
      }

      const days = this._state(row.entities.days);
      const active = new Set(
        days && days.state && days.state !== "none"
          ? days.state.split(",").map((d) => d.trim())
          : []
      );
      for (const key of DAY_KEYS) {
        row.chips[key].classList.toggle("on", active.has(key));
      }
    }
  }

  _paintBadge(badgeEl, powerEl) {
    const value = Number(powerEl.value) || 0;
    if (value < 0) {
      badgeEl.textContent = `${-value}% charge`;
      badgeEl.className = "badge charge";
      powerEl.style.setProperty("--thumb", "#d32f2f");
    } else if (value > 0) {
      badgeEl.textContent = `${value}% discharge`;
      badgeEl.className = "badge discharge";
      powerEl.style.setProperty("--thumb", "#2e7d32");
    } else {
      badgeEl.textContent = "off";
      badgeEl.className = "badge";
      powerEl.style.setProperty("--thumb", "#9e9e9e");
    }
  }

  _callTime(entityId, value) {
    if (!value) return;
    this._callService("time", "set_value", { entity_id: entityId, time: `${value}:00` });
  }

  _toggleDay(windowNumber, entityId, day) {
    const state = this._state(entityId);
    const active = new Set(
      state && state.state && state.state !== "none"
        ? state.state.split(",").map((d) => d.trim())
        : []
    );
    if (active.has(day)) active.delete(day);
    else active.add(day);
    const ordered = DAY_KEYS.filter((key) => active.has(key));
    this._callService("text", "set_value", {
      entity_id: entityId,
      value: ordered.length ? ordered.join(",") : "none",
    });
  }

  _press(entityId) {
    this._callService("button", "press", { entity_id: entityId });
  }

  _callService(domain, service, data) {
    if (!this._hass) return;
    this._hass.callService(domain, service, data).catch((err) => this._showError(err));
  }

  _showError(err) {
    const root = this.shadowRoot;
    if (!root) return;
    const el = root.querySelector("#error");
    if (!el) return;
    el.textContent = (err && err.message) || "Action failed";
    clearTimeout(this._errorTimer);
    this._errorTimer = setTimeout(() => {
      el.textContent = "";
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
  description: "Four Time-of-Use windows with charge/discharge sliders and a cheapest-hours button.",
  preview: false,
});

console.info("%c GOODWE-TOU-CARD %c v0.2.1 ", "background:#2e7d32;color:#fff;font-weight:bold", "background:#333;color:#fff");
