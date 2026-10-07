# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-rainbird-extended/releases/tag/v1.0.0
