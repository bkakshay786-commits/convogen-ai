# H-ADE - Project Report: dht22-relay-control

Generated: 2026-11-10 23:18 (engine v2.1)

## Requirement
As described by the user: Turn on a relay when my room gets hotter than 30 degrees, using a DHT22 sensor on an ESP32.

Implemented with the supported palette as:
Poll a DHT22 sensor on GPIO 4 every 2 seconds. If the temperature climbs above 30 C, switch the relay on GPIO 5 ON; otherwise switch it OFF. Print the readings and the relay state via the serial terminal.

## Pin configuration
- DHT22 data: GPIO 4
- Relay input: GPIO 5 (active-HIGH)
- Read interval: 2s - Threshold: 30.0 C
(Full wiring in wiring.md; machine-readable spec in hardware.json.)

## Attempts
- Attempt 1: PASSED all behaviour assertions (6 readings, 6 relay writes)

## Validation status
- main.py: **Generated**
- main.py: **Digitally validated - syntax** (compiled clean)
- main.py: **Digitally validated - simulated behaviour** (stub DHT22 profile 27,29.5,31,33,28 C; 6 readings, 6 relay pin writes asserted)
- main.py: NOT physically verified - no real board run yet (that label needs the mpremote/board step).

## Bundle
main.py, hardware.json, bom.md, wiring.md, sim_config.json, report.md, README.md, serial_log.txt, attempts/ - also packed as project.zip.
