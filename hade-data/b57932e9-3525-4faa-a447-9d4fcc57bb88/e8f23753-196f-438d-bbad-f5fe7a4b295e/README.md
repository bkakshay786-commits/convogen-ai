# Smart Greenhouse System - an H-ADE generated project

## What this is
MicroPython firmware for an ESP32, generated from a validated specification:

Project: Smart Greenhouse System (board: ESP32, read every 2s)
- DHT22 temperature/humidity sensor [dht22 id=sensor1]: data GPIO 4
- Relay module - ventilation fan [relay id=fan]: control GPIO 5
- LED - low-humidity alarm [led id=alarm]: control GPIO 18
- Rule: RELAY (Relay module - ventilation fan) ON when temperature > 32C, otherwise OFF
- Rule: LED (LED - low-humidity alarm) ON when humidity < 40%, otherwise OFF

Firmware status: **Digitally validated** - the firmware passed syntax checks and simulated behaviour tests against its specification (see report.md).
It is NOT physically verified - that label requires a run on a real ESP32 board.

## Files
- `main.py` - the firmware (copy this to the board)
- `project.json` - the validated specification this project was generated from
- `hardware.json` - components, pin map, parameters
- `bom.md` - what to buy
- `wiring.md` - how to wire it
- `sim_config.json` - the test scenario used for validation
- `report.md` - test evidence and validation labels
- `serial_log.txt` - serial output of the final simulated run
- `attempts/` - every firmware version the AI generated

## Flash it to a real ESP32
1. Put MicroPython on the board (once): download the ESP32_GENERIC firmware from micropython.org/download, then:
   ```
   pip install esptool mpremote
   esptool --port COMx erase_flash
   esptool --port COMx write_flash -z 0x1000 firmware.bin
   ```
   (Replace COMx with your board's port; on Linux it looks like /dev/ttyUSB0.)
2. Copy the firmware and run it:
   ```
   mpremote connect COMx cp main.py :main.py
   mpremote connect COMx reset
   ```
3. Watch the serial output: `mpremote connect COMx` (or open the board in Thonny). You should see HADE_BOOT, then TEMP/HUM lines and RELAY:ON/OFF, LED:ON/OFF every 2 seconds.
