# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Seasonal adjustment** per program (10–200%), adjustable from Home Assistant,
  using the controller's water budget. Models without water budgets get a
  read-only sensor instead.
- `rainbird_extended.start_zone` action to run a zone once for a set time.
- Option to disable the core Rain Bird zone switches the valves replace.

### Changed

- The core Rain Bird zone switches are now disabled by default; turn off
  **Disable the Rain Bird zone switches** in the options to keep them.
- The controller is now also asked for its seasonal adjustment every 30
  minutes.

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

[Unreleased]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/bisman-automations/ha-rainbird-extended/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-rainbird-extended/releases/tag/v1.0.0
