import machine
import dht
import time

print("HADE_BOOT")

sensor = dht.DHT22(machine.Pin(4))
fan = machine.Pin(5, machine.Pin.OUT)
alarm = machine.Pin(18, machine.Pin.OUT)

while True:
    try:
        sensor.measure()
        temp = sensor.temperature()
        hum = sensor.humidity()
        print("TEMP:{:.1f} HUM:{:.1f}".format(temp, hum))
        if temp > 32:
            fan.value(1)
            print("RELAY:ON")
        else:
            fan.value(0)
            print("RELAY:OFF")
        if hum < 40:
            alarm.value(1)
            print("LED:ON")
        else:
            alarm.value(0)
            print("LED:OFF")
    except Exception:
        pass
    time.sleep(2)
