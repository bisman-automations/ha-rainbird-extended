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
| Time remaining | `sensor.rain_bird_sprinkler_1_time_remaining` | When the current run ends (timestamp — the UI shows a countdown like "in 4 minutes"); while idle, when the last run ended. Attribute `estimated`. See [How time remaining is worked out](#how-time-remaining-is-worked-out). |
| Estimated next run | `sensor.rain_bird_sprinkler_1_estimated_next_run` | When the zone next runs: from the controller's schedule when it can be read, otherwise the last run's start plus **Run every**. Attributes `estimated`, `last_start`. (Installed before 1.5.0, it keeps its `…_next_run` id.) |
| Run every | `number.rain_bird_sprinkler_1_run_every` | How many days apart the zone runs (1–30, default 1), for the estimated next run. |
| Last run | `sensor.rain_bird_sprinkler_1_last_run` | When the zone last started, however it was started. Attributes `end`, `duration` (seconds) and `source`. Remembered across restarts. |
| Run | `event.rain_bird_sprinkler_1_run` | Fires `started` and `finished` for every run, with `source` (`home_assistant`, `run_all_zones`, `blowout`, `program`, `schedule` or `other`), `start`, and on finish `end` and `duration`. See [Run events](#run-events). |
| Flow rate | `number.rain_bird_sprinkler_1_flow_rate` | How much water the zone uses per minute (L/min, or gal/min with US units). 0 = don't track water. |
| Water used | `sensor.rain_bird_sprinkler_1_water_used` | Running total from run time × flow rate (L or gal). Add it to the Energy dashboard's water consumption. |

### On the controller

| Entity | Example ID | What it does |
| --- | --- | --- |
| Run program A, B, … | `button.rain_bird_controller_run_program_a` | Starts one of the controller's programs. One button per program the model supports. |
| Run all zones | `button.rain_bird_controller_run_all_zones` | Runs the zones once, each for its valve runtime — every zone in order, or the zones and order set in the options, optionally with cycle and soak. See [Run all zones and cycle and soak](#run-all-zones-and-cycle-and-soak). |
| Blowout sprinklers | `button.rain_bird_controller_blowout_sprinklers` | Winterizing with an air compressor: each zone in short bursts with a rest after each. See [Blowout sprinklers](#blowout-sprinklers). |
| Pause / Resume | `button.rain_bird_controller_pause`, `button.rain_bird_controller_resume` | Pause Run all zones, cycle and soak or a blowout where it is, and carry on later. See [Pause and resume](#pause-and-resume). |
| Stop irrigation | `button.rain_bird_controller_stop_irrigation` | Stops whatever is running, including Run all zones, and forgets anything paused. |
| Seasonal adjustment A, B, … | `number.rain_bird_controller_seasonal_adjustment_a` | Each program's seasonal adjust, 10–200% (100% = runtimes as programmed), re-read every 30 minutes. Controllers with one controller-wide value (ESP-RZXe, ST8) get a single **Seasonal adjustment**. See [Seasonal adjustment](#seasonal-adjustment). |
| Irrigating | `binary_sensor.rain_bird_controller_irrigating` | On while any zone runs, however it was started. Attributes: `zones`, `end`, `run_all_zones`, `blowout`, `paused`. |
| Rain skip | `switch.rain_bird_controller_rain_skip` | Only with a weather entity chosen in the options. See [Rain skip](#rain-skip). |
| Freeze skip | `switch.rain_bird_controller_freeze_skip` | With a weather entity or temperature sensor chosen in the options. See [Freeze skip](#freeze-skip). |
| Weather adjustment | `switch.rain_bird_controller_weather_adjustment` | With a weather entity chosen, on Home Assistant 2026.10 or newer. Starts off. See [Weather adjustment](#weather-adjustment). |
| Soil moisture skip | `switch.rain_bird_controller_soil_moisture_skip` | With a soil moisture sensor chosen in the options. See [Soil moisture skip](#soil-moisture-skip). |
| Run history | `calendar.rain_bird_controller_run_history` | Every run of every zone as a calendar event: how long, and what started it. See [Run history](#run-history). |

### Action: `rainbird_extended.start_zone`

Runs a zone once for a set time without changing its valve runtime:

```yaml
action: rainbird_extended.start_zone
target:
  entity_id: valve.rain_bird_sprinkler_1
data:
  duration: "00:15:00"
  cycle_and_soak: true  # optional, uses the cycle and soak times from the options
```

### Run all zones and cycle and soak

In Rain Bird Extended's options:

- **Run all zones: zones and order** — pick the zones to run, then drag them
  into the order to run them. Leave empty to run every zone in zone order.
- **Cycle length** — split each zone's run into cycles of at most this many
  minutes so water soaks in instead of running off (slopes, clay). 0 (the
  default) turns cycle and soak off.
- **Soak time** — the least time each zone rests between its own cycles
  (default 30 minutes).

With cycle and soak on, Run all zones takes turns: each zone runs one cycle,
then the next zone, round after round, so one zone soaks while the others
water. It only adds a pause between rounds when the other zones' cycles
don't already cover a zone's soak time. For example, with a 5 minute cycle and
30 minute soak, three zones of 10 minutes each run 5 minutes each, wait 20
minutes, then run their second 5 minutes. `start_zone` with `cycle_and_soak`
does the same for a single zone.

### Blowout sprinklers

For winterizing: with an air compressor hooked up, press **Blowout
sprinklers**. Each zone is opened in short bursts with a rest after every
burst (so the compressor can recover), one zone after another. The defaults:

| Option | Default |
| --- | --- |
| Blowout: zones and order | every zone, in zone order (pick zones and drag them into order) |
| Blowout: bursts per zone | 10 |
| Blowout: burst length | 1 minute (Rain Bird runs zones in whole minutes) |
| Blowout: rest between bursts | 150 seconds, also between zones |

With 4 zones that's about 2 hours 20 minutes. **Stop irrigation** (or closing
any valve) ends it; the Irrigating sensor's `blowout` attribute shows it's
running, and every burst fires the zone's Run event with source `blowout`.

`rainbird_extended.blowout` does the same from an automation or script, and
can override any setting for that run:

```yaml
action: rainbird_extended.blowout
target:
  entity_id: button.rain_bird_controller_blowout_sprinklers
data:
  zones: [1, 2, 3, 4]
  cycles: 10
  on_time: "00:01:00"
  rest: "00:02:30"
```

### Pause and resume

**Pause** stops Run all zones, cycle and soak or a blowout where it is and
remembers what's left — including the rest of the zone that was running
(rounded up to a whole minute, since Rain Bird runs zones in minutes) or the
rest of a soak or blowout rest. **Resume** carries on from there. The buttons
are only available when there's something to pause or resume, and the
Irrigating sensor's `paused` attribute says what's paused. **Stop irrigation**
forgets anything paused; starting Run all zones, cycle and soak or a blowout
replaces it.

### Run history

The **Run history** calendar shows every run of every zone — from Home
Assistant, the schedule, a program or the Rain Bird app — as an event named
after the zone, with how long it ran and what started it (for example
"12 min, started by the schedule"). A run in progress shows as the current
event. History is kept for a year and survives restarts.

### Run events

Each zone's **Run** event entity fires when the zone starts and finishes,
whatever started it, so automations can react to (and the logbook shows)
every run:

```yaml
triggers:
  - trigger: state
    entity_id: event.rain_bird_sprinkler_1_run
conditions:
  - condition: state
    entity_id: event.rain_bird_sprinkler_1_run
    attribute: event_type
    state: finished
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        Front lawn watered for
        {{ (state_attr('event.rain_bird_sprinkler_1_run', 'duration') / 60) | round }} min
```

`source` is `schedule` when the run matches the controller's schedule,
`program` for a program started from Home Assistant, and `other` for anything
else (such as the Rain Bird app).

### Rain Bird switches

The valves replace the core Rain Bird zone switches, so by default Rain Bird
Extended **disables those switches** (so HomeKit and dashboards don't show every
zone twice). To keep them, turn off **Disable the Rain Bird zone switches** in
Rain Bird Extended's options (**Settings → Devices & services → Rain Bird
Extended → Configure**). Switches you disabled yourself are left alone, and
removing Rain Bird Extended re-enables the ones it disabled.

The valves map directly onto HomeKit's irrigation valve characteristics
(Active, In Use, Set Duration, Remaining Duration).

### Options

**Settings → Devices & services → Rain Bird Extended → Configure.** The
options are grouped into sections: Run all zones and cycle and soak, Blowout
sprinklers, Weather (the forecast entity and the daily check time shared by
rain, freeze and soil moisture skip and weather adjustment), Rain skip, Freeze
skip, Soil moisture skip and Weather adjustment. Temperatures are in your Home
Assistant unit system.

### Rain skip

Choose a weather entity in Rain Bird Extended's options to get a **Rain skip**
switch. While it's on, each day at the check time (default 04:00 — pick a time
before your programs start) it looks at today's daily forecast. If the chance
of rain is at least your threshold (default 60%), or the forecast has no
chance of rain but its condition is rainy, it sets the controller's rain delay
(default 1 day). It never shortens a longer rain delay that's already set.
Each skip fires a `rainbird_extended_rain_skip` event, and the switch's
attributes show the last check, chance of rain and skip.

### Freeze skip

Choose a weather entity and/or an outdoor temperature sensor in the options
to get a **Freeze skip** switch (on by default). While it's on:

- Each day at the check time, if today's forecast low or the sensor is at or
  below the freeze temperature (default 35°F / 2°C), it sets the controller's
  rain delay (default 1 day), never shortening a longer one.
- With a sensor, whenever it reads at or below the freeze temperature while
  anything is watering (including Run all zones and cycle and soak), it stops
  irrigation — also if a zone is started while it's freezing.
- **Blowouts are never stopped** — they're air, and done in the cold.

Each skip or stop fires a `rainbird_extended_freeze_skip` event with `reason`
(`forecast` or `sensor`) and the temperature.

### Soil moisture skip

Choose a soil moisture sensor in the options to get a **Soil moisture skip**
switch (on by default). Each day at the check time, if the sensor reads at or
above your threshold (default 40%), it sets the controller's rain delay
(default 1 day), never shortening a longer one. Each skip fires a
`rainbird_extended_moisture_skip` event.

### Weather adjustment

On Home Assistant 2026.10 or newer, choosing a weather entity also adds a
**Weather adjustment** switch. It **starts off**, because it changes your
controller's settings every day. While it's on, each day at the check time it
sets every program's seasonal adjustment from today's forecast high:

| Forecast high | Seasonal adjustment |
| --- | --- |
| at or below the low temperature (default 60°F / 15°C) | the low percent (default 60%) |
| between | in a straight line between, rounded to 5% |
| at or above the high temperature (default 95°F / 35°C) | the high percent (default 150%) |

For example, with the defaults a 77°F (25°C) day gets 105%. Each check fires
a `rainbird_extended_weather_adjustment` event, and the switch's attributes
show the last high and percent. You can still change Seasonal adjustment A, B,
… yourself; the next check sets them again.

### Seasonal adjustment

Read from the controller's water budget. On Home Assistant 2026.10 and newer
each program's seasonal adjustment is an adjustable **number**
(`number.…_seasonal_adjustment_a`, 10–200%). Home Assistant 2026.9's Rain Bird
library can read it but not change it, so there it's a read-only **sensor**
(`sensor.…_seasonal_adjustment_a`); after upgrading Home Assistant the sensors
are replaced by the numbers automatically. Controllers without water budgets
show a single read-only sensor from the controller state, if they report it.

## Requirements

- Home Assistant 2026.9 or newer (tested against 2026.9 and 2026.10)
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
  should, with runtimes scaled by the program's seasonal adjustment.
- **Anything else** (the Rain Bird app, the core switch or its
  `start_irrigation` action, or a schedule that can't be read): an estimate
  of the zone's **valve runtime** from when it was seen starting. Changing the
  valve runtime during the run moves the estimate right away. If the zone is
  still running when the estimate runs out, it shows about a minute left until
  the zone is seen stopping. The sensor's `estimated` attribute is `true` while
  it's showing an estimate.
- **While idle:** when the last run ended (a time in the past, so HomeKit shows
  nothing left). It's kept across restarts, so it's only unknown before the
  zone's first run.

## Things to know

- **Closing any valve stops all irrigation.** Rain Bird controllers have no
  "stop one zone" command; the core switch behaves the same way.
- Rain Bird only accepts whole minutes, so runtimes are rounded to the minute.
- The valve runtime number is in seconds because that is what HomeKit's Set
  Duration uses.
- **Run all zones**, cycle and soak and **Blowout sprinklers** are run by
  Home Assistant, one zone at a time (the controller can't queue manual runs),
  so Home Assistant has to stay running until they finish. Starting another zone, a program, or
  stopping irrigation ends it, as does a zone being stopped early from the
  Rain Bird app.
- **Estimated next run** for controllers whose schedule can't be read (the
  ARC8, or when you water from Home Assistant automations) is the last run's
  start plus **Run every** days, moved on by whole intervals when a day was
  missed; set Run every to match how often the zone actually runs. Before a
  zone's first run it counts from when the integration started.
- **Next run** from the schedule follows the programmed start times and zone order, and skips
  rain delay days. It doesn't account for seasonal adjustment changing how
  long earlier zones in the same program run.
- **Water used** is an estimate: run time × the flow rate you enter. Use your
  water meter or a test run to find each zone's flow rate.
- If Rain Bird reloads (for example after changing its options), Rain Bird
  Extended reloads with it automatically. If the Rain Bird entry is deleted,
  **Settings → Repairs** shows an issue that removes Rain Bird Extended for it.

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
