/*
 * goodwe-tou-log-card
 *
 * A read-only activity log for the GoodWe Time-of-Use integration: every
 * modification the integration sent to the inverter, newest first, with the
 * fields that changed, the windows that were written and whether the inverter
 * confirmed it (or why it did not).
 *
 * The log itself lives in the integration (persisted across restarts) and is
 * fetched through the `goodwe_tou.get_activity` service, so the card can show
 * more entries than fit in a state attribute. It refreshes whenever the
 * activity sensor changes.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-log-card
 *   title: Logboek                            # optional; " (Version x.y.z)" is appended
 *   prefix: goodwe_time_of_use                # optional entity-id prefix
 *   sensor: sensor.goodwe_time_of_use_activity_log   # optional, refresh trigger
 *   limit: 200                                # optional, entries fetched
 */

const CARD_VERSION = "0.5.0";
const PREFIX_DEFAULT = "goodwe_time_of_use";
const LIMIT_DEFAULT = 200;

const ACTION_LABELS = {
  apply: "Toegepast",
  set_windows: "Plan toegepast",
  cheapest_hours: "Optimale prijzen",
  window_time: "Tijd gewijzigd",
  window_power: "Vermogen gewijzigd",
  window_days: "Dagen gewijzigd",
  eco: "ECO-modus",
};

