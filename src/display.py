import framebuf
from writer import Writer
import segments


class FBWrapper(framebuf.FrameBuffer):
    def __init__(self, buf, width, height, fmt):
        super().__init__(buf, width, height, fmt)
        self.width = width
        self.height = height


class Display:
    def __init__(self, oled, width, height, text_x=10, text_y=4):
        self.oled = oled
        self.width = width
        self.height = height
        self.text_x = text_x
        self.text_y = text_y

        self.buf = bytearray(width * height // 8)
        self.fb = FBWrapper(self.buf, width, height, framebuf.MONO_VLSB)
        self.writer = Writer(self.fb, segments)

    def draw(self, text):
        self.fb.fill(0)
        Writer.set_textpos(self.fb, self.text_y, self.text_x)
        self.writer.printstring(text)
        self.oled.blit(self.fb, 0, 0)
        self.oled.show()
