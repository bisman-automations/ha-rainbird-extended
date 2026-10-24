import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL(
    "../custom_components/rainbird_extended/frontend/rainbird-extended-panel.js",
    import.meta.url,
  ),
  "utf8",
);
let Panel;
vm.runInNewContext(source, {
  HTMLElement: class {},
  CSS: { escape: (value) => value },
  customElements: {
    define(_name, element) {
      Panel = element;
    },
  },
});

test("panel initializes nested settings and program fields from flow schema", () => {
  const panel = new Panel();
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        panel._initialValues([
          {
            name: "settings_section",
            type: "expandable",
            schema: [{
              name: "cycle_minutes",
              description: { suggested_value: 12 },
              default: 5,
            }],
          },
          { name: "frequency", default: "custom" },
        ]),
      ),
    ),
    { settings_section: { cycle_minutes: 12 }, frequency: "custom" },
  );
});

test("panel keeps configured entity order and filters by selector domain", () => {
  const panel = new Panel();
  panel._hass = {
    states: {
      "valve.zone_1": { attributes: { friendly_name: "Zone 1" } },
      "valve.zone_2": { attributes: { friendly_name: "Zone 2" } },
      "sensor.temperature": { attributes: { friendly_name: "Temperature" } },
    },
  };

  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        panel._entityOptions(
          { domain: "valve", multiple: true },
          ["valve.zone_2", "valve.zone_1"],
        ),
      ),
    ),
    [
      { entity_id: "valve.zone_2", label: "Zone 2" },
      { entity_id: "valve.zone_1", label: "Zone 1" },
    ],
  );
});

test("panel renders native number selector constraints and escapes text", () => {
  const panel = new Panel();
  const control = panel._renderControl(
    "number",
    { number: { min: 1, max: 60, step: 1, unit_of_measurement: "min" } },
    5,
    '["cycle_minutes"]',
  );
  assert.match(control, /min="1"/);
  assert.match(control, /max="60"/);
  assert.match(control, /<small>min<\/small>/);
  assert.match(control, /value="5"/);
  assert.equal(panel._escape('<script a="b">&'), "&lt;script a=&quot;b&quot;&gt;&amp;");
});

test("program editor only marks frequency-specific fields for their frequency", () => {
  const panel = new Panel();
  assert.equal(panel._frequencyOnly("program_a", "days"), "custom");
  assert.equal(panel._frequencyOnly("program_a", "every_days"), "cyclic");
  assert.equal(panel._frequencyOnly("program_a", "start_date"), "cyclic");
  assert.equal(panel._frequencyOnly("program_a", "station_delay"), undefined);
  assert.equal(panel._frequencyOnly("settings", "days"), undefined);
});

test("dashboard shows configured state and next date for each program", () => {
  const panel = new Panel();
  const configured = panel._programCard({
    letter: "A",
    name: "Morning",
    configured: true,
    next_run: "2026-10-12",
  });
  const unconfigured = panel._programCard({
    letter: "B",
    name: "",
    configured: false,
  });
  assert.match(configured, /Program A · Morning/);
  assert.match(configured, /Configured/);
  assert.match(configured, /Next run:/);
  assert.match(unconfigured, /Program B/);
  assert.match(unconfigured, /Not configured/);
});

test("zone cards use the linked device name and avoid repeating it on controls", () => {
  const panel = new Panel();
  const card = panel._zoneCard({
    zone: 1,
    name: "Front Boulevard",
    numbers: [
      {
        entity_id: "number.front_boulevard_flow_rate",
        name: "Front Boulevard Flow rate",
        value: "5.9",
        min: 0,
        max: 100,
        step: 0.1,
        unit: "gal/min",
      },
    ],
  });

  assert.match(card, /<h4>Front Boulevard<\/h4>/);
  assert.match(card, />Flow rate<\/span>/);
  assert.doesNotMatch(card, />Front Boulevard Flow rate<\/span>/);
});

test("dashboard renders forecast and rain delay above programs", () => {
  const panel = new Panel();
  const controller = panel._controllerCard({
    entry_id: "entry",
    title: "Backyard",
    rainbird_title: "Rain Bird",
    state: "loaded",
    rainbird_loaded: true,
    rainbird_status_entities: [],
    watering_entities: [],
    weather_entity: "weather.backyard",
    forecast_unit: "°C",
    forecast: [{
      date: "2026-10-12",
      condition: "partlycloudy",
      precipitation_probability: 40,
      temperature: 21,
      templow: 12,
    }],
    rain_delay: {
      entity_id: "number.rain_bird_controller_rain_delay",
      name: "Rain delay",
      value: "2",
      min: 0,
      max: 14,
      step: 1,
      unit: "d",
    },
    programs: [{
      letter: "A",
      name: "Morning",
      configured: true,
      next_run: "2026-10-14",
    }],
    zones: [],
  });

  assert.ok(controller.indexOf("weather-section") < controller.indexOf("Programs"));
  assert.match(controller, /10-day forecast/);
  assert.match(controller, /partlycloudy/);
  assert.match(controller, /40% rain/);
  assert.match(controller, /data-action="set-rain-delay"/);
  assert.match(controller, /data-rain-delay-value="entry"/);
  assert.match(controller, /min="0"/);
  assert.match(controller, /max="14"/);
});

