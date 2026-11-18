/*
 * goodwe-tou-cost-card
 *
 * A small, live cost card for the GoodWe Time-of-Use integration.
 *
 * It shows two numbers:
 *
 *   - what the electricity used today has cost so far. The P1 meter is the only
 *     source for import and export: its lifetime totals (or the high/low
 *     tariff counters) are read from Home Assistant's long-term statistics, so
 *     the card also works when it was not open since midnight. The kWh are
 *     spread over the elapsed quarter hours with the measured consumption
 *     profile (the same shape the planner uses), so every quarter hour is
 *     priced at its own all-in price (Tibber forecast, else wholesale plus the
 *     measured taxes/fees);
 *   - the expected total for today: the cost so far plus the predicted cost of
 *     the remaining quarter hours. The prediction starts from the measured
 *     import/export profile and is scaled to the energy actually used so far.
 *     The four Time-of-Use windows are added on top, so a charge window costs
 *     its grid energy (plus the battery wear adder) and a discharge window
 *     earns the export price.
 *
 * The planner card sends a `goodwe-tou-windows-changed` event on every edit of
 * one of the four windows. The cost card compares the predicted cost of the
 * old and the new schedule and shows a green check when the change is cheaper,
 * or a red cross when it is more expensive. Without a change there is no
 * symbol.
 *
 * Ships with the integration and is loaded automatically. Configure with:
 *
 *   type: custom:goodwe-tou-cost-card
 *   title: Elektriciteitskosten                   # optional
 *   language: nl                                  # optional, overrides the language
 *   prefix: goodwe_time_of_use                    # optional entity-id prefix
 *   import_entity: sensor.p1_meter_energy_import
 *   export_entity: sensor.p1_meter_energy_export
 *   price_entity: sensor.average_electricity_price       # wholesale market
 *   all_in_price_entity: sensor.goodwe_time_of_use_stroomprijzen
 *   all_in_average_entity: sensor.akkerv5_electricity_price
 *   import_price_adder: 0.16                            # €/kWh taxes, optional
 *   profile_entity: sensor.goodwe_time_of_use_verbruiksprofiel
 *   soc_entity: sensor.battery_state_of_charge
 *   capacity_kwh: 24
 *   min_soc: 20
 *   charge_kw: 6
 *   discharge_kw: 6
 *   battery_cost: 0.05                            # €/kWh added to grid charging
 *   rte: 0.85                                     # round-trip efficiency
 *   slot_minutes: 15                              # price/profile granularity
 *   battery_power_entity: sensor.battery_power    # + discharging, - charging
 *   grid_power_entity: sensor.meter_active_power_total
 *   invert_grid: false                            # true flips the grid sign
 *   power_idle_w: 25                              # below this the icon is grey
 */

const CARD_VERSION = "1.12.0";

const PREFIX_DEFAULT = "goodwe_time_of_use";
const PRICE_DEFAULT = "sensor.average_electricity_price";
const PROFILE_DEFAULT = "sensor.goodwe_time_of_use_verbruiksprofiel";
const IMPORT_DEFAULT = "sensor.p1_meter_energy_import";
const EXPORT_DEFAULT = "sensor.p1_meter_energy_export";
const SOC_DEFAULT = "sensor.battery_state_of_charge";
//: The high/low tariff children some P1 integrations expose next to the total.
const TARIFF_SUFFIXES = ["tariff_1", "tariff_2"];
const BATTERY_POWER_DEFAULT = "sensor.battery_power";
const GRID_POWER_DEFAULT = "sensor.meter_active_power_total";

const MAX_WINDOWS = 4;
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// The energy refresh is cheap enough to repeat, but there is no point in
// asking the recorder on every state update of the house.
const ENERGY_REFRESH_MS = 60_000;

// A change smaller than this is rounding noise, not a better/worse schedule.
const TREND_EPSILON = 0.005;

const LANGS = {
  nl: {
    title: "Elektriciteitskosten",
    used: "Nu al gebruikt",
    total: "Verwacht totaal vandaag",
    remaining: "nog {value} verwacht",
    energy: "{import} kWh in · {export} kWh uit",
    better: "{delta} goedkoper",
    worse: "{delta} duurder",
    price: "Huidige prijs {price}/kWh",
    avgRest: "gemiddeld rest {price}/kWh",
    taxes: "incl. {value}/kWh heffingen",
    noData: "geen gegevens",
    loading: "kosten laden…",
    trendTitle: "Effect van de laatste wijziging in de vensters",
    battery: "Batterij",
    grid: "Net",
    batCharge: "laden",
    batDischarge: "ontladen",
    batIdle: "rust",
    gridImport: "afname",
    gridExport: "teruglevering",
    gridIdle: "rust",
    batteryTitle: "Batterijvermogen: rood = laden, groen = ontladen, grijs = rust",
    gridTitle: "Netvermogen: paars = afname, blauw = teruglevering, grijs = rust",
  },
  en: {
    title: "Electricity cost",
    used: "Used so far",
    total: "Expected total today",
    remaining: "{value} still expected",
    energy: "{import} kWh in · {export} kWh out",
    better: "{delta} cheaper",
    worse: "{delta} more expensive",
    price: "Current price {price}/kWh",
    avgRest: "average rest {price}/kWh",
    taxes: "incl. {value}/kWh taxes & fees",
    noData: "no data",
    loading: "loading cost…",
    trendTitle: "Effect of the last change to the windows",
    battery: "Battery",
    grid: "Grid",
    batCharge: "charging",
    batDischarge: "discharging",
    batIdle: "idle",
    gridImport: "importing",
    gridExport: "exporting",
    gridIdle: "idle",
    batteryTitle: "Battery power: red = charging, green = discharging, grey = idle",
    gridTitle: "Grid power: purple = importing, blue = exporting, grey = idle",
  },
};

