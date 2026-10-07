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

# Re-ask the controller for its state (seasonal adjust) this often while idle.
CONTROLLER_STATE_IDLE_REFRESH = timedelta(minutes=30)

# A run tracked by Home Assistant that disappears this long before its end
# was stopped early (for "Run all zones" this ends the sequence).
EARLY_STOP_MARGIN = timedelta(seconds=60)

# Option: disable the core Rain Bird zone switches the valves replace.
CONF_DISABLE_RAINBIRD_SWITCHES = "disable_rainbird_switches"
DEFAULT_DISABLE_RAINBIRD_SWITCHES = True

# Zone flow rate limits (L/min or gal/min).
FLOW_RATE_MAX = 500
FLOW_RATE_STEP = 0.1

SERVICE_START_ZONE = "start_zone"
ATTR_DURATION = "duration"

# Water budget "program" code for controllers with one controller-wide value.
LCR_BUDGET = 0xFF
SEASONAL_ADJUST_MIN = 10

# How far a zone's actual start may drift from its scheduled or expected start
# and still be matched to it when working out time remaining.
SCHEDULE_TOLERANCE = timedelta(minutes=2)

# Rain skip options.
CONF_WEATHER_ENTITY = "weather_entity"
CONF_RAIN_CHANCE = "rain_chance"
CONF_RAIN_CHECK_TIME = "rain_check_time"
CONF_RAIN_DELAY_DAYS = "rain_delay_days"
DEFAULT_RAIN_CHANCE = 60
DEFAULT_RAIN_CHECK_TIME = "04:00:00"
DEFAULT_RAIN_DELAY_DAYS = 1
RAINY_CONDITIONS = {"rainy", "pouring", "lightning-rainy", "snowy-rainy"}
EVENT_RAIN_SKIP = f"{DOMAIN}_rain_skip"

# Run all zones and cycle and soak options.
CONF_RUN_ALL_ZONES = "run_all_zones"
CONF_CYCLE_MINUTES = "cycle_minutes"
CONF_SOAK_MINUTES = "soak_minutes"
DEFAULT_SOAK_MINUTES = 30
ATTR_CYCLE_AND_SOAK = "cycle_and_soak"

# Blowout (winterizing with an air compressor): short bursts per zone with a
# rest between them so the compressor can recover.
CONF_BLOWOUT_ZONES = "blowout_zones"
CONF_BLOWOUT_CYCLES = "blowout_cycles"
CONF_BLOWOUT_ON_MINUTES = "blowout_on_minutes"
CONF_BLOWOUT_REST_SECONDS = "blowout_rest_seconds"
DEFAULT_BLOWOUT_CYCLES = 10
DEFAULT_BLOWOUT_ON_MINUTES = 1
DEFAULT_BLOWOUT_REST_SECONDS = 150
SERVICE_BLOWOUT = "blowout"
ATTR_ZONES = "zones"
ATTR_CYCLES = "cycles"
ATTR_ON_TIME = "on_time"
ATTR_REST = "rest"
