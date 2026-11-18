/*
 * goodwe-tou-power-card
 *
 * A compact, live "energy display" for the GoodWe Time-of-Use integration,
 * styled after the HomeWizard Energy Display:
 *
 *   - one big circle whose ring is the battery state of charge, with the
 *     current grid power in the middle. The circle is purple while the house
 *     draws from the grid (afname) and green while it feeds back
 *     (teruglevering);
 *   - two smaller circles next to it: the lowest/highest battery cell
 *     temperature and the lowest/highest cell voltage.
 *
 * The big circle is 50% larger than the two smaller ones. The card is
 * read-only.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-power-card
 *   title: GoodWe vermogen                  # optional; " (Version x.y.z)" is appended
 *   grid_entity: sensor.meter_active_power_total
 *   invert_grid: false                      # see "Grid sign" below
 *   soc_entity: sensor.battery_state_of_charge
 *   min_temp_entity: sensor.battery_min_cell_temperature
 *   max_temp_entity: sensor.battery_max_cell_temperature
 *   min_volt_entity: sensor.battery_min_cell_voltage
 *   max_volt_entity: sensor.battery_max_cell_voltage
 *   language: nl                            # optional, overrides the language
 *
 * Grid sign: the GoodWe meter reports a *negative* value while the house draws
 * from the grid (``sensor.meter_active_power_total``). Set ``invert_grid: true``
 * for a meter that reports consumption as positive, such as a P1/DSMR meter
 * (``sensor.p1_meter_power``).
 */

const CARD_VERSION = "1.8.0";
const SVG_NS = "http://www.w3.org/2000/svg";

const GRID_DEFAULT = "sensor.meter_active_power_total";
const SOC_DEFAULT = "sensor.battery_state_of_charge";
const MIN_TEMP_DEFAULT = "sensor.battery_min_cell_temperature";
const MAX_TEMP_DEFAULT = "sensor.battery_max_cell_temperature";
const MIN_VOLT_DEFAULT = "sensor.battery_min_cell_voltage";
const MAX_VOLT_DEFAULT = "sensor.battery_max_cell_voltage";

// The small circles are 108 px; the big one is 50% larger.
const SMALL = 108;
const BIG = 162;

// Drawing from the grid is purple, feeding back is green, like the HomeWizard
// Energy Display.
const COLOR_IMPORT = "#7c4dff";
const COLOR_EXPORT = "#22c55e";
const COLOR_IDLE = "var(--secondary-text-color)";
// Below this many watts the grid is treated as "in balance".
const IDLE_WATTS = 25;

// Everything the card says, per language. A value may be a function when it
// takes an argument.
const LANGS = {
  nl: {
    title: "GoodWe vermogen",
    import: "afname",
    export: "teruglevering",
    idle: "in balans",
    soc: "SOC",
    cellTemp: "Cel-temp",
    cellVolt: "Celspanning",
    noData: "geen gegevens",
  },
  en: {
    title: "GoodWe power",
    import: "import",
    export: "export",
    idle: "balanced",
    soc: "SOC",
    cellTemp: "Cell temp",
    cellVolt: "Cell voltage",
    noData: "no data",
  },
};

// Map any language tag (nl, nl-NL, en-GB, ...) onto a language we ship.
const resolveLangCode = (value) => {
  const code = String(value || "").toLowerCase();
  if (code.startsWith("en")) return "en";
  return "nl";
};

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

const svgEl = (tag, attrs) => {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    el.setAttribute(key, String(value));
  }
  return el;
};

const STYLES = `
  :host { display: block; }
  ha-card { padding: 14px 16px 16px; }
  .head { margin-bottom: 4px; }
  .title { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
  .row { display: flex; align-items: center; justify-content: center; gap: 14px; flex-wrap: wrap; }
  svg.circle { display: block; overflow: visible; }
  .cap { fill: var(--secondary-text-color); letter-spacing: .05em; text-transform: uppercase; }
  .val { fill: var(--primary-text-color); font-weight: 700; }
  .unit { fill: var(--secondary-text-color); }
  .big-num { fill: var(--primary-text-color); font-weight: 800; }
  .dir { font-weight: 700; }
  .soc { fill: var(--secondary-text-color); }
`;

class GoodweTouPowerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._lang = "nl";
    this._langOverride = null;
    this._signature = undefined;
  }

  setConfig(config) {
    this._config = config || {};
    this._langOverride = this._config.language || null;
    this._title = this._config.title || null;
    this._gridEntity = this._config.grid_entity || GRID_DEFAULT;
    this._invertGrid = this._config.invert_grid === true;
    this._socEntity = this._config.soc_entity || SOC_DEFAULT;
    this._minTempEntity = this._config.min_temp_entity || MIN_TEMP_DEFAULT;
    this._maxTempEntity = this._config.max_temp_entity || MAX_TEMP_DEFAULT;
    this._minVoltEntity = this._config.min_volt_entity || MIN_VOLT_DEFAULT;
    this._maxVoltEntity = this._config.max_volt_entity || MAX_VOLT_DEFAULT;
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
    this._renderIfChanged();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 3;
  }

  // -- language ----------------------------------------------------------

  // Card `language:` override, else the language the integration speaks (on the
  // activity sensor), else Home Assistant's own language, else Dutch.
  _resolveLang(hass) {
    if (this._langOverride) return resolveLangCode(this._langOverride);
    const activity = hass && hass.states
      ? hass.states[`sensor.goodwe_time_of_use_activity_log`]
      : undefined;
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

  // -- state -------------------------------------------------------------

  _num(entityId) {
    const state = this._hass && this._hass.states ? this._hass.states[entityId] : undefined;
    if (!state) return null;
    const value = Number(state.state);
    return Number.isFinite(value) ? value : null;
  }

  // The grid power, normalised so that "drawing from the grid" is positive.
  _gridInfo() {
    const raw = this._num(this._gridEntity);
    if (raw === null) return { watts: null, direction: "unknown" };
    const draw = this._invertGrid ? raw : -raw;
    if (Math.abs(raw) < IDLE_WATTS) return { watts: 0, direction: "idle" };
    return { watts: Math.abs(draw), direction: draw > 0 ? "import" : "export" };
  }

  _color(direction) {
    if (direction === "import") return COLOR_IMPORT;
    if (direction === "export") return COLOR_EXPORT;
    return COLOR_IDLE;
  }

  _formatPower(watts) {
    const value = Math.abs(watts);
    if (value >= 1000) return { value: (value / 1000).toFixed(2), unit: "kW" };
    return { value: String(Math.round(value)), unit: "W" };
  }

  // -- construction ------------------------------------------------------

  _build() {
    if (!this._hass || !this.shadowRoot) return;
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="head"><div class="title"></div></div>
        <div class="row" id="row"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = `${
      this._title || this._t("title")
    } (Version ${CARD_VERSION})`;
    this._rowEl = root.querySelector("#row");
    this._built = true;
    this._signature = undefined;
    this._renderIfChanged();
  }

  // -- rendering ---------------------------------------------------------

  _renderIfChanged() {
    const signature = [
      this._num(this._gridEntity),
      this._num(this._socEntity),
      this._num(this._minTempEntity),
      this._num(this._maxTempEntity),
      this._num(this._minVoltEntity),
      this._num(this._maxVoltEntity),
      this._lang,
    ].join("|");
    if (signature === this._signature) return;
    this._signature = signature;
    this._render();
  }

  _render() {
    if (!this._rowEl) return;
    this._rowEl.innerHTML = "";

    const grid = this._gridInfo();
    this._rowEl.appendChild(this._renderBig(grid));

    const minTemp = this._num(this._minTempEntity);
    const maxTemp = this._num(this._maxTempEntity);
    this._rowEl.appendChild(
      this._renderSmall(
        this._t("cellTemp"),
        minTemp === null ? "\u2013" : String(Math.round(minTemp)),
        maxTemp === null ? "\u2013" : String(Math.round(maxTemp)),
        "\u00b0C"
      )
    );

    const minVolt = this._num(this._minVoltEntity);
    const maxVolt = this._num(this._maxVoltEntity);
    this._rowEl.appendChild(
      this._renderSmall(
        this._t("cellVolt"),
        minVolt === null ? "\u2013" : minVolt.toFixed(2),
        maxVolt === null ? "\u2013" : maxVolt.toFixed(2),
        "V"
      )
    );
  }

  _renderBig(grid) {
    const d = BIG;
    const cx = d / 2;
    const cy = d / 2;
    const stroke = 11;
    const r = cx - stroke / 2 - 2;
    const circumference = 2 * Math.PI * r;
    const soc = this._num(this._socEntity);
    const socPct = soc === null ? 0 : clamp(soc, 0, 100);
    const color = this._color(grid.direction);

    const el = svgEl("svg", {
      class: "circle",
      width: d,
      height: d,
      viewBox: `0 0 ${d} ${d}`,
    });

    // Track + SOC arc.
    el.appendChild(
      svgEl("circle", {
        cx, cy, r, fill: "none",
        stroke: "var(--divider-color)",
        "stroke-width": stroke,
        opacity: 0.45,
      })
    );
    el.appendChild(
      svgEl("circle", {
        cx, cy, r, fill: "none",
        stroke: color,
        "stroke-width": stroke,
        "stroke-linecap": "round",
        "stroke-dasharray": `${((circumference * socPct) / 100).toFixed(1)} ${circumference.toFixed(1)}`,
        transform: `rotate(-90 ${cx} ${cy})`,
      })
    );

    // SOC label at the top, inside the ring.
    if (soc !== null) {
      const socText = svgEl("text", {
        x: cx, y: cy - r + 22, "text-anchor": "middle",
        class: "soc", "font-size": "11",
      });
      socText.textContent = `${this._t("soc")} ${Math.round(soc)}%`;
      el.appendChild(socText);
    }

    // The current grid power in the middle.
    if (grid.watts === null) {
      const none = svgEl("text", {
        x: cx, y: cy + 6, "text-anchor": "middle",
        class: "unit", "font-size": "16",
      });
      none.textContent = this._t("noData");
      el.appendChild(none);
    } else {
      const power = this._formatPower(grid.watts);
      const num = svgEl("text", {
        x: cx, y: cy + 2, "text-anchor": "middle",
        class: "big-num", "font-size": "30",
      });
      num.textContent = power.value;
      el.appendChild(num);

      const unit = svgEl("text", {
        x: cx, y: cy + 22, "text-anchor": "middle",
        class: "unit", "font-size": "13",
      });
      unit.textContent = power.unit;
      el.appendChild(unit);
    }

    // The direction, in the circle's colour. Hidden when we have no reading.
    if (grid.direction !== "unknown") {
      const dirLabel =
        grid.direction === "import" ? this._t("import")
        : grid.direction === "export" ? this._t("export")
        : this._t("idle");
      const dir = svgEl("text", {
        x: cx, y: cy + 44, "text-anchor": "middle",
        class: "dir", "font-size": "12", fill: color,
      });
      dir.textContent = dirLabel;
      el.appendChild(dir);
    }

    return el;
  }

  _renderSmall(caption, minText, maxText, unit) {
    const d = SMALL;
    const cx = d / 2;
    const cy = d / 2;
    const r = cx - 3;

    const el = svgEl("svg", {
      class: "circle",
      width: d,
      height: d,
      viewBox: `0 0 ${d} ${d}`,
    });
    el.appendChild(
      svgEl("circle", {
        cx, cy, r, fill: "none",
        stroke: "var(--divider-color)",
        "stroke-width": 2,
        opacity: 0.7,
      })
    );

    const cap = svgEl("text", {
      x: cx, y: cy - 22, "text-anchor": "middle",
      class: "cap", "font-size": "9",
    });
    cap.textContent = caption;
    el.appendChild(cap);

    const val = svgEl("text", {
      x: cx, y: cy + 6, "text-anchor": "middle",
      class: "val", "font-size": "15",
    });
    val.textContent = `${minText}\u2013${maxText}`;
    el.appendChild(val);

    const u = svgEl("text", {
      x: cx, y: cy + 24, "text-anchor": "middle",
      class: "unit", "font-size": "10",
    });
    u.textContent = unit;
    el.appendChild(u);

    return el;
  }
}

if (!customElements.get("goodwe-tou-power-card")) {
  customElements.define("goodwe-tou-power-card", GoodweTouPowerCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "goodwe-tou-power-card",
  name: "GoodWe power",
  description:
    "A live HomeWizard-style widget: battery SOC ring with the grid power (purple when drawing, green when feeding back) and the battery's min/max cell temperature and voltage.",
  preview: false,
});

console.info(`%c GOODWE-TOU-POWER-CARD %c v${CARD_VERSION} `, "background:#7c4dff;color:#fff;font-weight:bold", "background:#333;color:#fff");
