<p align="center">
  <img src="https://raw.githubusercontent.com/bisman-automations/ha-rainbird-extended/main/custom_components/rainbird_extended/brand/icon@2x.png" alt="Rain Bird Extended" width="128" height="128">
</p>

<h1 align="center">Rain Bird Extended</h1>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/bisman-automations/ha-rainbird-extended/actions/workflows/ci.yml"><img src="https://github.com/bisman-automations/ha-rainbird-extended/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

A Home Assistant custom integration that extends the built-in
[Rain Bird](https://www.home-assistant.io/integrations/rainbird) integration.
It adds entities **to the devices Rain Bird already created** — no new
devices, no second connection to the controller.

### On each zone

| Entity | Example ID | What it does |
| --- | --- | --- |
| Valve | `valve.rain_bird_sprinkler_1` | Water valve. Open runs the zone for its valve runtime; close stops irrigation. |
| Valve runtime | `number.rain_bird_sprinkler_1_valve_runtime` | How long the zone runs when opened, in seconds (1 min steps, 1 min – 24 h). Remembered across restarts. |
| Time remaining | `sensor.rain_bird_sprinkler_1_time_remaining` | When the current run ends (timestamp — the UI shows a countdown like "in 4 minutes"). Unknown while idle. |
| Next run | `sensor.rain_bird_sprinkler_1_next_run` | When the controller's schedule next runs this zone. Controllers with programs only. |
| Last run | `sensor.rain_bird_sprinkler_1_last_run` | When the zone last started, however it was started. Attributes `end` and `duration` (seconds). Remembered across restarts. |
| Flow rate | `number.rain_bird_sprinkler_1_flow_rate` | How much water the zone uses per minute (L/min, or gal/min with US units). 0 = don't track water. |
| Water used | `sensor.rain_bird_sprinkler_1_water_used` | Running total from run time × flow rate (L or gal). Add it to the Energy dashboard's water consumption. |

### On the controller

| Entity | Example ID | What it does |
| --- | --- | --- |
| Run program A, B, … | `button.rain_bird_controller_run_program_a` | Starts one of the controller's programs. One button per program the model supports. |
| Run all zones | `button.rain_bird_controller_run_all_zones` | Runs every zone once, in order, each for its valve runtime. |
| Stop irrigation | `button.rain_bird_controller_stop_irrigation` | Stops whatever is running, including Run all zones. |
| Seasonal adjustment A, B, … | `number.rain_bird_controller_seasonal_adjustment_a` | Each program's seasonal adjust, 10–200% (100% = runtimes as programmed), re-read every 30 minutes. Controllers with one controller-wide value (ESP-RZXe, ST8) get a single **Seasonal adjustment**. See [Seasonal adjustment](#seasonal-adjustment). |
| Irrigating | `binary_sensor.rain_bird_controller_irrigating` | On while any zone runs, however it was started. Attributes: `zones`, `end`, `run_all_zones`. |
| Rain skip | `switch.rain_bird_controller_rain_skip` | Only with a weather entity chosen in the options. See [Rain skip](#rain-skip). |

### Action: `rainbird_extended.start_zone`

Runs a zone once for a set time without changing its valve runtime:

```yaml
action: rainbird_extended.start_zone
target:
  entity_id: valve.rain_bird_sprinkler_1
data:
  duration: "00:15:00"
```

### Rain Bird switches

The valves replace the core Rain Bird zone switches, so by default Rain Bird
Extended **disables those switches** (so HomeKit and dashboards don't show every
zone twice). To keep them, turn off **Disable the Rain Bird zone switches** in
Rain Bird Extended's options (**Settings → Devices & services → Rain Bird
Extended → Configure**). Switches you disabled yourself are left alone, and
removing Rain Bird Extended re-enables the ones it disabled.

The valves map directly onto HomeKit's irrigation valve characteristics
(Active, In Use, Set Duration, Remaining Duration).

### Rain skip

Choose a weather entity in Rain Bird Extended's options to get a **Rain skip**
switch. While it's on, each day at the check time (default 04:00 — pick a time
before your programs start) it looks at today's daily forecast. If the chance
of rain is at least your threshold (default 60%), or the forecast has no
chance of rain but its condition is rainy, it sets the controller's rain delay
(default 1 day). It never shortens a longer rain delay that's already set.
Each skip fires a `rainbird_extended_rain_skip` event, and the switch's
attributes show the last check, chance of rain and skip.

### Seasonal adjustment

Read from the controller's water budget. With Home Assistant 2026.9, whose
Rain Bird library can read but not change it, each program's seasonal
adjustment is a read-only **sensor** (`sensor.…_seasonal_adjustment_a`). Once
Home Assistant ships pyrainbird 6.6 or newer, it becomes an adjustable
**number** (`number.…_seasonal_adjustment_a`) automatically, and the old
sensors can be deleted. Controllers without water budgets show a single
read-only sensor from the controller state, if they report it.

## Requirements

- Home Assistant 2026.9 or newer (developed and tested against 2026.9.4)
- The core **Rain Bird** integration set up for your controller

## Install

### HACS

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-rainbird-extended&category=integration)

Or add it by hand:

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
so you don't get each zone twice; the switches are disabled by default.

## How time remaining is worked out

- **Runs started from these valves:** start time + valve runtime.
- **Runs started anywhere else** (a schedule, the Rain Bird app, the core
  switch): asked from the controller each minute while a zone is running,
  using the combined controller state command. The controller reports this
  as seconds remaining for the active station.
- Controllers that don't support that command (the ARC8, for example) are
  detected once and not asked again. For them, time remaining comes from the
  controller's schedule: a zone running during one of its scheduled runs, or
  during a program started with a **Run program** button, ends when that run
  should, with runtimes scaled by the program's seasonal adjustment. Runs
  started from the Rain Bird app or the core switch stay unknown.

## Things to know

- **Closing any valve stops all irrigation.** Rain Bird controllers have no
  "stop one zone" command; the core switch behaves the same way.
- Rain Bird only accepts whole minutes, so runtimes are rounded to the minute.
- The valve runtime number is in seconds because that is what HomeKit's Set
  Duration uses.
- **Run all zones** is run by Home Assistant, one zone at a time (the
  controller can't queue manual runs). Starting another zone, a program, or
  stopping irrigation ends it, as does a zone being stopped early from the
  Rain Bird app.
- **Next run** follows the programmed start times and zone order, and skips
  rain delay days. It doesn't account for seasonal adjustment changing how
  long earlier zones in the same program run.
- **Water used** is an estimate: run time × the flow rate you enter. Use your
  water meter or a test run to find each zone's flow rate.
- If Rain Bird reloads (for example after changing its options), Rain Bird
  Extended reloads with it automatically.

## Reporting a problem

Open an [issue](https://github.com/bisman-automations/ha-rainbird-extended/issues)
and attach diagnostics: **Settings → Devices & services → Rain Bird Extended →
⋮ → Download diagnostics**. They include your controller model and firmware and
whether it reports remaining run time; the controller's MAC address is removed.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

## License

[MIT](LICENSE)
