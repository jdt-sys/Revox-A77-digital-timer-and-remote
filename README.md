# Revox A77 Digital Counter + Web Remote

Adds a digital tape counter and web-based remote control to a Revox A77.  
All mods are reversible and use existing holes where possible.

## Features

- Digital tape counter (OLED display)  
- Web-based remote control (WiFi)  
- Real-time counter updates over WebSocket  
- Safe transport control logic (no rewind → play abuse)  
- Non-invasive install  

## Hardware

- Raspberry Pi Pico W  

- 6-channel 24V relay board with opto-couplers  
  [AliExpress link](https://www.aliexpress.us/item/3256806094499129.html)

- 1.3" SH1106 OLED display  
  [AliExpress link](https://www.aliexpress.us/item/3256806019738729.html)

- Quadrature optical rotary encoder  
  [AliExpress link](https://www.aliexpress.us/item/3256809776375520.html)

- Buck converter (27V → 5V)  
- CAT5 cable (used for OLED + reset button)  

## Transport Control Notes

The A77 remote input will happily switch directly from rewind to play if commanded, which is not kind to tape.

This implementation enforces:

1. STOP  
2. Wait for reels to stop  
3. Then PLAY  

Handled in software to prevent tape abuse.

## Tape Counter

- Quadrature encoder driven from reel  
- PIO-based counting using state machine  
- Software rolling counter + persistence  
- Handles direction changes cleanly  

Future idea:
- “Self-learning” position mapping based on reel size / tape length  

## Software

- MicroPython on Pico W  
- PIO used for encoder counting  
- Async web server (AJAX + WebSocket updates)  
- Custom OLED font rendering

## A77 Remote Wiring

The A77 remote is controlled by shorting specific wires together (via relay contacts).

### Important

Red and brown must be connected for transport functions to latch.  
Without that connection, PLAY / FF / REW will only operate momentarily and won’t stay engaged.

### Connections

- **STOP:** red ↔ brown *(opens to stop / releases latch)*  
- **PLAY:** orange → yellow  
- **FAST FORWARD (FF):** black → gray  
- **REWIND (REW):** white → gray  

### Record (Unverified)

- **REC:** green → blue *(not tested)*  

### Notes

- All control is done by momentarily shorting the corresponding wires.  
- Use relays or opto-isolated outputs (recommended).  
- Do not switch directly between modes (e.g. REW → PLAY).  
  Always issue STOP first and allow reels to come to rest.  
- Latching is handled internally by the deck once red/brown are connected.

### References

- https://github.com/peterhinch/micropython-font-to-py — Font encoder
- https://github.com/jamon/pi-pico-pio-quadrature-encoder — PIO-based quadrature decoding  
- https://www.tapeheads.net/threads/revox-a77-wifi-remote-i-am-making-my-own.113867/ — similar DIY WiFi remote project and discussion  

## Repo Contents

- `/src` → MicroPython code  
- `/web` → HTML/JS interface  
- `/cad` → 3D printable parts (encoder mount, etc.)  

## TODO

- Clean up wiring at rear of deck  
- Better position tracking (reel size awareness)  

## Disclaimer

You can absolutely break things doing this.  
Double-check voltages, grounds, and relay isolation.

Also:  
Most faults encountered were self-inflicted. Don’t ask me how I know.
