# SPDX-FileCopyrightText: 2022 Jamon Terrell <github@jamonterrell.com>
# SPDX-License-Identifier: MIT
# https://github.com/jamon/pi-pico-pio-quadrature-encoder/


from rp2 import asm_pio

@asm_pio(autopush=True, push_thresh=28)
def encoder():
    wrap_target()

    wait(0, pin, 0)
    jmp(pin, "clk_low_data_high")

    mov(x, invert(x))
    jmp(x_dec, "inc1")
    label("inc1")
    mov(x, invert(x))

    label("clk_low_data_high")
    jmp(x_dec, "dec1")
    label("dec1")

    wait(1, pin, 0)
    jmp(pin, "clk_high_data_high")

    jmp(x_dec, "dec2")
    label("dec2")

    label("clk_high_data_high")
    mov(x, invert(x))
    jmp(x_dec, "inc2")
    label("inc2")
    mov(x, invert(x))

    wrap()


def read_raw_count(sm):
    sm.exec("in_(x, 28)")
    return sm.get()
