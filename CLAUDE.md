# Rain Bird Extended: notes for Claude and other coding agents

A Home Assistant custom integration (`rainbird_extended`, HACS) that extends
the core `rainbird` integration: valves, runtimes, time remaining, run history,
programs for controllers whose schedule can't be read (ARC8), weather skips,
blowout, and an admin sidebar panel. It reuses the core integration's
connection and coordinators; it never opens its own connection.

## How the maintainer works

- **Commit straight to `main`. No pull requests** unless asked.
- **"Prepare vX.Y.Z"** means a release commit as described in the shared
  rules: bump the manifest version, add the dated CHANGELOG section and its
  compare link, push (after asking), check CI passes, and hand over the
  release notes. Donavan publishes the draft.
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
  and CI tests that and the current release. A non-blocking **next** job
  also tests the newest `pytest-homeassistant-custom-component` build. That
  build follows HA betas, so the job tests the upcoming release while a beta
  is out. If **next** fails, look into it before that HA version is released.
  Raise the minimum as the shared rules describe. `pyrainbird` isn't pinned:
  CI installs the version the installed Home Assistant's `rainbird`
  integration requires (`core-requirements: rainbird`).
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
- `strings.json` and `translations/en.json` hold the same text (see the
  shared rules). Update both, and `icons.json`, with new entities or options.

## Checks

Run the shared checks at the end of this file, plus:

```bash
pip install pyrainbird   # the version homeassistant/components/rainbird/manifest.json requires
node --test tests/test_panel_frontend.mjs
mypy custom_components/rainbird_extended   # newest HA only
```

`pytest` reports coverage, and it must stay at 90% or more (`fail_under` in
`pyproject.toml`). Ruff adds the `PL`, `RUF` and `SIM` rules on top of the
shared set here.

Run the oldest-HA pytest locally when touching device or entity registry
code. Add tests for new behaviour. Tests that depend on the time of day use
`freeze_time` and stay away from the test schedule (05:00–05:25 local).

## Docs to keep in step

Update these with every user-facing change:

- `README.md`: entity tables, options, "Things to know".
- `CHANGELOG.md`.
- `strings.json` and `translations/en.json`.

<!-- BEGIN bisman-automations shared rules -->
<!-- This block is the same in every bisman-automations repo. Edit it in
     bisman-automations/.github (templates/CLAUDE.shared.md) and copy it to
     every repo; the Validate workflow's "standards" job checks it matches. -->

## Shared rules (every bisman-automations repo)

### Releases

- The version lives in `custom_components/<domain>/manifest.json`. Anything
  else that repeats it is called out in the repo-specific notes below and is
  bumped in the same commit.
