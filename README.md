# Rain Bird Extended

A Home Assistant custom integration that extends the built-in
[Rain Bird](https://www.home-assistant.io/integrations/rainbird) integration.
For every zone it adds three entities **to the zone devices Rain Bird already
created** — no new devices, no second connection to the controller:

| Entity | Example ID | What it does |
| --- | --- | --- |
| Valve | `valve.rain_bird_sprinkler_1` | Water valve. Open runs the zone for its valve runtime; close stops irrigation. |
| Valve runtime | `number.rain_bird_sprinkler_1_valve_runtime` | How long the zone runs when opened, in seconds (1 min steps, 1 min – 24 h). Remembered across restarts. |
| Time remaining | `sensor.rain_bird_sprinkler_1_time_remaining` | When the current run ends (timestamp — the UI shows a countdown like "in 4 minutes"). Unknown while idle. |

The existing Rain Bird switches keep working and stay in sync with the valves.

These map directly onto HomeKit's irrigation valve characteristics (Active,
In Use, Set Duration, Remaining Duration).

## Requirements

- Home Assistant 2026.9 or newer (developed and tested against 2026.9.4)
- The core **Rain Bird** integration set up for your controller

## Install

### HACS

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/bisman-automations/ha-rainbird-extended` as an **Integration**.
2. Install **Rain Bird Extended** and restart Home Assistant.

### Manual

Copy `custom_components/rainbird_extended` into your config's
`custom_components` folder and restart.

## Set up

**Settings → Devices & services → Add integration → Rain Bird Extended.** With
one controller it is added immediately; with several you pick which one.

## HomeKit

Expose the valves through the HomeKit Bridge and link the runtime and time
remaining entities so the Home app shows the duration picker and countdown:

```yaml
homekit:
  - name: Sprinklers
    filter:
      include_entities:
        - valve.rain_bird_sprinkler_1
        - valve.rain_bird_sprinkler_2
    entity_config:
      valve.rain_bird_sprinkler_1:
        type: sprinkler
        linked_valve_duration: number.rain_bird_sprinkler_1_valve_runtime
        linked_valve_end_time: sensor.rain_bird_sprinkler_1_time_remaining
      valve.rain_bird_sprinkler_2:
        type: sprinkler
        linked_valve_duration: number.rain_bird_sprinkler_2_valve_runtime
        linked_valve_end_time: sensor.rain_bird_sprinkler_2_time_remaining
```

Changing the duration in the Home app updates the valve runtime entity, and
the next run uses it. Use the valves (not the Rain Bird switches) in HomeKit
so you don't get each zone twice.

## How time remaining is worked out

- **Runs started from these valves:** start time + valve runtime.
- **Runs started anywhere else** (a schedule, the Rain Bird app, the core
  switch): asked from the controller each minute while a zone is running,
  using the combined controller state command. The controller reports this
  as seconds remaining for the active station.
- Controllers that don't support that command are detected once and not asked
  again; for them time remaining is only known for runs started from these
  valves.

## Things to know

- **Closing any valve stops all irrigation.** Rain Bird controllers have no
  "stop one zone" command; the core switch behaves the same way.
- Rain Bird only accepts whole minutes, so runtimes are rounded to the minute.
- The valve runtime number is in seconds because that is what HomeKit's Set
  Duration uses.
- If Rain Bird reloads (for example after changing its options), Rain Bird
  Extended reloads with it automatically.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```
