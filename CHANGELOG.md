# Changelog

Versions are `YEAR.MONTH.N`, where N is the release number within the month.

## 2026.10.1

First version, ported from the varipro-modbus-mqtt bridge 2.0.

- Climate entity (off / cool / fan only, presets home / away, fan modes, hvac_action)
- Sensors, alarm binary sensors and the away dead zone number
- Fan only that continues across Home Assistant restarts and is restored when the integration is removed
- Config flow and options flow, English and Finnish translations, diagnostics
