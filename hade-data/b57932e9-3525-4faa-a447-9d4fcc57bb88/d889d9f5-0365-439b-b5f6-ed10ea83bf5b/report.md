# H-ADE - Project Report: smart-greenhouse-system

Generated: 2026-11-11 00:05 (engine v3.0)

## Requirement
As described by the user: Create a smart greenhouse system using ESP32.

Use a DHT22 sensor to monitor temperature and humidity.

Automatically turn ON a ventilation fan when temperature exceeds 32°C and turn it OFF when temperature is 32°C or below.

Turn ON a red LED alarm when humidity falls below 40% and turn it OFF when humidity reaches 40% or above.

Use a 2-second sampling interval.

Automatically select supported components, assign valid GPIO pins, generate firmware, independently generate behavioral tests, simulate the firmware, validate serial output and actual GPIO activity, and export the complete project ZIP.

## Specification (project.json)
Produced by the Spec Agent and checked by the Registry Validator (round 1 of 3):

Project: Smart Greenhouse System (board: ESP32, read every 2s)
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