- Versions follow [Semantic Versioning](https://semver.org): patch for fixes,
  translations, docs and CI; minor for new features, options or entities;
  major when something users rely on is removed or renamed (entities,
  services, events, options) or setup has to be redone.
- Check what is released before choosing a version:
  `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`
  (`sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`). Never add
  changes to a released version. If the manifest version has no tag yet,
  add to its CHANGELOG section instead of bumping again.
- Every push to `main` ships as a release. In that same commit, named
  `Release X.Y.Z`, bump the manifest version and write the notes directly
  under a new `## [X.Y.Z] - YYYY-MM-DD` heading in `CHANGELOG.md` (Keep a
  Changelog: Added, Changed, Deprecated, Removed, Fixed, Security), with
  that version's compare link at the bottom. There is no `## [Unreleased]`
  section and no `[Unreleased]:` compare link. New work is not collected
  under a holding heading and moved later; the release commit is where the
  heading and the entries are written. The standards check fails if a
  `## [Unreleased]` heading is present.
- Ask Donavan before every push. Push fast-forward only: never force-push,
  rebase or amend anything already pushed, or rewrite history in any other
  way. Rewriting history orphans the release tags (it broke drafting in
  ha-button-actions).
- Pushing to `main` runs **Draft release**, which creates or refreshes a
  draft `vX.Y.Z` with that version's CHANGELOG section as the notes, the
  commits since the last release and `<domain>.zip`. **Donavan publishes
  the draft himself**, and publishing creates the tag. Never create or push
  tags, publish releases, or edit published releases.
- Publishing runs **Release check**: the tag must match the manifest version
  and the CHANGELOG, and be on `main`. If it fails, fix it with a new patch
  release, not by moving the tag.

### Commits

- Author commits as `Donavan Becker <donavan.becker@icloud.com>` (set
  `git config user.name` / `user.email` in the clone). HACS's
  default-repository check needs the submitter to have a fair share of the
  commits. Credit an AI assistant with a `Co-Authored-By:` trailer if you
  like, never as the author.
- Subject lines are plain, imperative and say what changed for the user
  ("Show Pico remotes as unavailable when Lutron isn't running"). No
  `fix:`/`feat:` prefixes. Release commits are "Release X.Y.Z".
- Commit and push only when asked.

### CI (shared workflows)

- `.github/workflows/` holds four thin callers of the reusable workflows in
  [bisman-automations/.github](https://github.com/bisman-automations/.github):
  `test.yml` (lint, frontend, pytest on the oldest supported and the newest
  Home Assistant, plus the upcoming one as information only), `validate.yml`
  (hassfest, HACS, standards), `draft-release.yml` and `release-check.yml`.
  Don't add jobs to the callers. Use the shared workflow's inputs, or change
  the shared workflow so every repo gets the improvement.
- `requirements_test.txt` leaves `pytest-homeassistant-custom-component`
  unpinned (newest Home Assistant) and pins ruff (and mypy where used).
  `requirements_test_min.txt` pins the harness release for the minimum in
  `hacs.json`: the newest harness release of that month. CI fails if they
  drift apart.
- Raising the minimum Home Assistant means changing, together: `hacs.json`,
  `requirements_test_min.txt`, `python-min` in `test.yml` (3.13 up to
  2026.2, 3.14 from 2026.3), the README's Home Assistant badge, and a
  CHANGELOG entry.
- Home Assistant 2026.3 and newer need Python 3.14 (3.14.2+).
- The published hassfest rejects `categories` in `manifest.json` and Pillow
  in `requirements`. Leave both out.
- Dependabot (`.github/dependabot.yml`) proposes action and pinned-tool
  updates weekly. It ignores the test harness on purpose.
- Test and Validate also run every Thursday, the day after Home Assistant's
  betas come out. The **next** test job (newest pre-release harness) is
  information only: when it fails, fix the cause before that release ships.

### Before saying work is done

Run from the repo root, in a Python 3.14 venv with `requirements_test.txt`
installed:

```
ruff check .
ruff format --check .
pytest
```

Also run `node --test tests/*.mjs` when the repo has Node tests, mypy when
the repo-specific notes list it, and the tests in a second venv with
`requirements_test_min.txt` when a change could behave differently on the
oldest supported Home Assistant. The standards check (also in CI) is
`python <path to bisman-automations/.github>/scripts/check_repo.py .`.

### Code and docs

- Support every Home Assistant from the `hacs.json` minimum to the newest
  release. Check for deprecations in the newest release and the beta.
- Keep entity IDs, unique IDs, service names and event names stable. When
  one has to change, migrate it and say so under Changed in the CHANGELOG.
- `strings.json` and `translations/en.json` hold the same keys. Home
  Assistant reads only `translations/`, so change both in the same commit.
- Never put personal details in the repo (README, CHANGELOG, code, tests,
  screenshots): no names, addresses, account or device identifiers, or real
  entity IDs. Use placeholders such as `light.living_room`.
- `README.md` is user documentation with the standard header (badges,
  Marketplace, HACS and "add integration" My buttons) and sections
  Installation, Setup, ..., Troubleshooting, Development, License. Use plain
  language and show names, not entity IDs, wherever the UI does.
<!-- END bisman-automations shared rules -->
