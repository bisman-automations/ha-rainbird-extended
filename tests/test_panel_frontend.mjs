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