const resolveLangCode = (value) => {
  const code = String(value || "").toLowerCase();
  if (code.startsWith("en")) return "en";
  return "nl";
};

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

const pad = (value) => String(value).padStart(2, "0");

const localDateKey = (date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

const startOfDay = (date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate());

const addDays = (date, days) => {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
};

// Monday = 0 ... Sunday = 6.
const weekdayIndex = (date) => (date.getDay() + 6) % 7;

const minutesOfDay = (date) => date.getHours() * 60 + date.getMinutes();

const parseHM = (text) => {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(text || ""));
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 24 || minutes > 59) return null;
  return hours * 60 + minutes;
};

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
  ha-card { padding: 14px 16px 16px; }
  .head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .title { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
  .version { font-size: .7rem; color: var(--secondary-text-color); }
  .power { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--divider-color); }
  .powerblock { display: flex; flex-direction: column; align-items: center; text-align: center; min-width: 0; }
  .ico { width: 72px; height: 72px; display: block; }
  .ico.battery.idle, .ico.grid.idle { color: var(--disabled-text-color, #9e9e9e); }
  .ico.battery.charge { color: #dc2626; }
  .ico.battery.discharge { color: #16a34a; }
  .ico.grid.import { color: #9333ea; }
  .ico.grid.export { color: #2563eb; }
  .ptitle { margin-top: 4px; font-size: .78rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .pstate { font-size: .95rem; font-weight: 600; color: var(--secondary-text-color); }
  .pstate.charge { color: #dc2626; }
  .pstate.discharge { color: #16a34a; }
  .pstate.import { color: #9333ea; }
  .pstate.export { color: #2563eb; }
  .pkw { font-size: 1.05rem; font-weight: 700; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; }
  .metric { min-width: 0; }
  .label { font-size: .78rem; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .value { font-size: 1.75rem; line-height: 1.15; font-weight: 700; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .metric.total .value { color: var(--primary-color); }
  .sub { margin-top: 2px; font-size: .76rem; color: var(--secondary-text-color); }
  .status { display: none; align-items: center; gap: 8px; margin-top: 12px; padding: 8px 10px; border-radius: 8px; font-size: .86rem; font-weight: 600; }
  .status.show { display: flex; }
  .status.better { background: rgba(34, 197, 94, .12); color: #16a34a; }
  .status.worse { background: rgba(239, 68, 68, .12); color: #dc2626; }
  .status .icon { font-size: 1.15rem; line-height: 1; font-weight: 800; }
  .foot { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 10px; font-size: .74rem; color: var(--secondary-text-color); }
  @media (max-width: 420px) { .grid { grid-template-columns: 1fr; } }
`;

class GoodweTouCostCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._built = false;
    this._lang = "nl";
    this._langOverride = null;
    this._title = null;

    this._prefix = PREFIX_DEFAULT;
    this._priceEntity = PRICE_DEFAULT;
    this._allInPriceEntity = null;
    this._allInAverageEntity = null;
    this._importAdderOverride = null;
    this._profileEntity = PROFILE_DEFAULT;
    this._importEntity = IMPORT_DEFAULT;
    this._exportEntity = EXPORT_DEFAULT;
    this._socEntity = SOC_DEFAULT;
    this._batteryPowerEntity = BATTERY_POWER_DEFAULT;
    this._gridPowerEntity = GRID_POWER_DEFAULT;
    this._invertGrid = false;
    this._idleW = 25;

    this._capacityKwh = 24;
    this._minSoc = 20;
    this._chargeKw = 6;
    this._dischargeKw = 6;
    this._batteryCost = 0.05;
    this._rte = 0.85;
    this._slotMinutes = 15;

    this._prices = new Map();
    this._priceValues = [];
    this._priceTodayValues = [];
    this._priceSig = null;
    this._allInPrices = new Map();
    this._allInTodayValues = [];
    this._allInSig = null;
    this._allInAverage = null;
    this._profile = { load: [], import: [], export: [] };
    this._profileSig = null;

    this._energy = {
      importKwh: null,
      exportKwh: null,
      key: null,
      at: 0,
      source: null,
    };
    this._energyLoading = false;
    this._energyToken = 0;
    this._energyAttemptAt = 0;
    this._energyAttemptKey = null;
    this._energyTried = false;

    this._windows = [];
    this._referenceWindows = null;
    this._settingsKey = null;
    this._entityKey = null;
    this._liveWindows = null;
    this._trend = null;

    this._signature = null;
    this._onWindowsChanged = this._onWindowsChanged.bind(this);
  }

  connectedCallback() {
    window.addEventListener("goodwe-tou-windows-changed", this._onWindowsChanged);
  }

  disconnectedCallback() {
    window.removeEventListener("goodwe-tou-windows-changed", this._onWindowsChanged);
  }

  setConfig(config) {
    this._config = config || {};
    this._langOverride = this._config.language || null;
    this._title = this._config.title || null;
    this._prefix = this._config.prefix || PREFIX_DEFAULT;

    this._priceEntity = this._config.price_entity || PRICE_DEFAULT;
    // The integration's own Tibber sensor carries the all-in (tax included)
    // quarter-hour prices. Set `all_in_price_entity: false` to price only the
    // wholesale market.
    const allIn = this._config.all_in_price_entity;
    this._allInPriceEntity =
      allIn === false
        ? null
        : allIn || `sensor.${this._prefix}_stroomprijzen`;
    this._allInAverageEntity = this._config.all_in_average_entity || null;
    this._profileEntity = this._config.profile_entity || PROFILE_DEFAULT;
    this._importEntity = this._config.import_entity || IMPORT_DEFAULT;
    this._exportEntity = this._config.export_entity || EXPORT_DEFAULT;
    this._socEntity = this._config.soc_entity || SOC_DEFAULT;
    this._batteryPowerEntity =
      this._config.battery_power_entity || BATTERY_POWER_DEFAULT;
    this._gridPowerEntity = this._config.grid_power_entity || GRID_POWER_DEFAULT;
    this._invertGrid = this._config.invert_grid === true;

    const number = (value, fallback, low, high) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? clamp(parsed, low, high) : fallback;
    };
    this._capacityKwh = number(this._config.capacity_kwh, 24, 0.1, 500);
    this._minSoc = number(this._config.min_soc, 20, 0, 100);
    this._chargeKw = number(this._config.charge_kw, 6, 0, 100);
    this._dischargeKw = number(this._config.discharge_kw, 6, 0, 100);
    this._batteryCost = number(this._config.battery_cost, 0.05, 0, 1);
    this._rte = number(this._config.rte, 0.85, 0.1, 1);
    this._slotMinutes = number(this._config.slot_minutes, 15, 1, 60);
    this._idleW = number(this._config.power_idle_w, 25, 0, 5000);
    const adder = this._config.import_price_adder;
    this._importAdderOverride =
      adder === undefined || adder === null || adder === ""
        ? null
        : number(adder, 0, 0, 1);

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

    this._readPrices();
    this._readAllInPrices();
    this._readProfile();
    this._syncWindows();
    this._refreshEnergy();
    this._renderIfChanged();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 4;
  }

  // -- language ----------------------------------------------------------

  // Card `language:` override, else the language the integration speaks (on the
  // activity sensor), else Home Assistant's own language, else Dutch.
  _resolveLang(hass) {
    if (this._langOverride) return resolveLangCode(this._langOverride);
    const activity = hass && hass.states
      ? hass.states[`sensor.${this._prefix}_activity_log`]
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
    if (typeof value === "function") return value(...args);
    if (typeof value === "string" && args.length) {
      // The translations use named placeholders ({import}, {delta}, ...); the
      // call sites pass them in the order they appear.
      let index = 0;
      return value.replace(/\{(\w+)\}/g, () => {
        const arg = args[index++];
        return arg === undefined ? "" : String(arg);
      });
    }
    return value;
  }

  // -- state -------------------------------------------------------------

  _state(entityId) {
    return this._hass && this._hass.states ? this._hass.states[entityId] : undefined;
  }

  _num(entityId) {
    const state = this._state(entityId);
    if (!state) return null;
    const value = Number(state.state);
    return Number.isFinite(value) ? value : null;
  }

  _formatEuro(value) {
    const currency = this._lang === "en" ? "EUR" : "EUR";
    try {
      return new Intl.NumberFormat(this._lang === "en" ? "en-GB" : "nl-NL", {
        style: "currency",
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
    } catch (_err) {
      return `€ ${value.toFixed(2)}`;
    }
  }

  _formatKwh(value) {
    return `${value.toFixed(1)}`;
  }

  _formatKw(value) {
    if (value === null) return "\u2013";
    return `${(Math.abs(value) / 1000).toFixed(2)} kW`;
  }

  // Battery and grid direction, straight from the power sensors. The GoodWe
  // battery reports positive while discharging and negative while charging; the
  // meter reports positive while importing and negative while feeding back.
  _powerInfo() {
    const battery = this._num(this._batteryPowerEntity);
    let grid = this._num(this._gridPowerEntity);
    if (grid !== null && this._invertGrid) grid = -grid;

    let batteryKind = "idle";
    if (battery !== null && battery < -this._idleW) batteryKind = "charge";
    else if (battery !== null && battery > this._idleW) batteryKind = "discharge";

    let gridKind = "idle";
    if (grid !== null && grid > this._idleW) gridKind = "import";
    else if (grid !== null && grid < -this._idleW) gridKind = "export";

    return { battery, grid, batteryKind, gridKind };
  }

  // -- prices and profile ------------------------------------------------

  _readPrices() {
    const state = this._state(this._priceEntity);
    if (!state) return;
    const sig = state.last_updated || state.last_changed || "";
    if (sig === this._priceSig) return;
    this._priceSig = sig;

    const attrs = state.attributes || {};
    let list = Array.isArray(attrs.prices) ? attrs.prices : null;
    if (!list) {
      const today = Array.isArray(attrs.prices_today) ? attrs.prices_today : [];
      const tomorrow = Array.isArray(attrs.prices_tomorrow)
        ? attrs.prices_tomorrow
        : [];
      list = [...today, ...tomorrow];
    }

    const map = new Map();
    const values = [];
    const todayValues = [];
    const todayKey = localDateKey(new Date());
    for (const slot of list) {
      if (!slot || slot.time === undefined) continue;
      const when = new Date(slot.time);
      const price = Number(slot.price);
      if (!Number.isFinite(when.getTime()) || !Number.isFinite(price)) continue;
      const index = Math.floor(minutesOfDay(when) / this._slotMinutes);
      map.set(`${localDateKey(when)}|${index}`, price);
      values.push(price);
      if (localDateKey(when) === todayKey) todayValues.push(price);
    }
    this._prices = map;
    this._priceValues = values;
    this._priceTodayValues = todayValues;
  }

  // The Tibber prices from the integration are all-in (incl. energy tax and
  // VAT). When that forecast is available it prices every imported kWh exactly;
  // otherwise the wholesale prices get the measured taxes/fees adder.
  _readAllInPrices() {
    const state = this._allInPriceEntity
      ? this._state(this._allInPriceEntity)
      : undefined;
    if (state) {
      const sig = state.last_updated || state.last_changed || "";
      if (sig !== this._allInSig) {
        this._allInSig = sig;
        const attrs = state.attributes || {};
        let list = Array.isArray(attrs.prices) ? attrs.prices : null;
        if (!list) {
          const today = Array.isArray(attrs.prices_today) ? attrs.prices_today : [];
          const tomorrow = Array.isArray(attrs.prices_tomorrow)
            ? attrs.prices_tomorrow
            : [];
          list = [...today, ...tomorrow];
        }
        const map = new Map();
        const todayValues = [];
        const todayKey = localDateKey(new Date());
        for (const slot of list) {
          if (!slot || slot.time === undefined) continue;
          const when = new Date(slot.time);
          const price = Number(slot.price);
          if (!Number.isFinite(when.getTime()) || !Number.isFinite(price)) {
            continue;
          }
          const index = Math.floor(minutesOfDay(when) / this._slotMinutes);
          map.set(`${localDateKey(when)}|${index}`, price);
          if (localDateKey(when) === todayKey) todayValues.push(price);
        }
        this._allInPrices = map;
        this._allInTodayValues = todayValues;
      }
    } else {
      this._allInPrices = new Map();
      this._allInTodayValues = [];
    }

    // The Tibber price sensor also carries today's average all-in price. That
    // is the fallback adder while the quarter-hour list is still empty (for
    // example right after a restart, before the price coordinator has run).
    this._allInAverage = null;
    if (this._allInAverageEntity) {
      const averageState = this._state(this._allInAverageEntity);
      const attrs = averageState ? averageState.attributes || {} : {};
      const average = Number(attrs.avg_price);
      if (Number.isFinite(average)) this._allInAverage = average;
    }
  }

  _average(values) {
    if (!values || !values.length) return null;
    return values.reduce((a, b) => a + b, 0) / values.length;
  }

  // €/kWh between the all-in retail price and the wholesale market price. This
  // is what makes the card's euros match the Tibber app: the P1 kWh are real,
  // but they are bought at the all-in tariff, not at the bare market price.
  importAdder() {
    if (this._importAdderOverride !== null) return this._importAdderOverride;
    const market = this._average(this._priceTodayValues);
    if (market === null) return 0;
    const allIn = this._average(this._allInTodayValues);
    if (allIn !== null) return clamp(allIn - market, 0, 1);
    if (this._allInAverage !== null) {
      return clamp(this._allInAverage - market, 0, 1);
    }
    return 0;
  }

  _allInPriceAt(date) {
    const index = Math.floor(minutesOfDay(date) / this._slotMinutes);
    return this._allInPrices.get(`${localDateKey(date)}|${index}`);
  }

  _importPriceAt(date) {
    const allIn = this._allInPriceAt(date);
    if (allIn !== undefined) return allIn;
    return this._priceAt(date) + this.importAdder();
  }

  // Feed-in is paid at the wholesale price; the retail taxes and fees are not
  // earned back on exported kWh.
  _exportPriceAt(date) {
    return this._priceAt(date);
  }

  _readProfile() {
    const state = this._state(this._profileEntity);
    if (!state) return;
    const sig = state.last_updated || state.last_changed || "";
    if (sig === this._profileSig) return;
    this._profileSig = sig;
    const attrs = state.attributes || {};
    const clean = (values) =>
      Array.isArray(values) && values.length
        ? values.map((value) => {
            const number = Number(value);
            return Number.isFinite(number) && number >= 0 ? number : 0;
          })
        : [];
    this._profile = {
      load: clean(attrs.load),
      import: clean(attrs.import),
      export: clean(attrs.export),
    };
  }

  _priceAt(date) {
    const index = Math.floor(minutesOfDay(date) / this._slotMinutes);
    const exact = this._prices.get(`${localDateKey(date)}|${index}`);
    if (exact !== undefined) return exact;
    // A forecast that has rolled over to the next day still has a usable
    // average; better than pricing the rest of today at zero.
    if (!this._priceValues.length) return 0;
    return this._priceValues.reduce((a, b) => a + b, 0) / this._priceValues.length;
  }

  _profileIndex(date) {
    return Math.floor(minutesOfDay(date) / this._slotMinutes);
  }

  // -- the four windows --------------------------------------------------

  _readEntityWindows() {
    const windows = [];
    for (let number = 1; number <= MAX_WINDOWS; number++) {
      const base = `window_${number}`;
      const start = this._state(`time.${this._prefix}_${base}_start`);
      const stop = this._state(`time.${this._prefix}_${base}_stop`);
      const power = this._state(`number.${this._prefix}_${base}_power`);
      const days = this._state(`text.${this._prefix}_${base}_days`);
      const signed = power ? Number(power.state) : 0;
      const dayText = days ? days.state : "none";
      const enabled = Number.isFinite(signed) && signed !== 0 && expandDays(dayText).size > 0;
      windows.push(
        enabled
          ? {
              start: start ? start.state : "00:00:00",
              stop: stop ? stop.state : "00:00:00",
              power: signed,
              days: dayText,
            }
          : { start: "00:00:00", stop: "00:00:00", power: 0, days: "none" }
      );
    }
    return windows;
  }

  _windowsKey(windows) {
    return windows
      .map((window) => `${window.start}|${window.stop}|${window.power}|${window.days}`)
      .join(";");
  }

  _syncWindows() {
    const entityWindows = this._readEntityWindows();
    const entityKey = this._windowsKey(entityWindows);
    const externalChange = this._entityKey !== null && entityKey !== this._entityKey;

    if (externalChange) this._liveWindows = null;
    this._entityKey = entityKey;

    // While the planner card is being edited its windows are not yet written
    // to the entities; keep using the event's windows until the entities
    // themselves change.
    const windows =
      this._liveWindows && !externalChange ? this._liveWindows : entityWindows;
    this._setWindows(windows);
  }

  _setWindows(windows) {
    const key = this._windowsKey(windows);
    if (this._settingsKey === null) {
      this._windows = windows;
      this._referenceWindows = windows;
      this._settingsKey = key;
      return;
    }
    if (key === this._settingsKey) {
      this._windows = windows;
      return;
    }

    const now = new Date();
    const before = this._windowCost(this._windows, now);
    const after = this._windowCost(windows, now);
    const delta = after - before;
    if (delta < -TREND_EPSILON) {
      this._trend = { kind: "better", delta: Math.abs(delta) };
    } else if (delta > TREND_EPSILON) {
      this._trend = { kind: "worse", delta: Math.abs(delta) };
    } else {
      this._trend = null;
    }
    this._windows = windows;
    this._settingsKey = key;
  }

  _onWindowsChanged(event) {
    const windows = event && event.detail ? event.detail.windows : null;
    if (!Array.isArray(windows) || windows.length !== MAX_WINDOWS) return;
    this._liveWindows = windows.map((window) => ({
      start: String(window.start || "00:00:00"),
      stop: String(window.stop || "00:00:00"),
      power: Number(window.power) || 0,
      days: String(window.days || "none"),
    }));
    this._setWindows(this._liveWindows);
    this._renderIfChanged(true);
  }

  _windowActive(window, date, minutes) {
    if (!window || !window.power) return false;
    const start = parseHM(window.start);
    const stop = parseHM(window.stop);
    if (start === null || stop === null || start === stop) return false;
    const days = expandDays(window.days);
    const todayKey = DAY_KEYS[weekdayIndex(date)];
    if (start < stop) {
      return days.has(todayKey) && minutes >= start && minutes < stop;
    }
    // A window that crosses midnight belongs to the day it starts on.
    if (minutes >= start) return days.has(todayKey);
    return minutes < stop && days.has(DAY_KEYS[weekdayIndex(addDays(date, -1))]);
  }

  // Effect of the four windows on the remaining part of today: grid charging
  // costs its energy (plus battery wear), discharging earns the export price.
  _windowCost(windows, now) {
    const end = addDays(startOfDay(now), 1);
    const socStart = this._socKwh();
    let soc = socStart;
    let cost = 0;
    let cursor = new Date(now);

    while (cursor < end) {
      const slotStart = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate(),
        cursor.getHours(),
        Math.floor(cursor.getMinutes() / this._slotMinutes) * this._slotMinutes,
        0,
        0
      );
      const slotEnd = new Date(
        Math.min(
          addDays(startOfDay(now), 1).getTime(),
          slotStart.getTime() + this._slotMinutes * 60_000
        )
      );
      const from = cursor > slotStart ? cursor : slotStart;
      const hours = Math.max(0, slotEnd - from) / 3_600_000;
      if (hours <= 0) break;

      const minutes = minutesOfDay(from);
      const window = windows.find((candidate) =>
        this._windowActive(candidate, from, minutes)
      );
      if (window && window.power < 0) {
        const energy = this._chargeKw * (Math.abs(window.power) / 100) * hours;
        soc = Math.min(this._usableKwh(), soc + energy * this._rte);
        cost += energy * (this._importPriceAt(slotStart) + this._batteryCost);
      } else if (window && window.power > 0) {
        const wanted = this._dischargeKw * (window.power / 100) * hours;
        const actual = Math.min(wanted, soc);
        soc = Math.max(0, soc - actual);
        cost -= actual * this._exportPriceAt(slotStart);
      }

      cursor = slotEnd;
    }
    return cost;
  }

  _usableKwh() {
    return Math.max(0, ((100 - this._minSoc) / 100) * this._capacityKwh);
  }

  _socKwh() {
    const soc = this._num(this._socEntity);
    if (soc === null) return 0;
    return clamp(((soc - this._minSoc) / 100) * this._capacityKwh, 0, this._usableKwh());
  }

  // -- energy used today -------------------------------------------------

  _refreshEnergy() {
    const now = new Date();
    const key = localDateKey(now);
    const fresh =
      this._energy.importKwh !== null &&
      this._energy.key === key &&
      Date.now() - this._energy.at < ENERGY_REFRESH_MS;
    if (fresh || this._energyLoading || !this._hass) return;

    // A failed fetch (no recorder, no statistics yet) must not turn every state
    // update into a new request; the card tries again once a minute.
    const attempted =
      this._energyTried &&
      this._energyAttemptKey === key &&
      Date.now() - this._energyAttemptAt < ENERGY_REFRESH_MS;
    if (attempted) return;

    this._energyLoading = true;
    this._energyAttemptAt = Date.now();
    this._energyAttemptKey = key;
    this._energyTried = true;
    const token = ++this._energyToken;
    const midnight = startOfDay(now);

    const finish = () => {
      this._energyLoading = false;
      if (token === this._energyToken) this._renderIfChanged(true);
    };

    if (typeof this._hass.callWS !== "function") {
      finish();
      return;
    }

    const statisticIds = this._energyStatisticIds();
    if (!statisticIds.length) {
      finish();
      return;
    }

    this._hass
      .callWS({
        type: "recorder/statistics_during_period",
        start_time: midnight.toISOString(),
        end_time: now.toISOString(),
        statistic_ids: statisticIds,
        period: "day",
        types: ["change"],
      })
      .then((result) => {
        const importKwh = this._sideKwh(result, this._importEntity);
        const exportKwh = this._sideKwh(result, this._exportEntity);
        if (importKwh === null && exportKwh === null) {
          throw new Error("no P1 statistics");
        }
        this._energy = {
          importKwh: importKwh ?? 0,
          exportKwh: exportKwh ?? 0,
          key,
          at: Date.now(),
          source: "p1-statistics",
        };
      })
      .catch(() => this._energyFromP1Totals(key, now))
      .finally(finish);
  }

  // The P1 lifetime totals and their high/low tariff children are the only
  // valid source for import and export. Never fall back to the inverter's
  // "today" counters: those measure something else and made the cost far too
  // low.
  _energyStatisticIds() {
    const ids = [];
    for (const entity of [this._importEntity, this._exportEntity]) {
      if (!entity) continue;
      ids.push(entity);
      for (const suffix of TARIFF_SUFFIXES) {
        const child = `${entity}_${suffix}`;
        if (this._state(child)) ids.push(child);
      }
    }
    return Array.from(new Set(ids));
  }

  _sumChanges(result, entityId) {
    const rows = result ? result[entityId] : undefined;
    let sum = 0;
    let found = false;
    for (const row of Array.isArray(rows) ? rows : []) {
      const change = Number(row && row.change);
      if (Number.isFinite(change)) {
        sum += change;
        found = true;
      }
    }
    return found ? sum : null;
  }

  // A single side can be a total or the sum of its high/low tariffs, whichever
  // the P1 integration and the recorder provide.
  _sideKwh(result, entity) {
    const total = this._sumChanges(result, entity);
    if (total !== null) return total;
    let sum = 0;
    let found = false;
    for (const suffix of TARIFF_SUFFIXES) {
      const value = this._sumChanges(result, `${entity}_${suffix}`);
      if (value !== null) {
        sum += value;
        found = true;
      }
    }
    return found ? sum : null;
  }

  _lifetimeValue(map, entityId) {
    const rows = map ? map[entityId] : undefined;
    if (!Array.isArray(rows) || !rows.length) return null;
    const first = rows[0] || {};
    const value = Number(first.state !== undefined ? first.state : first.s);
    return Number.isFinite(value) ? value : null;
  }

  _lifetimeSide(map, entity) {
    const total = this._lifetimeValue(map, entity);
    if (total !== null) return total;
    let sum = 0;
    let found = false;
    for (const suffix of TARIFF_SUFFIXES) {
      const value = this._lifetimeValue(map, `${entity}_${suffix}`);
      if (value !== null) {
        sum += value;
        found = true;
      }
    }
    return found ? sum : null;
  }

  // Fallback when the statistics are unavailable: the current P1 lifetime
  // totals minus the totals at midnight, read from the history API. Still P1
  // only, so the number stays honest. When that also fails the card shows
  // "no data" instead of inventing a value.
  _energyFromP1Totals(key, now) {
    if (typeof this._hass.callWS !== "function") return Promise.resolve();
    // Same totals-or-tariffs logic as the statistics path, so a P1 integration
    // that only exposes the high/low counters also works here.
    const entityIds = this._energyStatisticIds();
    if (!entityIds.length) return Promise.resolve();
    const currentRows = {};
    for (const entity of entityIds) {
      const value = this._num(entity);
      if (value !== null) currentRows[entity] = [{ state: value }];
    }
    const currentSide = (entity) => this._lifetimeSide(currentRows, entity);
    if (
      currentSide(this._importEntity) === null &&
      currentSide(this._exportEntity) === null
    ) {
      return Promise.resolve();
    }

    const midnight = startOfDay(now);
    return this._hass
      .callWS({
        type: "history/history_during_period",
        start_time: midnight.toISOString(),
        end_time: now.toISOString(),
        entity_ids: entityIds,
        include_start_time_state: true,
        significant_changes_only: false,
        minimal_response: true,
        no_attributes: true,
      })
      .then((result) => {
        const today = (entity) => {
          const nowValue = currentSide(entity);
          const baseValue = this._lifetimeSide(result, entity);
          if (nowValue === null || baseValue === null) return null;
          return Math.max(0, nowValue - baseValue);
        };
        const importKwh = today(this._importEntity);
        const exportKwh = today(this._exportEntity);
        if (importKwh === null && exportKwh === null) return;
        this._energy = {
          importKwh: importKwh ?? 0,
          exportKwh: exportKwh ?? 0,
          key,
          at: Date.now(),
          source: "p1-history",
        };
      })
      .catch(() => {
        // Leave the energy empty; the card shows "no data" rather than a
        // number from a counter that is not the P1 meter.
      });
  }

  // -- cost model --------------------------------------------------------

  _elapsedProfileKwh(now, series) {
    if (!series.length) return 0;
    const midnight = startOfDay(now);
    let total = 0;
    let cursor = midnight;
    while (cursor < now) {
      const slotStart = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate(),
        cursor.getHours(),
        Math.floor(cursor.getMinutes() / this._slotMinutes) * this._slotMinutes,
        0,
        0
      );
      const slotEnd = new Date(slotStart.getTime() + this._slotMinutes * 60_000);
      const from = cursor > slotStart ? cursor : slotStart;
      const to = slotEnd < now ? slotEnd : now;
      const hours = Math.max(0, to - from) / 3_600_000;
      const index = this._profileIndex(slotStart);
      total += (series[index] ?? 0) * hours;
      cursor = to;
    }
    return total;
  }

  _usedCost(now, used) {
    if (!used || used.importKwh === null) return null;
    const midnight = startOfDay(now);
    const importSeries = this._profile.import;
    const exportSeries = this._profile.export;

    const importWeights = [];
    const exportWeights = [];
    let importWeight = 0;
    let exportWeight = 0;
    let cursor = midnight;
    while (cursor < now) {
      const slotStart = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate(),
        cursor.getHours(),
        Math.floor(cursor.getMinutes() / this._slotMinutes) * this._slotMinutes,
        0,
        0
      );
      const slotEnd = new Date(slotStart.getTime() + this._slotMinutes * 60_000);
      const from = cursor > slotStart ? cursor : slotStart;
      const to = slotEnd < now ? slotEnd : now;
      const hours = Math.max(0, to - from) / 3_600_000;
      const index = this._profileIndex(slotStart);
      const iw = importSeries.length ? (importSeries[index] ?? 0) * hours : hours;
      const ew = exportSeries.length ? (exportSeries[index] ?? 0) * hours : hours;
      importWeights.push({ at: slotStart, weight: iw });
      exportWeights.push({ at: slotStart, weight: ew });
      importWeight += iw;
      exportWeight += ew;
      cursor = to;
    }

    const distribute = (weights, totalWeight, energy) => {
      if (!weights.length) return new Map();
      const result = new Map();
      for (const entry of weights) {
        const share = totalWeight > 0 ? entry.weight / totalWeight : 1 / weights.length;
        result.set(entry.at, (result.get(entry.at) || 0) + energy * share);
      }
      return result;
    };

    const importBySlot = distribute(importWeights, importWeight, used.importKwh);
    const exportBySlot = distribute(exportWeights, exportWeight, used.exportKwh);

    let cost = 0;
    for (const [at, energy] of importBySlot) {
      cost += energy * this._importPriceAt(at);
    }
    for (const [at, energy] of exportBySlot) {
      cost -= energy * this._exportPriceAt(at);
    }
    return {
      cost,
      importKwh: used.importKwh,
      exportKwh: used.exportKwh,
    };
  }

  _profileRemainingCost(now, used) {
    const end = addDays(startOfDay(now), 1);
    const importSeries = this._profile.import;
    const exportSeries = this._profile.export;

    const profileImportSoFar = this._elapsedProfileKwh(now, importSeries);
    const profileExportSoFar = this._elapsedProfileKwh(now, exportSeries);
    const importFactor =
      used && used.importKwh !== null && profileImportSoFar > 0.05
        ? clamp(used.importKwh / profileImportSoFar, 0.25, 4)
        : 1;
    const exportFactor =
      used && used.exportKwh !== null && profileExportSoFar > 0.05
        ? clamp(used.exportKwh / profileExportSoFar, 0.25, 4)
        : 1;

    let cost = 0;
    let cursor = new Date(now);
    while (cursor < end) {
      const slotStart = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate(),
        cursor.getHours(),
        Math.floor(cursor.getMinutes() / this._slotMinutes) * this._slotMinutes,
        0,
        0
      );
      const slotEnd = new Date(
        Math.min(end.getTime(), slotStart.getTime() + this._slotMinutes * 60_000)
      );
      const from = cursor > slotStart ? cursor : slotStart;
      const hours = Math.max(0, slotEnd - from) / 3_600_000;
      if (hours <= 0) break;
      const index = this._profileIndex(slotStart);
      let importKwh;
      let exportKwh;
      if (importSeries.length || exportSeries.length) {
        importKwh = (importSeries[index] ?? 0) * hours * importFactor;
        exportKwh = (exportSeries[index] ?? 0) * hours * exportFactor;
      } else {
        importKwh = 0;
        exportKwh = 0;
      }
      cost +=
        importKwh * this._importPriceAt(slotStart) -
        exportKwh * this._exportPriceAt(slotStart);
      cursor = slotEnd;
    }
    return cost;
  }

  _compute() {
    const now = new Date();
    const used = this._usedCost(now, this._energy);
    const remaining = this._profileRemainingCost(now, this._energy);
    const windowsNow = this._windowCost(this._windows, now);
    const reference = this._referenceWindows
      ? this._windowCost(this._referenceWindows, now)
      : windowsNow;
    const predictedRemaining = remaining + (windowsNow - reference);
    const total = (used ? used.cost : 0) + predictedRemaining;

    const currentSlot = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
      now.getHours(),
      Math.floor(now.getMinutes() / this._slotMinutes) * this._slotMinutes,
      0,
      0
    );
    const currentPrice =
      this._prices.size || this._allInPrices.size
        ? this._importPriceAt(currentSlot)
        : null;
    const averagePrice = this._averageRestImportPrice(now);

    return {
      now,
      used,
      total,
      predictedRemaining,
      currentPrice,
      averagePrice,
      importAdder: this.importAdder(),
    };
  }

  _averageRestImportPrice(now) {
    const end = addDays(startOfDay(now), 1);
    let sum = 0;
    let count = 0;
    let cursor = new Date(now);
    while (cursor < end) {
      const slotStart = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate(),
        cursor.getHours(),
        Math.floor(cursor.getMinutes() / this._slotMinutes) * this._slotMinutes,
        0,
        0
      );
      sum += this._importPriceAt(slotStart);
      count += 1;
      const next = new Date(
        slotStart.getTime() + this._slotMinutes * 60_000
      );
      if (next <= cursor) break;
      cursor = next;
    }
    return count ? sum / count : null;
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
          <div class="version">v${CARD_VERSION}</div>
        </div>
        <div class="power">
          <div class="powerblock">
            <svg id="batIcon" class="ico battery idle" viewBox="0 0 44 44" aria-hidden="true">
              <rect x="8" y="4" width="24" height="36" rx="4" fill="none" stroke="currentColor" stroke-width="2.8"/>
              <rect x="36" y="15" width="4" height="14" rx="1.5" fill="currentColor"/>
              <rect id="batFill" x="11.5" y="7.5" width="17" height="29" rx="2" fill="currentColor" opacity=".18"/>
            </svg>
            <div class="ptitle" id="batLabel"></div>
            <div class="pstate" id="batState"></div>
            <div class="pkw" id="batKw"></div>
          </div>
          <div class="powerblock">
            <svg id="gridIcon" class="ico grid idle" viewBox="0 0 44 44" aria-hidden="true">
              <g fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 4 L15 11 L11 40"/>
                <path d="M22 4 L29 11 L33 40"/>
                <path d="M15 11 H29"/>
                <path d="M12 21 H32"/>
                <path d="M13 31 H31"/>
                <path d="M18 4 H26"/>
                <path d="M15.5 21 L28.5 31" stroke-width="1.4" opacity=".55"/>
                <path d="M28.5 21 L15.5 31" stroke-width="1.4" opacity=".55"/>
              </g>
            </svg>
            <div class="ptitle" id="gridLabel"></div>
            <div class="pstate" id="gridState"></div>
            <div class="pkw" id="gridKw"></div>
          </div>
        </div>
        <div class="grid">
          <div class="metric used">
            <div class="label" id="usedLabel"></div>
            <div class="value" id="usedValue">–</div>
            <div class="sub" id="usedSub"></div>
          </div>
          <div class="metric total">
            <div class="label" id="totalLabel"></div>
            <div class="value" id="totalValue">–</div>
            <div class="sub" id="totalSub"></div>
          </div>
        </div>
        <div class="status" id="status" role="status" aria-live="polite">
          <span class="icon" id="statusIcon" aria-hidden="true"></span>
          <span id="statusText"></span>
        </div>
        <div class="foot" id="foot"></div>
      </ha-card>
    `;
    root.querySelector(".title").textContent = this._title || this._t("title");
    root.querySelector("#usedLabel").textContent = this._t("used");
    root.querySelector("#totalLabel").textContent = this._t("total");
    root.querySelector("#batLabel").textContent = this._t("battery");
    root.querySelector("#gridLabel").textContent = this._t("grid");
    this._built = true;
    this._signature = null;
    this._renderIfChanged(true);
  }

  // -- rendering ---------------------------------------------------------

  _renderIfChanged(force = false) {
    if (!this._built || !this.shadowRoot) return;
    const signature = [
      this._lang,
      this._energy.key,
      this._energy.importKwh,
      this._energy.exportKwh,
      this._energy.at,
      this._energy.source,
      this._priceSig,
      this._allInSig,
      this._priceTodayValues.length,
      this.importAdder().toFixed(4),
      this._profileSig,
      this._windowsKey(this._windows),
      this._socKwh().toFixed(3),
      Math.round(this._num(this._batteryPowerEntity) ?? NaN) || 0,
      Math.round(this._num(this._gridPowerEntity) ?? NaN) || 0,
      this._trend ? `${this._trend.kind}:${this._trend.delta.toFixed(3)}` : "",
      Math.floor(Date.now() / 60_000),
    ].join("|");
    if (!force && signature === this._signature) return;
    this._signature = signature;
    this._render();
  }

  _render() {
    const root = this.shadowRoot;
    if (!root) return;
    const data = this._compute();
    this._renderPower(root);
    const usedValue = root.querySelector("#usedValue");
    const totalValue = root.querySelector("#totalValue");
    const usedSub = root.querySelector("#usedSub");
    const totalSub = root.querySelector("#totalSub");

    if (!data.used) {
      const pending = this._energyLoading || !this._energyTried;
      usedValue.textContent = this._t(pending ? "loading" : "noData");
      totalValue.textContent = this._t("noData");
      usedSub.textContent = "";
      totalSub.textContent = "";
    } else {
      usedValue.textContent = this._formatEuro(data.used.cost);
      totalValue.textContent = this._formatEuro(data.total);
      usedSub.textContent = this._t(
        "energy",
        this._formatKwh(data.used.importKwh),
        this._formatKwh(data.used.exportKwh)
      );
      totalSub.textContent = this._t(
        "remaining",
        this._formatEuro(Math.max(0, data.predictedRemaining))
      );
    }

    const status = root.querySelector("#status");
    const icon = root.querySelector("#statusIcon");
    const text = root.querySelector("#statusText");
    status.classList.remove("better", "worse", "show");
    if (this._trend) {
      const better = this._trend.kind === "better";
      status.classList.add("show", better ? "better" : "worse");
      icon.textContent = better ? "✓" : "✗";
      text.textContent = this._t(
        better ? "better" : "worse",
        this._formatEuro(this._trend.delta)
      );
      status.title = this._t("trendTitle");
    } else {
      icon.textContent = "";
      text.textContent = "";
      status.removeAttribute("title");
    }

    const foot = root.querySelector("#foot");
    const parts = [];
    if (data.currentPrice !== null) {
      parts.push(this._t("price", this._formatEuro(data.currentPrice)));
    }
    if (data.averagePrice !== null) {
      parts.push(this._t("avgRest", this._formatEuro(data.averagePrice)));
    }
    if (data.importAdder > 0.0005) {
      parts.push(this._t("taxes", this._formatEuro(data.importAdder)));
    }
    foot.innerHTML = parts
      .map((part) => `<span>${this._escape(part)}</span>`)
      .join("");
  }

  // The two large status symbols: colour tells the direction, the text says it
  // in words and the number is the power flowing right now.
  _renderPower(root) {
    const power = this._powerInfo();
    const blocks = [
      {
        id: "bat",
        value: power.battery,
        kind: power.batteryKind,
        title: this._t("batteryTitle"),
        labels: {
          charge: "batCharge",
          discharge: "batDischarge",
          idle: "batIdle",
        },
      },
      {
        id: "grid",
        value: power.grid,
        kind: power.gridKind,
        title: this._t("gridTitle"),
        labels: {
          import: "gridImport",
          export: "gridExport",
          idle: "gridIdle",
        },
      },
    ];

    for (const block of blocks) {
      const icon = root.querySelector(`#${block.id}Icon`);
      const state = root.querySelector(`#${block.id}State`);
      const value = root.querySelector(`#${block.id}Kw`);
      if (!icon || !state || !value) continue;
      icon.classList.remove("charge", "discharge", "import", "export", "idle");
      icon.classList.add(block.kind);
      state.classList.remove("charge", "discharge", "import", "export");
      if (block.kind !== "idle") state.classList.add(block.kind);
      state.textContent = this._t(block.labels[block.kind]);
      value.textContent = this._formatKw(block.value);
      icon.setAttribute("title", block.title);
      icon.parentElement.setAttribute("title", block.title);
    }

    // The battery symbol is also a state-of-charge gauge: the fill follows the
    // same SOC the cost model uses.
    const fill = root.querySelector("#batFill");
    if (fill) {
      const soc = this._num(this._socEntity);
      const pct = soc === null ? 0 : clamp(soc, 0, 100);
      const height = (29 * pct) / 100;
      fill.setAttribute("height", height.toFixed(2));
      fill.setAttribute("y", (36.5 - height).toFixed(2));
      fill.setAttribute("opacity", pct > 0 ? "0.85" : "0.18");
    }
  }

  _escape(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
}

if (!customElements.get("goodwe-tou-cost-card")) {
  customElements.define("goodwe-tou-cost-card", GoodweTouCostCard);
}
