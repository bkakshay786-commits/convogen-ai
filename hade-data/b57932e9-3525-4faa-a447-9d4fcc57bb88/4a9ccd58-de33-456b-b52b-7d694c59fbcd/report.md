# H-ADE - Project Report: smart-greenhouse-monitor

Generated: 2026-11-10 23:43 (engine v3.0)

## Requirement
As described by the user: Create a smart greenhouse monitoring system using an ESP32.

Requirements:
1. Monitor temperature and humidity using a DHT22 sensor.
2. Turn ON a ventilation fan when temperature exceeds 32°C.
3. Turn OFF the fan when temperature returns to 32°C or below.
4. Turn ON an LED alarm when humidity falls below 40%.
5. Turn OFF the LED when humidity returns to 40% or above.
6. Read the sensor every 2 seconds.

Choose the required components from your supported hardware registry, assign valid ESP32 GPIO pins, and generate the complete hardware specification.

Generate firmware from the validated specification. Independently generate simulator test profiles and behavioral assertions from the same specification.

Validate both serial output and actual GPIO pin activity.

Export the complete project ZIP, including project.json, hardware.json, main.py, BOM, wiring instructions, simulator configuration, validation report, and test evidence.

## Specification (project.json)
Produced by the Spec Agent and checked by the Registry Validator (round 1 of 3):

Project: Smart Greenhouse Monitoring System (board: ESP32, read every 2s)
- DHT22 temperature/humidity sensor [dht22 id=sensor1]: data GPIO 4
- Relay module - ventilation fan [relay id=fan]: control GPIO 5
- LED - low-humidity alarm [led id=alarm]: control GPIO 18
- Rule: RELAY (Relay module - ventilation fan) ON when temperature > 32C, otherwise OFF
- Rule: LED (LED - low-humidity alarm) ON when humidity < 40%, otherwise OFF

## Attempts
- Attempt 1: PASSED all behaviour assertions (8 readings, 16 pin writes)

## Validation status
- main.py: **Generated**
- main.py: **Digitally validated - syntax** (compiled clean)
- main.py: **Digitally validated - simulated behaviour** (spec-derived test profile 29C/48%, 35C/48%, 29C/32%, 35C/32%, 29C/48%; 8 readings, 16 actuator pin writes asserted)
- main.py: NOT physically verified - no real board run yet (that label needs the mpremote/board step).

## Bundle
main.py, project.json, hardware.json, bom.md, wiring.md, sim_config.json, report.md, README.md, serial_log.txt, attempts/ - also packed as project.zip.
