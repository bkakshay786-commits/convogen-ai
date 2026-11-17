import machine
import dht
import time

print("HADE_BOOT")

temp_sensor = dht.DHT22(machine.Pin(4))
cooling_relay = machine.Pin(5, machine.Pin.OUT)

while True:
    try:
        temp_sensor.measure()
        temp = temp_sensor.temperature()
        hum = temp_sensor.humidity()
        print("TEMP:{:.1f} HUM:{:.1f}".format(temp, hum))
        if temp > 30:
            cooling_relay.value(1)
            print("RELAY:ON")
        else:
            cooling_relay.value(0)
            print("RELAY:OFF")
    except Exception:
        pass
    time.sleep(2)
