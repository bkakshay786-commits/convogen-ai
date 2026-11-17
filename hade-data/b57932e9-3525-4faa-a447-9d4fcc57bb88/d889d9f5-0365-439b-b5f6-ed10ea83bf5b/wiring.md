# Wiring - smart-greenhouse-system

## DHT22 temperature/humidity sensor
- VCC -> ESP32 3V3
- GND -> ESP32 GND
- DATA -> GPIO 4 (10k pull-up resistor between DATA and 3V3; many DHT22 modules already include it)

## Relay module - ventilation fan
- IN -> GPIO 5
- VCC -> 5V (or 3V3, per your module), GND -> GND
- The relay is ACTIVE-HIGH: GPIO HIGH = relay ON.

## LED - low-humidity alarm
- GPIO 18 -> 220 ohm resistor -> LED anode (long leg)
- LED cathode (short leg) -> ESP32 GND

## Safety
- Keep any relay's load side (COM/NO/NC) UNWIRED while testing firmware.
- A relay can switch mains voltage, which can kill. Until you are experienced, switch only low-voltage DC loads (LED strips, small fans, pumps under 24V).

Validation: **Generated** (standard wiring for the supported palette).
