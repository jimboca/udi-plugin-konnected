"""Local REST/SSE client for Konnected ESPHome garage door devices."""

from .device import KonnectedDevice
from .mdns import browse_konnected_devices, browse_konnected_gdos
from .models import DeviceType, classify_device, semantic_entity_map
from .mqtt_health import check_mqtt_client_cert, log_mqtt_health

__all__ = [
    'KonnectedDevice',
    'DeviceType',
    'browse_konnected_devices',
    'browse_konnected_gdos',
    'classify_device',
    'semantic_entity_map',
    'check_mqtt_client_cert',
    'log_mqtt_health',
]
