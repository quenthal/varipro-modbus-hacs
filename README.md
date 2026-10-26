# VariPro for Home Assistant

Home Assistant integration for the **Chiller VariPro room controller** (and the
GRAND / BOX / STUDIO VariPro fan coil units it drives), over Modbus TCP.

*Suomeksi: ks. [alla](#suomeksi).*

The controller appears as one device with a climate entity, diagnostic sensors
and alarm sensors. Unlike a plain `modbus` climate configuration, the
integration understands the controller's **away mode** (its own target
temperature and extended dead zone) and adds a **fan only** mode the controller
does not have natively.

## Features

- **Climate entity**: off / cool / fan only, presets *home* and *away*, fan
  speed auto / low / medium / high, target temperature, and `hvac_action`
  (cooling / heating / fan / idle) from the controller's PID outputs.
- **Fan only**: implemented as away mode + 15 °C dead zone + manual fan speed,
  so cooling never starts. The previous state (mode, dead zone, fan speed) is
  stored and restored when fan only ends. Fan only continues across Home
  Assistant restarts; removing the integration restores the previous state.
- **Sensors**: room temperature, active setpoint, home/away targets, supply and
  return water temperatures, fan speed, cooling and heating outputs,
  application state, fan control.
- **Alarms** as problem binary sensors: condensation, fan speed missing,
  water sensor faults, parameter file, filter cleaning.
- **Away dead zone** as a number entity.
- Bus friendly: one connection, at least 200 ms between requests, grouped
  register reads with staggered intervals, no periodic re-writes. Short
  gateway drops (common with WiFi gateways) do not make the entities flap.

## Requirements

- Home Assistant 2026.9 or newer.
- The controller's RS-485 bus behind a Modbus TCP gateway (for example a
  Hi-Flying Elfin EW11 / EW41 in Modbus TCP ↔ RTU mode). Serial settings must
  match the controller's Modbus menu.
- Only one Modbus client should talk to the gateway at a time.

## Installation

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/quenthal/varipro-modbus-hacs` as an *Integration*.
2. Install **VariPro** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → VariPro*, and enter the
   gateway host, port (502) and the controller's Modbus address (1).

Options: minimum and maximum target temperature (default 18–24 °C).

## Notes

- **Fan speed and fan only**: by default the controller returns a manually
  selected fan speed to *auto* after 1 hour (controller settings → fan
  control). In fan only, *auto* stops the fan. For longer fan-only use, set the
  timer to *no limit* on the controller.
- **Heating** cannot be selected over Modbus; the controller switches between
  cooling and heating by itself. Heating shows up as `hvac_action: heating`.
- Changes made on the controller's panel are respected: switching mode on the
  panel ends fan only, and the integration never re-writes values on its own.
- Register 4x00202 (away dead zone) is documented in the manual's text but not
  in its register table; verified on controller firmware 0.36.

## Migrating from varipro-modbus-mqtt

The MQTT bridge ([varipro-modbus-mqtt](https://github.com/quenthal/varipro-modbus-mqtt))
and this integration should not run at the same time.

1. Turn fan only off, then stop the bridge (`systemctl stop varipro`).
2. Remove the bridge's retained discovery topics (`homeassistant/+/varipro*/config`
   and `homeassistant/climate/varipro/config`), or delete the MQTT device in HA.
3. Add this integration. If you rename the new entities to the old entity IDs,
   automations and dashboards keep working; history is not carried over.

## Suomeksi

Home Assistant -integraatio Chiller VariPro -huonesäätimelle Modbus TCP:n yli.
Asennus HACS:n kautta (custom repository), asetukset HA:n käyttöliittymästä:
yhdyskäytävän osoite, portti ja säätimen Modbus-osoite. Toiminnot vastaavat
[MQTT-siltaa](https://github.com/quenthal/varipro-modbus-mqtt): poissa-tila,
fan only (tuuletus), hälytykset ja diagnostiikka. Käyttöliittymä on suomeksi ja
englanniksi.

## Disclaimer

Not affiliated with or endorsed by Chiller Oy. VariPro and the Chiller logo are
trademarks of Chiller Oy and are used only to identify the supported device.
Use at your own risk; changing controller parameters may affect the warranty.

## License

MIT, see [LICENSE](LICENSE).
