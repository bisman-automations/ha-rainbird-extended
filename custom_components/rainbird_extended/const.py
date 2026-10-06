"""Constants for Rain Bird Extended."""

from datetime import timedelta

DOMAIN = "rainbird_extended"

# The core integration this one extends.
RAINBIRD_DOMAIN = "rainbird"
# Option key used by the core Rain Bird integration for the default run time.
RAINBIRD_ATTR_DURATION = "duration"
RAINBIRD_DEFAULT_DURATION_MINUTES = 6

CONF_RAINBIRD_ENTRY_ID = "rainbird_entry_id"

# Valve runtime limits, in seconds. Rain Bird runs zones in whole minutes,
# so the step is one minute. The max matches the core start_irrigation service.
RUNTIME_MIN_SECONDS = 60
RUNTIME_MAX_SECONDS = 1440 * 60
RUNTIME_STEP_SECONDS = 60

TIMEOUT_SECONDS = 20

# The device keeps reporting a zone as idle for a few seconds after it is
# started; keep the end time we computed ourselves for at least this long.
START_GRACE_PERIOD = timedelta(seconds=90)
# How long after a start before an "active" report is trusted as real.
CONFIRM_DELAY = timedelta(seconds=10)

# Ignore drift smaller than this when the controller re-reports the
# remaining run time, so the end-time sensor does not change every poll.
END_TIME_TOLERANCE = timedelta(seconds=30)
