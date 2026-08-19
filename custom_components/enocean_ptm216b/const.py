"""Constants for the EnOcean PTM 216B integration."""

DOMAIN = "enocean_ptm216b"
ENOCEAN_MANUFACTURER_ID = 0x03DA

# Bus event fired by event.py's button event entities alongside their normal
# EventEntity trigger, for device_trigger.py's automation-editor triggers to
# filter on. Payload is exactly {device_id, button, action} -- device_id
# only, never an address or handle-derived identifier beyond what the
# device registry already exposes for this device.
EVENT_ENOCEAN_PTM216B = "enocean_ptm216b_event"
ATTR_BUTTON = "button"
ATTR_ACTION = "action"
