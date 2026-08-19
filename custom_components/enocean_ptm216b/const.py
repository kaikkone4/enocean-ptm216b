"""Constants for the EnOcean PTM 216B integration."""

DOMAIN = "enocean_ptm216b"
ENOCEAN_MANUFACTURER_ID = 0x03DA
# Casambi Technologies Oy's assigned Bluetooth SIG manufacturer ID. Used only
# to label the radio-census diagnostic's convenience fields (see
# radio_census.py and docs/evidence-findings.md's "User-observed Casambi
# advertisement" note) -- never used to filter, decode, or otherwise treat
# Casambi traffic specially anywhere else in this integration.
CASAMBI_MANUFACTURER_ID = 0x03C3

# Bus event fired by event.py's button event entities alongside their normal
# EventEntity trigger, for device_trigger.py's automation-editor triggers to
# filter on. Payload is exactly {device_id, button, action} -- device_id
# only, never an address or handle-derived identifier beyond what the
# device registry already exposes for this device. As of Phase 5D,
# ATTR_BUTTON carries a telegram.ButtonPattern's .value string (one of
# "A0"/"A1"/"B0"/"B1"/"A0+B0"/"A1+B1"), sourced from event.py's
# Ptm216bButtonEventEntity._pattern -- the same string device_trigger.py
# filters CONF_SUBTYPE on, so the two stay in sync mechanically.
EVENT_ENOCEAN_PTM216B = "enocean_ptm216b_event"
ATTR_BUTTON = "button"
ATTR_ACTION = "action"
