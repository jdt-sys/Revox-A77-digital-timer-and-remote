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
- SH1106 128x64 OLED (cheap AliExpress special)  
- Quadrature encoder (belt-driven from take-up reel)  
- 6-channel relay board with opto-couplers  
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

### References

- https://github.com/peterhinch/micropython-font-to-py  
- https://github.com/jamon/pi-pico-pio-quadrature-encoder — PIO-based quadrature decoding  
- https://www.tapeheads.net/threads/revox-a77-wifi-remote-i-am-making-my-own.113867/ — similar DIY WiFi remote project and discussion  

## Repo Contents

- `/src` → MicroPython code  
- `/web` → HTML/JS interface  
- `/cad` → 3D printable parts (encoder mount, etc.)  

## TODO

- Clean up wiring at rear of deck  
- Finalize mounting positions  
- Improve UI (less 1998)  
- Optional IR remote learning  
- Better position tracking (reel size awareness)  

## Disclaimer

You can absolutely break things doing this.  
Double-check voltages, grounds, and relay isolation.

Also:  
Most faults encountered were self-inflicted. Don’t ask me how I know.
