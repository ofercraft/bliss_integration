"""Constants for the Bliss blinds integration."""
from __future__ import annotations

import logging
import re

from homeassistant.const import Platform

DOMAIN: str = "bliss"
PLATFORMS = [Platform.COVER]

CONF_NAME = "name"
CONF_MAC = "mac"
CONF_PASSWORD = "password"
CONF_RANGE_MAX = "range_max"

DEFAULT_PASSWORD = "123456"
DEFAULT_RANGE_MAX = 1000

BLISS_NAME_PATTERN = re.compile(r"^(HD|TS)\\d{4}$")

LOGGER = logging.getLogger(__package__)