test("controller rain delay uses its admin dashboard command", async () => {
  const panel = new Panel();
  const input = { value: "4", min: "0", max: "14" };
  const button = { dataset: { entryId: "entry" }, disabled: false };
  const messages = [];
  panel.shadowRoot = { querySelector: () => input };
  panel._hass = {
    async callWS(message) {
      messages.push(message);
      return { days: message.days };
    },
  };
  panel._refresh = async () => {};
  panel._showNotice = () => {};

  await panel._setRainDelay(button);

  assert.deepEqual(
    JSON.parse(JSON.stringify(messages)),
    [{
      type: "rainbird_extended/panel/rain_delay",
      entry_id: "entry",
      days: 4,
    }],
  );
  assert.equal(button.disabled, false);
});

test("dashboard controller status follows Rain Bird entities", () => {
  const panel = new Panel();
  panel._hass = {
    states: {
      "sensor.rain_bird_controller_raindelay": { state: "0" },
      "binary_sensor.rain_bird_controller_irrigating": { state: "off" },
      "switch.rain_bird_sprinkler_1": { state: "off" },
    },
  };
  const controller = {
    entry_id: "entry",
    rainbird_loaded: true,
    rainbird_status_entities: ["sensor.rain_bird_controller_raindelay"],
    irrigating_entity: "binary_sensor.rain_bird_controller_irrigating",
    watering_entities: ["switch.rain_bird_sprinkler_1"],
  };

  assert.deepEqual(
    JSON.parse(JSON.stringify(panel._controllerStatus(controller))),
    {
      availability: "Online",
      availabilityClass: "connected",
      irrigation: "Idle",
      irrigationClass: "idle",
    },
  );
  panel._hass.states["binary_sensor.rain_bird_controller_irrigating"].state = "on";
  assert.equal(panel._controllerStatus(controller).irrigation, "Irrigating");

  panel._hass.states["binary_sensor.rain_bird_controller_irrigating"].state =
    "unavailable";
  panel._hass.states["switch.rain_bird_sprinkler_1"].state = "on";
  assert.equal(panel._controllerStatus(controller).irrigation, "Irrigating");

  const badges = [
    {
      dataset: { entryId: "entry", controllerStatus: "availability" },
      textContent: "",
      className: "",
    },
    {
      dataset: { entryId: "entry", controllerStatus: "irrigation" },
      textContent: "",
      className: "",
    },
  ];
  panel._data = { controllers: [controller] };
  panel.shadowRoot = { querySelectorAll: () => badges };
  panel._updateControllerStatuses();
  assert.deepEqual(
    badges.map(({ textContent, className }) => ({ textContent, className })),
    [
      { textContent: "Online", className: "status connected" },
      { textContent: "Irrigating", className: "status irrigating" },
    ],
  );

  panel._hass.states["sensor.rain_bird_controller_raindelay"].state = "unavailable";
  panel._updateControllerStatuses();
  assert.deepEqual(
    JSON.parse(JSON.stringify(panel._controllerStatus(controller))),
    {
      availability: "Unavailable",
      availabilityClass: "disconnected",
      irrigation: "Unknown",
      irrigationClass: "unknown",
    },
  );
  assert.equal(badges[0].textContent, "Unavailable");
  assert.equal(badges[1].textContent, "Unknown");
});

test("dashboard shows a controller that's turned off", () => {
  const panel = new Panel();
  panel._hass = {
    states: {
      "sensor.rain_bird_controller_raindelay": { state: "0" },
      "binary_sensor.rain_bird_controller_irrigating": { state: "off" },
      "switch.irrigation_controller": { state: "off" },
    },
  };
  const controller = {
    entry_id: "entry",
    rainbird_loaded: true,
    rainbird_status_entities: ["sensor.rain_bird_controller_raindelay"],
    irrigating_entity: "binary_sensor.rain_bird_controller_irrigating",
    power_entity: "switch.irrigation_controller",
  };
  const status = panel._controllerStatus(controller);
  assert.equal(status.availability, "Online");
  assert.equal(status.irrigation, "Turned off");
  assert.equal(status.irrigationClass, "turned-off");
  const card = panel._programCard(
    { letter: "A", configured: true, next_run: null },
    panel._turnedOff(controller),
  );
  assert.match(card, /Turned off/);
  assert.match(card, /turned back on/);
  assert.doesNotMatch(card, /Next run/);

  // Watering still shows as watering, and unavailable isn't "off".
  panel._hass.states["binary_sensor.rain_bird_controller_irrigating"].state = "on";
  assert.equal(panel._controllerStatus(controller).irrigation, "Irrigating");
  panel._hass.states["binary_sensor.rain_bird_controller_irrigating"].state = "off";
  panel._hass.states["switch.irrigation_controller"].state = "unavailable";
  assert.equal(panel._controllerStatus(controller).irrigation, "Idle");

  // Turning it back on reloads the programs' next runs.
  panel._data = { controllers: [controller] };
  panel._powerStatesChanged();
  assert.equal(panel._powerStatesChanged(), false);
  panel._hass.states["switch.irrigation_controller"].state = "on";
  assert.equal(panel._powerStatesChanged(), true);
});

