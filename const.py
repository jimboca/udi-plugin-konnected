"""Shared constants for udi-plugin-konnected."""

# IoX UOMs
UOM_INDEX = 25
UOM_ONOFF = 78
UOM_BOOLEAN = 2
UOM_RAW = 56

# Door state (ST) — matches ratgdo-style GDO labels
IX_DOOR_CLOSED = 0
IX_DOOR_OPEN = 1
IX_DOOR_STOPPED = 2
IX_DOOR_CLOSING = 3
IX_DOOR_OPENING = 4
IX_DOOR_UNKNOWN = 9

# Binary-ish index states (obstruction / lock / motion / synced)
IX_CLEAR = 0
IX_ACTIVE = 1
IX_UNKNOWN = 101

IX_LOCK_UNLOCKED = 0
IX_LOCK_LOCKED = 1

IX_MOTION_CLEAR = 0
IX_MOTION_DETECTED = 1

IX_SYNCED_NO = 0
IX_SYNCED_YES = 1

# Device type (GV5)
IX_DEVTYPE_UNKNOWN = 0
IX_DEVTYPE_BLAQ = 1
IX_DEVTYPE_WHITE = 2

# Online / on-off
ISY_FALSE = 0
ISY_TRUE = 1
ISY_OFF = 0
ISY_ON = 100
ISY_ONOFF_UNKNOWN = 101

# Custom param keys
PARAM_HOSTS = 'hosts'
PARAM_CHANGE_NODE_NAMES = 'change_node_names'

# Custom NS data keys (per device node)
NS_HOST = 'host'
NS_DEVICE_TYPE = 'device_type'
NS_HAS_LIGHT = 'has_light'
NS_FRIENDLY_NAME = 'friendly_name'

# Defaults
DEFAULT_SSE_READY_TIMEOUT = 15.0
DEFAULT_HTTP_TIMEOUT = 8.0
DEFAULT_RECONNECT_DELAY = 5.0
DEFAULT_MDNS_SECONDS = 5.0
MOTION_EVENT_MASK_TIME = 30  # ignore retained-like motion bursts after start
MOTION_STATE_RESET_TIME = 60  # clear motion if device never sends OFF
# After this many consecutive shortPolls while SSE is down, mark door/sensor
# drivers Unknown (timing follows the user-set PG3 shortPoll interval).
OFFLINE_STALE_SHORTPOLLS = 2
# Wait this long after SSE drops before showing a per-device "reconnecting"
# Notice. Brief resets (common on blaQ) reconnect in ~5s; without a grace
# window the Notice flaps and a PG3 Notices.load echo can leave it stuck.
NOTICE_OFFLINE_GRACE_SEC = 30.0

# Notice keys
NOTICE_HOSTS = 'hosts'
NOTICE_DISCOVER = 'discover'
NOTICE_MDNS = 'mdns'
NOTICE_MQTT = 'mqtt'
NOTICE_DEVICE_PREFIX = 'device_'
NOTICE_UNKNOWN_PREFIX = 'unknown_'
