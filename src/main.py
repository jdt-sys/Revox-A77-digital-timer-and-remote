# SPDX-FileCopyrightText: 2022 Jamon Terrell <github@jamonterrell.com>
# SPDX-License-Identifier: MIT

from rp2 import StateMachine
from machine import Pin, SPI
import uasyncio as asyncio
import sh1106
import time

from encoder_pio import encoder, read_raw_count
from display import Display
from counter_websocket import CounterWebSocketServer
from RollingCounter import RollingCounter


ENCODER_CLK_PIN = 14
ENCODER_DT_PIN = 15
RESET_PIN = 16

SPI_ID = 0
SPI_SCK_PIN = 2
SPI_MOSI_PIN = 3
OLED_DC_PIN = 5
OLED_RES_PIN = 4
OLED_CS_PIN = 1

OLED_WIDTH = 128
OLED_HEIGHT = 64

TEXT_X = 18
TEXT_Y = 21

DEBOUNCE_MS = 20
PULSE_MS = 200
WAIT_STEP_MS = 50

WIFI_SSID = "dutoithomenew"
WIFI_PASSWORD = "miranda546"

TRANSPORT_PINS = {
    "play": 6,
    "stop": 11,
    "rwd": 7,
    "ff": 8,
    "pause": 10,
    "rec": 9,
}


async def main():
    # --- SPI + OLED ---
    spi = SPI(
        SPI_ID,
        baudrate=10_000_000,
        polarity=0,
        phase=0,
        sck=Pin(SPI_SCK_PIN),
        mosi=Pin(SPI_MOSI_PIN),
    )

    oled = sh1106.SH1106_SPI(
        OLED_WIDTH,
        OLED_HEIGHT,
        spi,
        Pin(OLED_DC_PIN),
        Pin(OLED_RES_PIN),
        Pin(OLED_CS_PIN),
    )

    display = Display(oled, OLED_WIDTH, OLED_HEIGHT, TEXT_X, TEXT_Y)

    # --- PIO Encoder ---
    sm = StateMachine(
        1,
        encoder,
        freq=125_000_000,
        in_base=Pin(ENCODER_CLK_PIN),
        jmp_pin=Pin(ENCODER_DT_PIN),
    )
    sm.active(1)

    # --- Reset button ---
    reset_btn = Pin(RESET_PIN, Pin.IN, Pin.PULL_UP)

  
    # --- Transport GPIO setup ---
    transport_gpio = {
        name: Pin(pin_num, Pin.OUT, value=0)
        for name, pin_num in TRANSPORT_PINS.items()
    }

    # --- Web interface ---
    web = CounterWebSocketServer(WIFI_SSID, WIFI_PASSWORD, title="Revox Counter")

    state = {
        "last_text": "",
        "last_btn": reset_btn.value(),
    }

    def refresh_counter():
        raw = read_raw_count(sm)
        counter.update(raw)
        counter.tick()

    def update_outputs():
        text = counter.formatted()
        if text != state["last_text"]:
            print(text)
            display.draw(text)
            web.set_text(text)
            state["last_text"] = text

    def service_counter():
        refresh_counter()
        update_outputs()

    def pulse_pin(pin, ms=PULSE_MS):
        print("-> mapped to GPIO:", pin)
        print("-> initial value:", pin.value())

        print("-> setting HIGH")
        pin.on()
        print("-> value now:", pin.value())

        time.sleep_ms(ms)

        print("-> setting LOW")
        pin.off()
        print("-> value now:", pin.value())

    def stop_transport_if_moving():
        moving = counter.counterMovedInLastSecond()
        print("playing =", moving)

        if not moving:
            return

        stop_pin = transport_gpio.get("stop")
        if stop_pin is None:
            print("-> STOP pin missing")
            return

        print("stop first")
        pulse_pin(stop_pin)

        while counter.counterMovedInLastSecond():
            print("waiting..")
            service_counter()
            time.sleep_ms(WAIT_STEP_MS)

    def on_transport(cmd):
        print("\n=== TRANSPORT EVENT ===")
        print("cmd:", cmd)

        if cmd == "reset":
            print("-> RESET requested")
            counter.reset()
            update_outputs()
            print("=== DONE ===\n")
            return
        
        
        if cmd.startswith("goto:"):
            target = int(cmd.split(":")[1])
            print(f"-> GOTO requested: {target}")
            counter.goto(target)
            print("=== DONE ===\n")
            return

        pin = transport_gpio.get(cmd)
        if pin is None:
            print("-> UNKNOWN COMMAND (ignored)")
            print("=== DONE ===\n")
            return

        if cmd != "stop":
            stop_transport_if_moving()

        pulse_pin(pin)

        print("=== DONE ===\n")
        


    # --- Counter logic ---
    counter = RollingCounter(steps_per_count=300, on_transport=on_transport)


    web.set_transport_callback(on_transport)
    await web.start()

    while True:
        service_counter()

        # --- Button debounce ---
        btn = reset_btn.value()
        if state["last_btn"] == 1 and btn == 0:
            await asyncio.sleep_ms(DEBOUNCE_MS)
            if reset_btn.value() == 0:
                counter.reset()
                print("Physical reset")
                update_outputs()

        state["last_btn"] = btn

        await asyncio.sleep_ms(10)


asyncio.run(main())
