# H-ADE - Project Report: server-shelf-cooling

Generated: 2026-11-11 14:12 (engine v3.3)

## Requirement
As described by the user: Server shelf cooling: read a DHT22 every 2 seconds and switch a relay on when the temperature rises above 30 degrees.

## Specification (project.json)
Produced by the Spec Agent and checked by the Registry Validator (round 1 of 3):

Project: Server Shelf Cooling System (board: ESP32, read every 2s)
- DHT22 temperature sensor [dht22 id=temp_sensor]: data GPIO 4
- Relay module - cooling fan [relay id=cooling_relay]: control GPIO 5
- Rule: RELAY (Relay module - cooling fan) ON when temperature > 30C, otherwise OFF

## Attempts
- Attempt 1: PASSED all behaviour assertions (6 readings, 6 pin writes)

## Validation status
- main.py: **Generated**
- main.py: **Digitally validated - syntax** (compiled clean)
- main.py: **Digitally validated - simulated behaviour** (spec-derived test profile 27C/55%, 33C/55%, 27C/55%; 6 readings, 6 actuator pin writes asserted)
- main.py: NOT physically verified - no real board run yet (that label needs the mpremote/board step).

## Bundle
main.py, project.json, hardware.json, bom.md, wiring.md, sim_config.json, report.md, README.md, serial_log.txt, attempts/ - also packed as project.zip.