test("winter stop confirmation makes clear that scheduled programs remain enabled", () => {
  const panel = new Panel();
  const dialog = panel._stopConfirmation();
  assert.match(dialog, /stops active watering now/i);
  assert.match(dialog, /does not disable the controller or change its configured programs/i);
});

test("dashboard passes the configured stop action through to its control", () => {
  const panel = new Panel();
  const controller = panel._controllerCard({
    entry_id: "entry",
    title: "Rain Bird Extended",
    rainbird_title: "Rain Bird",
    state: "loaded",
    rainbird_loaded: true,
    rainbird_status_entities: [],
    irrigating_entity: undefined,
    watering_entities: [],
    zones: [],
    program_count: 0,
    stop_button: "button.rain_bird_controller_stop_irrigation",
    stop_action: "stop_irrigation",
  });
  assert.match(controller, /data-stop-action="stop_irrigation"/);
});

test("stop action selector uses its localized supported action label", () => {
  const panel = new Panel();
  panel._hass = {
    localize(key) {
      return key.endsWith(".stop_action.options.stop_irrigation")
        ? "Stop active irrigation"
        : undefined;
    },
  };
  const control = panel._renderControl(
    "select",
    { select: { options: ["stop_irrigation"], translation_key: "stop_action" } },
    "stop_irrigation",
    '["stop_action"]',
  );
  assert.match(control, />Stop active irrigation<\/option>/);
});

test("dashboard shows a skeleton while it first loads", () => {
  const panel = new Panel();
  panel.shadowRoot = { innerHTML: "" };
  panel._renderLoading();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<main aria-busy="true">/);
  assert.match(html, /role="status">Loading Rain Bird Extended…/);
  assert.match(html, /class="controller skeleton-card"/);
  assert.match(html, /prefers-reduced-motion:reduce/);
  assert.match(html, /data-action="refresh">Refresh/);
});

test("refresh button shows a spinner while refreshing", () => {
  const panel = new Panel();
  const button = { outerHTML: "" };
  panel.shadowRoot = { querySelector: () => button };
  // First load: the skeleton shows loading, so the button just waits.
  panel._setRefreshing(true);
  assert.equal(button.outerHTML, '<button class="secondary" data-action="refresh" disabled>Refresh</button>');
  panel._data = { controllers: [] };
  panel._setRefreshing(true);
  assert.match(button.outerHTML, /disabled aria-busy="true"/);
  assert.match(button.outerHTML, /class="spinner"/);
  assert.match(button.outerHTML, /Refreshing…/);
  panel._setRefreshing(false);
  assert.equal(button.outerHTML, '<button class="secondary" data-action="refresh">Refresh</button>');
});

test("settings dialog shows a skeleton while opening and a spinner while saving", () => {
  const panel = new Panel();
  panel._hass = { localize: () => "" };
  panel._flow = { entryId: "entry", loading: true };
  const opening = panel._flowDialog();
  assert.match(opening, /aria-busy="true"/);
  assert.match(opening, /Opening configuration…/);
  assert.match(opening, /class="skeleton field-shape"/);

  panel._flow = {
    entryId: "entry",
    loading: true,
    step: { type: "form", step_id: "settings", data_schema: [] },
  };
  const saving = panel._flowDialog();
  assert.match(saving, /class="spinner"/);
  assert.match(saving, /Saving…/);
  assert.match(saving, /data-action="close-flow" disabled/);

  panel._flow.loading = false;
  const ready = panel._flowDialog();
  assert.match(ready, /data-action="submit-flow">Save</);
  assert.doesNotMatch(ready, /class="spinner"/);

  panel._flow = {
    entryId: "entry",
    loading: true,
    step: { type: "menu", step_id: "init", menu_options: ["settings"] },
  };
  assert.match(panel._flowDialog(), /data-next-step="settings" disabled/);
});