const FIELD_LABELS = {
  start: "start",
  stop: "stop",
  power: "vermogen",
  days: "dagen",
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

const formatPower = (value) => {
  const n = Number(value) || 0;
  if (n < 0) return `${n}% laden`;
  if (n > 0) return `+${n}% ontladen`;
  return "0% uit";
};

const formatDays = (text) => {
  const value = String(text || "").toLowerCase();
  if (!value || value === "none" || value === "off") return "geen";
  if (value.includes("all") || value.includes("every")) return "elke dag";
  if (value.includes("weekday")) return "ma\u2013vr";
  if (value.includes("weekend")) return "za\u2013zo";
  return String(text);
};

const fieldLabel = (field) => {
  const match = /^window_(\d+)_(\w+)$/.exec(String(field || ""));
  if (match) return `Venster ${match[1]} \u00b7 ${FIELD_LABELS[match[2]] || match[2]}`;
  if (field === "eco_mode") return "ECO-modus";
  return String(field || "");
};

const formatValue = (field, value) => {
  if (value === null || value === undefined) return "\u2013";
  if (/_power$/.test(field)) return formatPower(value);
  if (/_days$/.test(field)) return formatDays(value);
  if (field === "eco_mode") {
    if (value === "eco") return "ECO";
    if (value === "general") return "algemeen";
  }
  return String(value);
};

const touchesWindow = (entry, number) => {
  const prefix = `window_${number}_`;
  if ((entry.changes || []).some((c) => String(c.field || "").startsWith(prefix))) {
    return true;
  }
  return (entry.windows || []).some((w) => Number(w.n) === number);
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; gap: 8px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .count { font-size: .78rem; color: var(--secondary-text-color); }

  .filters { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 10px; }
  .filter { display: flex; align-items: center; gap: 6px; }
  .filter .flabel { font-size: .74rem; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg button { border: none; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .8rem; padding: 4px 10px; cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--divider-color); }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }

  .list { display: flex; flex-direction: column; gap: 6px; max-height: 420px; overflow-y: auto; }
  .entry { border: 1px solid var(--divider-color); border-left: 3px solid var(--secondary-text-color); border-radius: 8px; padding: 7px 9px; background: var(--card-background-color); }
  .entry.ok { border-left-color: #2e7d32; }
  .entry.error { border-left-color: #c62828; }
  .erow { display: flex; align-items: baseline; gap: 8px; }
  .etime { font-variant-numeric: tabular-nums; font-size: .82rem; color: var(--secondary-text-color); min-width: 74px; }
  .eaction { font-size: .88rem; font-weight: 600; color: var(--primary-text-color); }
  .ebadge { margin-left: auto; font-size: .7rem; padding: 1px 8px; border-radius: 9px; }
  .ebadge.ok { background: rgba(46,125,50,.16); color: #2e7d32; }
  .ebadge.error { background: rgba(198,40,40,.16); color: #c62828; }
  .changes { margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }
  .change { font-size: .78rem; color: var(--secondary-text-color); }
  .change .f { color: var(--primary-text-color); }
  .change .old { text-decoration: line-through; opacity: .7; }
  .change .arrow { margin: 0 4px; }
  .change .new { color: var(--primary-color); font-weight: 600; }
  .wins { margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }
  .win { font-size: .78rem; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .win .wlabel { color: var(--primary-text-color); }
  .error-text { margin-top: 4px; font-size: .76rem; color: var(--error-color, #d32f2f); }
  .empty { font-size: .82rem; color: var(--secondary-text-color); padding: 10px 0; }
  .note { font-size: .74rem; color: var(--secondary-text-color); margin-top: 6px; }
`;

class GoodweTouLogCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._entries = [];
    this._signal = undefined;
    this._result = "all";
    this._window = 0;
    this._error = "";
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = this._config.prefix || PREFIX_DEFAULT;
    this._title = this._config.title || "Logboek";
    this._sensor = this._config.sensor || `sensor.${this._prefix}_activity_log`;
    this._limit =
      Number(this._config.limit) > 0 ? Number(this._config.limit) : LIMIT_DEFAULT;
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._built) return;
    this._refreshIfChanged();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 10;
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
        <div class="filters">
          <div class="filter">
            <span class="flabel">Resultaat</span>
            <span class="seg" id="resultseg"></span>
          </div>
          <div class="filter">
            <span class="flabel">Venster</span>
            <span class="seg" id="windowseg"></span>
          </div>
        </div>
        <div class="list" id="list"></div>
        <div class="note" id="note"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${this._title} (Version ${CARD_VERSION})`;
    this._countEl = root.querySelector("#count");
    this._listEl = root.querySelector("#list");
    this._noteEl = root.querySelector("#note");

    const resultSeg = root.querySelector("#resultseg");
    this._resultBtns = [];
    for (const [value, label] of [
      ["all", "Alles"],
      ["ok", "Gelukt"],
      ["error", "Mislukt"],
    ]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.value = value;
      button.addEventListener("click", () => {
        this._result = value;
        this._render();
      });
      resultSeg.appendChild(button);
      this._resultBtns.push(button);
    }

    const windowSeg = root.querySelector("#windowseg");
    this._windowBtns = [];
    for (const value of [0, 1, 2, 3, 4]) {
      const button = document.createElement("button");
      button.textContent = value === 0 ? "Alle" : String(value);
      button.dataset.value = String(value);
      button.addEventListener("click", () => {
        this._window = value;
        this._render();
      });
      windowSeg.appendChild(button);
      this._windowBtns.push(button);
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
        "get_activity",
        { limit: this._limit }
      );
      this._entries = (response && response.entries) || [];
      this._error = "";
    } catch (err) {
      this._entries = [];
      this._error = (err && err.message) || "Kon het logboek niet laden";
    }
    this._render();
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

  _matches(entry) {
    if (this._result !== "all" && entry.result !== this._result) return false;
    if (this._window && !touchesWindow(entry, this._window)) return false;
    return true;
  }

  // -- rendering ---------------------------------------------------------

  _render() {
    if (!this._built) return;

    for (const button of this._resultBtns) {
      button.classList.toggle("on", button.dataset.value === this._result);
    }
    for (const button of this._windowBtns) {
      button.classList.toggle("on", Number(button.dataset.value) === this._window);
    }

    const entries = this._entries.filter((entry) => this._matches(entry));
    this._countEl.textContent =
      entries.length === this._entries.length
        ? `${this._entries.length} wijzigingen`
        : `${entries.length} van ${this._entries.length} wijzigingen`;

    this._listEl.innerHTML = "";
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      if (this._error) {
        empty.textContent = this._error;
      } else if (this._entries.length) {
        empty.textContent = "Geen wijzigingen voor deze filters.";
      } else {
        empty.textContent = "Nog geen wijzigingen naar de omvormer gestuurd.";
      }
      this._listEl.appendChild(empty);
    }
    for (const entry of entries) {
      this._listEl.appendChild(this._renderEntry(entry));
    }

    if (this._noteEl) {
      this._noteEl.textContent = this._error
        ? ""
        : `Nieuwste ${this._limit} wijzigingen uit het logboek van de integratie.`;
    }
  }

  _renderEntry(entry) {
    const row = document.createElement("div");
    row.className = `entry ${entry.result === "error" ? "error" : "ok"}`;

    const head = document.createElement("div");
    head.className = "erow";

    const time = document.createElement("span");
    time.className = "etime";
    time.textContent = formatTime(entry.time);
    head.appendChild(time);

    const action = document.createElement("span");
    action.className = "eaction";
    action.textContent = ACTION_LABELS[entry.action] || entry.action || "Wijziging";
    head.appendChild(action);

    const badge = document.createElement("span");
    badge.className = `ebadge ${entry.result === "error" ? "error" : "ok"}`;
    badge.textContent = entry.result === "error" ? "mislukt" : "gelukt";
    head.appendChild(badge);

    row.appendChild(head);

    const changes = entry.changes || [];
    if (changes.length) {
      const box = document.createElement("div");
      box.className = "changes";
      for (const change of changes) {
        box.appendChild(this._renderChange(change));
      }
      row.appendChild(box);
    }

    const windows = entry.windows || [];
    const active = windows.filter((window) => Number(window.power) !== 0);
    if (active.length) {
      const box = document.createElement("div");
      box.className = "wins";
      for (const window of active) {
        const line = document.createElement("div");
        line.className = "win";
        const label = document.createElement("span");
        label.className = "wlabel";
        label.textContent = `Venster ${window.n}: `;
        line.appendChild(label);
        line.appendChild(
          document.createTextNode(
            `${window.start}\u2013${window.stop} \u00b7 ${formatPower(window.power)} \u00b7 ${formatDays(window.days)}`
          )
        );
        box.appendChild(line);
      }
      row.appendChild(box);
    } else if (windows.length && !changes.length) {
      const box = document.createElement("div");
      box.className = "wins";
      const line = document.createElement("div");
      line.className = "win";
      line.textContent = "Alle vensters uit";
      box.appendChild(line);
      row.appendChild(box);
    }

    if (entry.error) {
      const error = document.createElement("div");
      error.className = "error-text";
      error.textContent = entry.error;
      row.appendChild(error);
    }

    return row;
  }

  _renderChange(change) {
    const line = document.createElement("div");
    line.className = "change";

    const field = document.createElement("span");
    field.className = "f";
    field.textContent = `${fieldLabel(change.field)}: `;
    line.appendChild(field);

    const old = document.createElement("span");
    old.className = "old";
    old.textContent = formatValue(change.field, change.old);
    line.appendChild(old);

    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "\u2192";
    line.appendChild(arrow);

    const next = document.createElement("span");
    next.className = "new";
    next.textContent = formatValue(change.field, change.new);
    line.appendChild(next);

    return line;
  }
}

if (!customElements.get("goodwe-tou-log-card")) {
  customElements.define("goodwe-tou-log-card", GoodweTouLogCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goodwe-tou-log-card",
  name: "GoodWe Time-of-Use log",
  description:
    "An activity log of the modifications sent to the inverter, with filters for window and result.",
  preview: false,
});

console.info(`%c GOODWE-TOU-LOG-CARD %c v${CARD_VERSION} `, "background:#2e7d32;color:#fff;font-weight:bold", "background:#333;color:#fff");
