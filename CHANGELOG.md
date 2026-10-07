# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.0] - 2026-10-07

### Added

- **Freeze skip** switch: with a weather entity and/or an outdoor temperature
  sensor, sets the rain delay when today's low is at or below the freeze
  temperature (default 35°F / 2°C), and with a sensor stops watering whenever
  it reads freezing. Blowouts are never stopped. Fires
  `rainbird_extended_freeze_skip`.
- **Weather adjustment** switch (Home Assistant 2026.10+, starts off): sets
  every program's seasonal adjustment daily from the forecast high, from 60%
  at 60°F (15°C) to 150% at 95°F (35°C) by default. Fires
  `rainbird_extended_weather_adjustment`.
- **Soil moisture skip** switch: with a soil moisture sensor, sets the rain
  delay at the check time when the soil is at or above a threshold (default
  40%). Fires `rainbird_extended_moisture_skip`.
- **Pause** and **Resume** buttons for Run all zones, cycle and soak and
  blowouts: pausing remembers what's left, including the rest of the running
  zone (rounded up to a minute) or soak. The Irrigating sensor has a `paused`
  attribute.
- **Run history** calendar: every zone run, however it was started, with how
  long it ran and what started it. Kept for a year across restarts.

### Changed

- The options are grouped into sections (Run all zones, Blowout, Weather,
  Rain skip, Freeze skip, Soil moisture skip, Weather adjustment). Saved
  options carry over.
- The daily check time is shared by rain, freeze and soil moisture skip and
  weather adjustment.
- CI also type checks with mypy and requires 90% test coverage. Tests are
  organized by feature.

## [1.3.1] - 2026-10-07

### Changed

- **Run all zones: zones and order** and **Blowout: zones and order** in the
  options are now zone pickers: pick the zones, then drag them into order,
  instead of typing zone numbers separated by commas. Saved orders carry over
  unchanged, and the picker only offers this controller's zones.
- Tested against both Home Assistant 2026.9 (pyrainbird 6.5) and 2026.10
  (pyrainbird 6.6). With 2026.10, seasonal adjustment is adjustable.

## [1.3.0] - 2026-10-07

### Added

- **Blowout sprinklers** button for winterizing with an air compressor: each
  zone in turn is opened in short bursts with a rest after every burst.
  Defaults: every zone in order, 10 one-minute bursts per zone, 150 second
  rest. Zones and order, bursts, burst length and rest are in the options.
- `rainbird_extended.blowout` action targeting that button, with optional
  `zones`, `cycles`, `on_time` and `rest` overrides.
- The Irrigating sensor has a `blowout` attribute, and runs started by a
  blowout have `blowout` as their source.

## [1.2.0] - 2026-10-06

### Added

- **Seasonal adjustment** per program (10–200%) from the controller's water
  budget, which the ARC8, ESP-TM2 and other models support (the 1.1.0 sensor
  was unavailable on the ARC8). Read-only with Home Assistant 2026.9
  (pyrainbird 6.5); adjustable once Home Assistant ships pyrainbird 6.6 or
  newer. Models without water budgets keep a read-only sensor from the
  controller state.
- **Irrigating** binary sensor on the controller: on while any zone runs, with
  the running zones and end time.
- **Rain skip**: choose a weather entity in the options to get a Rain skip
  switch that sets the controller's rain delay when today's forecast is wet
  enough (chance of rain threshold, check time and delay days configurable).
- Time remaining for controllers that don't report it (such as the ARC8),
  worked out from the controller's schedule for scheduled runs and programs
  started from Home Assistant.
- Bug report and feature request forms.
- **Cycle and soak**: options for a cycle length and soak time. Run all zones
  splits each zone's run into cycles and takes turns between zones, pausing
  only when needed for each zone to soak; `start_zone` gets a
  `cycle_and_soak` option for a single zone.
- **Run all zones** zones and order, set in the options (for example
  `3, 1, 2`).
- **Run** event entity per zone, firing `started` and `finished` with what
  started the run (`home_assistant`, `run_all_zones`, `program`, `schedule`
  or `other`).
- **Last run** has a `source` attribute.
- A repair issue, with a fix that removes Rain Bird Extended, when the Rain
  Bird entry it extends is deleted.

### Changed

- The seasonal adjustment is read from the water budget every 30 minutes; the
  idle controller state check only runs for models without water budgets.
- The old **Seasonal adjustment** sensor is removed on controllers that now
  get one per program, instead of being left behind as unavailable.

## [1.1.0] - 2026-10-06

### Added

- Diagnostics: **Download diagnostics** on the integration page now includes the
  controller model and firmware, whether it reports remaining run time, zones,
  runtimes, active zones and current end times. The controller's MAC address
  is redacted.
- Brand icon (a water drop with a timer inside a house) shown in Home Assistant
  and HACS.
- **Next run** sensor per zone, from the controller's schedule.
- **Last run** sensor per zone: when it last started (any source), with `end`
  and `duration` attributes.
- **Flow rate** number and **Water used** sensor per zone, for the Energy
  dashboard's water consumption.
- **Run program** buttons (one per program), **Run all zones** and **Stop
  irrigation** buttons on the controller.
- **Seasonal adjustment** sensor on the controller.
- `rainbird_extended.start_zone` action to run a zone once for a set time.
- Option to disable the core Rain Bird zone switches the valves replace.

### Changed

- The core Rain Bird zone switches are now disabled by default; turn off
  **Disable the Rain Bird zone switches** in the options to keep them.
- The controller is now also asked for its state every 30 minutes while idle
  (for the seasonal adjustment).

## [1.0.0] - 2026-10-06

Initial release.

### Added

- **Valve** entity per zone (`valve.*`): opening runs the zone for its valve
  runtime; closing stops irrigation.
- **Valve runtime** number per zone (`number.*_valve_runtime`): run length in
  seconds, 1 minute steps from 1 minute to 24 hours, remembered across restarts.
- **Time remaining** sensor per zone (`sensor.*_time_remaining`): timestamp for
  when the current run ends; unknown while idle.
- Entities attach to the zone devices the core Rain Bird integration already
  created and share its connection to the controller.
- Time remaining for runs started outside these valves (schedules, the Rain
  Bird app, the core switch) is read from the controller each minute while a
  zone runs; controllers without that command are detected once and skipped.
- Works with HomeKit Bridge irrigation valves via `linked_valve_duration` and
  `linked_valve_end_time`.
- Reloads automatically when the Rain Bird integration reloads.
- Config flow that adds a single controller immediately or lets you pick one
  when there are several.

[Unreleased]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-rainbird-extended/releases/tag/v1.0.0
