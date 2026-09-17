

# Built in Python toolbox for anything time related.
# Needed here for time.sleep() (pausing briefly each loop) and
# time.time() checking how much real time has passed used for
# the sensor-timing logic further down
import time

# gpiozero is the standard library for controlling a Raspberry Pi's
# physical GPIO pins. PWMOutputDevice specifically is the tool that
# lets us rapidly pulse a pin on/off to simulate a variable voltage
# (Pulse Width Modulation) — this is what drives our MOSFET
from gpiozero import PWMOutputDevice

# pyserial — lets Python open, read from, and write to a USB/serial
# connection. This is the channel talking to the ESP32
import serial

# The library written specifically for our CCS811B air quality chip.
# It knows the exact commands/data formats that chip expects, so we
# don't have to send raw I2C register commands ourselves
import adafruit_ccs811

# 'board' gives access to the Pi's hardware communication buses
# like I2C  different from gpiozero, which deals with individual
# pins doing individual jobs (PWM, buttons, etc)
import board

# Opens the I2C bus the two-wire communication channel (SDA/SCL)
# the CO2 sensor is physically wired to stored in a variable so we
# can reuse this same open connection later
i2c = board.I2C()

# Builds the actual sensor object, using the CCS811 blueprint from
# the adafruit_ccs811 library. We hand it 'i2c' so it knows exactly
# which communication channel to use to reach the physical chip.
ccs = adafruit_ccs811.CCS811(i2c)

# Opens the USB/serial connection to the ESP32
# "/dev/ttyUSB0" = the exact address of that USB port on the Pi
# 115200 = baud rate (transmission speed) must match Serial.begin(115200) on the ESP32 exactly
# timeout=1 = if we ask to read and nothing arrives, give up after 1 second rather than freezing forever
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)

# Creates our PWM connection to GPIO 18 which is wired to the
# MOSFET's TRIG pin. frequency=1000 means the pin pulses on/off
# 1000 times per second fast enough that the fan motor doesn't
# audibly buzz from the switching itself
Mosfet_Signal = PWMOutputDevice(18, frequency=1000)

# Tracks the current PWM duty cycle as a fraction from 0.0 (off)
# to 1.0 (fully on/24V since this fan is rated for the full 24V
# Starts at 0.5 (50% = 12V) as a safe default speed on boot.
current_Duty = 0.5

# Remembers the last moment in seconds since 1970 we sent sensor
# data to the ESP32 starting at 0 guarantees the very first loop
# pass counts as "enough time has passed," so we send immediately
# on startup rather than waiting 1.5 seconds first
last_Sensor_Send = 0

# Actually applies our starting duty cycle to the real GPIO pin
# so the fan starts running the instant the script launches
Mosfet_Signal.value = current_Duty


#Function that takes a new requested duty cycle clamps it to a safe range, and applies it to the fan
def Set_New_Voltage(New_Duty):
    # Without this Python would create a brand-new function-only
    # copy of current_Duty instead of changing the real one shared
    # by the rest of the script
    global current_Duty

    # Safety clamp, read inside-out:
    # min(1, New_Duty)  never allow a value above 1.0 (100% = full 24V)
    # max(0.0, ...)    never allow the result to go below 0.0 (0V)
    # No matter what New_Duty is asked for, current_Duty always
    # lands somewhere between 0.0 and 1.0.
    current_Duty = max(0.0, min(1, New_Duty))

    # Sends the now-safe value out to the physical pin this is
    # the line that actually changes the fan's real speed
    Mosfet_Signal.value = current_Duty

    # Just a debug message printed on the Pi's own screen/terminal
    # not sent anywhere, purely for us to watch while testing
    print(f"Effective Output: {current_Duty * 24:.1f}V")

    # Tells the ESP32 the new speed, so its display stays accurate
    # current_Duty * 100 converts our 0.0-1.0 fraction into a real
    # 0-100 percentage. :.0f rounds it to a whole number (no decimals)
    # since the display doesn't need fractional percent
    # \n marks "this message is complete" for the ESP32 to detect
    # .encode() converts the text into raw bytes since that's the
    # only thing that can actually travel down a USB cable
    ser.write(f"FAN:{current_Duty * 100:.0f}\n".encode())


# try/except wraps the whole loop so that if the script gets
# manually stopped, we can catch that moment and safely turn the
# fan off, instead of it potentially getting stuck running
try:
    while True:  # runs forever, until interrupted

        # Checks if there's unread data sitting in the USB buffer 
        # i.e. has the ESP32 sent us anything since we last checked?
        if ser.in_waiting > 0:

            # Reads one full line of incoming text, converts the raw
            # bytes into a readable string (.decode), and trims off
            # any trailing whitespace/newline characters (.rstrip)
            line = ser.readline().decode('utf-8').rstrip()

            # Compares the received text against our three known
            # commands, and calls Set_New_Voltage with the
            # appropriate adjustment for each
            if line == "UP":
                Set_New_Voltage(current_Duty + 0.1)
            elif line == "DOWN":
                Set_New_Voltage(current_Duty - 0.1)
            elif line == "RESET":
                Set_New_Voltage(0.0)

        # Grabs "the current moment," as a single ever-increasing
        # number (seconds since 1970). Used below to measure elapsed
        # time without ever pausing/freezing the whole script.
        now = time.time()

        # Two conditions both must be true:
        # (1) has at least 1.5 real seconds passed since our last
        #     sensor send? (subtracting two "seconds since 1970"
        #     numbers gives you the elapsed time between them)
        # (2) has the sensor chip actually finished a fresh reading?
        #     (ccs.data_ready avoids reading stale/incomplete data)
        if now - last_Sensor_Send >= 1.5 and ccs.data_ready:

            # ccs.eco2 = the sensor's estimated CO2 reading, as a number.
            # Sent as its own separate message, prefixed "CO2:" so the
            # ESP32 can tell this apart from "FAN:" or "TVOC:" messages.
            ser.write(f"CO2:{ccs.eco2}\n".encode())

            # ccs.tvoc = total volatile organic compounds reading,
            # sent the same way, as its own separate message.
            ser.write(f"TVOC:{ccs.tvoc}\n".encode())

            # Updates our "stopwatch" — remembering this exact moment
            # as the new reference point, so the next elapsed-time
            # check measures forward correctly from here.
            last_Sensor_Send = now

        # Tiny pause each loop cycle. Without this, the Pi would
        # check for serial data and re-check the clock constantly,
        # thousands of times per second, wasting processing power
        # for no real benefit.
        time.sleep(0.05)

# Runs only if the script is manually stopped (e.g. Ctrl+C).
except KeyboardInterrupt:
    # Forces the pin straight to 0V, so the fan doesn't get left
    # running if the script is interrupted mid-execution.
    Mosfet_Signal.value = 0
    

    
