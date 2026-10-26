"""Constants for the VariPro integration."""

from datetime import timedelta

DOMAIN = "varipro"
MANUFACTURER = "Chiller"
MODEL = "VariPro room controller"

CONF_SLAVE = "slave"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"

DEFAULT_PORT = 502
DEFAULT_SLAVE = 1
DEFAULT_MIN_TEMP = 18.0
DEFAULT_MAX_TEMP = 24.0

SCAN_INTERVAL = timedelta(seconds=10)
STORAGE_VERSION = 1
