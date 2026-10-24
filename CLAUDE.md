# Rain Bird Extended — notes for Claude

A Home Assistant custom integration (`rainbird_extended`, HACS) that extends
the core `rainbird` integration: valves, runtimes, time remaining, run history,
programs for controllers whose schedule can't be read (ARC8), weather skips,
blowout, and an admin sidebar panel. It reuses the core integration's
connection and coordinators; it never opens its own connection.

## How the maintainer works

- **Commit straight to `main`. No pull requests** unless asked.
- **"Prepare vX.Y.Z"** means: bump `version` in
  `custom_components/rainbird_extended/manifest.json`; add a dated
  `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md` (Keep a Changelog) and
  its compare link at the bottom; push; check CI passes; hand over release
  notes. The maintainer publishes the release. The `Draft release` workflow
  drafts it on every push to `main`, and `release.yml` fails a tag that
  doesn't match the manifest or has no CHANGELOG section.
- Semantic versioning: new features or changed behaviour → minor; fixes →
  patch. Check the latest tag before choosing (`git tag --sort=-v:refname`).
- **Never put the maintainer's own details (account numbers, service IDs,
  addresses, MACs, serials) in the README, CHANGELOG, tests or code.** Use
  placeholders.
- Write user-facing text (README, CHANGELOG, translations, release notes) in
  plain words, explaining what changes for the user, not the implementation.

## Things that must not break

- **Entity unique IDs and entity IDs.** Unique IDs are
  `<controller uid>-<key>` and `<controller uid>-<zone>-<key>`. Renaming a
  `translation_key` changes entity IDs for new installs only; say so in the
  CHANGELOG.
- **Devices (since 1.9.0).** Entities live on Rain Bird Extended's own
  service devices (`devices.py`), never on the core Rain Bird devices:
  - The controller device is linked to the Rain Bird controller by MAC
    `connections`. That also links it to UniFi and HomeKit Extended devices for
    the same hardware. It has no `via_device`.
  - Each zone device carries both `(rainbird_extended, uid-zone)` and
    `(rainbird, uid-zone)` identifiers, so it's linked to the Rain Bird zone.
    Its `via_device_id` is our controller device.
  - Names, starting areas, manufacturer, model and firmware are copied from
    the Rain Bird devices, and names follow renames.
  - Entities use `DeviceInfo(identifiers={(DOMAIN, device_uid)})`.
  - This matches the other bisman-automations integrations (TV Mgmt, Ford
    Assistant, HomeKit Extended): each has its own device, linked instead of
    merged.
- **HomeKit Extended depends on this layout:**
  - It finds a zone's valve, its duration `number` (`valve_runtime`) and its
    end-time timestamp `sensor` (`time_remaining`) on the valve's device.
  - It finds the `run_all_zones`, `stop_irrigation` and `resume` buttons and
    the `irrigating` binary sensor (by translation key) on the device the
    valve's device is connected through.
  - It reads accessory info and the MAC address along that device chain.
  - Keep all of this when changing devices or keys.
- **Options storage.** Settings are stored flat (the form groups them into
  sections via `nest`/`flatten` in `config_flow.py`). Programs are stored as
  one dict per program (`program_a`, `program_b`, …), with zones as numbers.
  The options flow and the sidebar panel must both keep the other's keys
  when saving.

## Home Assistant compatibility

- **Supported range:** oldest supported is **2026.9**, set in `hacs.json`,
  and CI tests that and the current release. A non-blocking **next HA** job
  also tests the newest `pytest-homeassistant-custom-component` build. That
  build follows HA betas, so the job tests the upcoming release while a beta
  is out. CI also runs weekly on Thursdays, after the Wednesday beta releases.
  If **next HA** fails, look into it before that HA version is released.
  When raising the minimum,
  change `hacs.json`, the `HA 2026.9` CI matrix pins
  (`pytest-homeassistant-custom-component` and `pyrainbird`), and the README.
- **HA 2026.9+ keeps devices per config entry:**
  - Use `async_get_device_by_identifier(identifier, config_entry_id)` and
    `via_device_id`.
  - Don't use `async_get_device` or `via_device`. They're deprecated, and
    `via_device` already raises in tests on 2026.10.
- In tests, HA's `report_usage` deprecations raise, so a clean test run
  means no deprecated core API was hit on the tested paths. Look for
  `breaks_in_ha_version` in a new HA release (or the `dev` branch of
  `home-assistant/core`) when checking upcoming versions.
- **Keep returning `True`/`False`** from `async_setup_entry` and
  `async_unload_entry`. HA 2026.11 moves core integrations to returning
  `None` and raising instead. Custom integrations may still return booleans
  there, but 2026.10 and earlier treat `None` as a failed setup.
- **Core Rain Bird internals:** `entry.runtime_data.coordinator`,
  `.schedule_coordinator`, `.controller`, `.model_info` and the core
  coordinator's `_device_lock` aren't public API. Diff
  `homeassistant/components/rainbird` between releases before each new HA
  version.
- `voluptuous` imports are fine: HA swaps in `probatio` at runtime (see the
  mypy override in `pyproject.toml`).

## Layout

- `coordinator.py`: works out end times and tracks runs. End-time sources,
  in priority order:
  1. what the controller reports;
  2. a run started from Home Assistant;
  3. the schedule (the controller's, or `programs.py` when programs are set
     up);
  4. an estimate from the valve runtime.
- `programs.py`: programs entered in the options. They build the same
  pyrainbird recurrences the controller's schedule would.
- `devices.py`: our devices, linked to Rain Bird's.
- `panel.py` and `frontend/rainbird-extended-panel.js`: the admin sidebar
  panel. It's a plain web component with no build step. The module URL
  carries the version to bust browser caches.
- `translations/en.json` is the only strings file (there is no
  `strings.json`). Update it and `icons.json` with new entities or options.

## Checks (all run in CI)

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
node --test tests/test_panel_frontend.mjs
mypy custom_components/rainbird_extended   # current HA only
pytest --cov                               # coverage must stay >= 90%
```

CI also runs these:

- **Oldest supported HA:** pytest with
  `pytest-homeassistant-custom-component==0.13.367 pyrainbird==6.5.0`
  installed (HA 2026.9).
- **Validation:** `hassfest` and the HACS action.

Run the oldest-HA pytest locally when touching device or entity registry
code. Add tests for new behaviour. Tests that depend on the time of day use
`freeze_time` and stay away from the test schedule (05:00–05:25 local).

## Docs to keep in step

Update these with every user-facing change:

- `README.md`: entity tables, options, "Things to know".
- `CHANGELOG.md`.
- `translations/en.json`.
