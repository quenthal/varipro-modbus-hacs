# Changelog

Versions are `YEAR.MONTH.N`, where N is the release number within the month.

## Unreleased

- Fixed: the main registers (mode, targets, temperatures, outputs) were read
  only every 20 seconds instead of every 10: Home Assistant starts a refresh
  slightly less than 10 seconds after the previous read, so every other
  refresh skipped them.
- Fixed: commands and polls now run one at a time as whole operations. Two
  simultaneous commands (for example fan only and the away dead zone from one
  script) could leave fan only active with the wrong dead zone.
- Fixed: leaving fan only while the controller does not answer no longer
  leaves it in away mode. The requested mode is written when the controller
  answers again, also across a Home Assistant restart (previously a restart
  could continue fan only).
- Fixed: fan only is rolled back and an error is shown if the manual fan speed
  cannot be written; before, it reported success with the fan left on auto.
- Fixed: removing the integration keeps the fan-only snapshot if the
  controller could not be restored, and logs the values to restore.

## 2026.11.1

- Fixed: after a network or gateway outage the integration could keep a second
  TCP connection open to the gateway (pymodbus' own background reconnect ran
  alongside the integration's reconnect). Gateways such as the Elfin EW11 accept
  only a few connections, which could slow down recovery.
- Fixed: a single missing response no longer closes the TCP connection. The
  connection now survives a short WiFi drop of the gateway; previously each
  timeout abandoned a session the gateway still held. pymodbus closes the
  connection itself after repeated missing responses.
- Fixed: when the gateway accepts a connection and closes it at once (its
  connection slots are full), the poll fails immediately with a clear log
  message instead of waiting for the response timeout.
- Fixed: Home Assistant 2026.11 deprecation warnings for the climate entity
  (`native_temperature_unit`, `native_current_temperature`,
  `native_target_temperature`). Older Home Assistant versions keep working.

## 2026.10.1

First version, ported from the varipro-modbus-mqtt bridge 2.0.

- Climate entity (off / cool / fan only, presets home / away, fan modes, hvac_action)
- Sensors, alarm binary sensors and the away dead zone number
- Fan only that continues across Home Assistant restarts and is restored when the integration is removed
- Config flow and options flow, English and Finnish translations, diagnostics
