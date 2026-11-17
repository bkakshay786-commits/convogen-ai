import machine
import dht
import time

# Initialize hardware
sensor = dht.DHT22(machine.Pin(4))
relay = machine.Pin(5, machine.Pin.OUT)

# Startup message
print("HADE_BOOT")

while True:
    try:
        sensor.measure()
        temp = sensor.temperature()
        hum = sensor.humidity()
        print("TEMP:{:.1f} HUM:{:.1f}".format(temp, hum))
    except Exception as e:
        # On read error, skip this cycle's relay update and continue
        time.sleep(2)
        continue

    # Relay control logic
    if temp > 30.0:
        relay.value(1)
        print("RELAY:ON")
    else:
        relay.value(0)
        print("RELAY:OFF")

    time.sleep(2)
