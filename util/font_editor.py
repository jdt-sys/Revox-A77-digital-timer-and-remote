import ast
import importlib.util
import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog


class BitmapFontModel:
    def __init__(self):
        self.path = None
        self.source_text = ""
        self.module = None
        self.font_bytes = b""
        self.index_bytes = b""
        self.min_ch = 32
        self.max_ch = 126
        self.height = 0
        self.hmap = True
        self.reverse = False
        self.glyphs = {}

    def load_py_font(self, path):
        self.path = path
        with open(path, "r", encoding="utf-8") as f:
            self.source_text = f.read()

        spec = importlib.util.spec_from_file_location("user_font_module", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.module = module

        self.font_bytes = bytes(module._font)
        self.index_bytes = bytes(module._index)
        self.min_ch = module.min_ch()
        self.max_ch = module.max_ch()
        self.height = module.height()
        self.hmap = bool(module.hmap())
        self.reverse = bool(module.reverse())

        if not self.hmap:
            raise ValueError("This editor currently supports hmap() == True only.")

        self.glyphs = {}
        for code in range(self.min_ch, self.max_ch + 1):
            self.glyphs[code] = self._decode_glyph(code)

    def _u16(self, data, offset):
        return data[offset] | (data[offset + 1] << 8)

    def _index_offset_for_code(self, code):
        return 2 * (code - self.min_ch + 1)

    def _decode_glyph(self, code):
        ioff = self._index_offset_for_code(code)
        doff = self._u16(self.index_bytes, ioff)
        width = self._u16(self.font_bytes, doff)
        row_bytes = (width + 7) // 8
        bmp = self.font_bytes[doff + 2 : doff + 2 + row_bytes * self.height]

        pixels = [[0 for _ in range(width)] for _ in range(self.height)]
        for y in range(self.height):
            for x in range(width):
                byte_index = y * row_bytes + (x // 8)
                bit_index = 7 - (x % 8)
                bit = (bmp[byte_index] >> bit_index) & 1
                if self.reverse:
                    bit ^= 1
                pixels[y][x] = bit

        return {"width": width, "pixels": pixels}

    def encode_all(self):
        chunks = []
        offsets = [0, 0]

        for code in range(self.min_ch, self.max_ch + 1):
            glyph = self.glyphs[code]
            width = glyph["width"]
            row_bytes = (width + 7) // 8
            payload = bytearray()

            payload.append(width & 0xFF)
            payload.append((width >> 8) & 0xFF)

            for y in range(self.height):
                row = [0] * row_bytes
                for x in range(width):
                    bit = glyph["pixels"][y][x]
                    if self.reverse:
                        bit ^= 1
                    if bit:
                        row[x // 8] |= 1 << (7 - (x % 8))
                payload.extend(row)

            chunks.append(bytes(payload))
            offsets.append(offsets[-1] + len(payload))

        font_blob = b"".join(chunks)
        index_blob = b"".join(o.to_bytes(2, "little") for o in offsets)
        return font_blob, index_blob

    def export_source(self):
        font_blob, index_blob = self.encode_all()
        text = self.source_text
        text = replace_bytes_literal(text, "_font", font_blob)
        text = replace_bytes_literal(text, "_index", index_blob)
        return text


class BitmapEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bitmap Font Editor")
        self.model = BitmapFontModel()

        self.current_code = ord("0")
        self.scale = 20
        self.show_grid = True
        self.dirty = False

        self.canvas = None
        self.glyph_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Open a font .py file to begin")
        self.width_var = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=8)

        tk.Button(top, text="Open", command=self.open_file).pack(side="left")
        tk.Button(top, text="Save As", command=self.save_as).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Prev", command=self.prev_glyph).pack(side="left", padx=(12, 0))
        tk.Button(top, text="Next", command=self.next_glyph).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Clone", command=self.clone_glyph).pack(side="left", padx=(12, 0))
        tk.Button(top, text="Clear", command=self.clear_glyph).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Invert", command=self.invert_glyph).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Shift ←", command=lambda: self.shift(-1, 0)).pack(side="left", padx=(12, 0))
        tk.Button(top, text="Shift →", command=lambda: self.shift(1, 0)).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Shift ↑", command=lambda: self.shift(0, -1)).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Shift ↓", command=lambda: self.shift(0, 1)).pack(side="left", padx=(4, 0))

        mid = tk.Frame(self.root)
        mid.pack(fill="x", padx=8)

        tk.Label(mid, textvariable=self.glyph_var, font=("Consolas", 14, "bold")).pack(side="left")
        tk.Label(mid, textvariable=self.width_var).pack(side="left", padx=(12, 0))
        tk.Button(mid, text="Set Width", command=self.set_width).pack(side="left", padx=(12, 0))
        tk.Button(mid, text="+ Zoom", command=lambda: self.change_scale(2)).pack(side="left", padx=(12, 0))
        tk.Button(mid, text="- Zoom", command=lambda: self.change_scale(-2)).pack(side="left", padx=(4, 0))

        self.canvas = tk.Canvas(self.root, bg="white", highlightthickness=1, highlightbackground="#999")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas.bind("<Button-1>", self.on_canvas_left)
        self.canvas.bind("<B1-Motion>", self.on_canvas_left)
        self.canvas.bind("<Button-3>", self.on_canvas_right)
        self.canvas.bind("<B3-Motion>", self.on_canvas_right)

        bottom = tk.Label(self.root, textvariable=self.status_var, anchor="w")
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        self.root.bind("<Left>", lambda e: self.prev_glyph())
        self.root.bind("<Right>", lambda e: self.next_glyph())
        self.root.bind("<Control-s>", lambda e: self.save_as())
        self.root.bind("<space>", lambda e: self.toggle_current())

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open bitmap font Python file",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.model.load_py_font(path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return

        if not (self.model.min_ch <= self.current_code <= self.model.max_ch):
            self.current_code = self.model.min_ch
        self.dirty = False
        self.status_var.set(f"Loaded {os.path.basename(path)}")
        self.redraw()

    def save_as(self):
        if not self.model.path:
            messagebox.showinfo("No file", "Open a font file first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save edited font",
            defaultextension=".py",
            filetypes=[("Python files", "*.py")],
            initialfile=os.path.basename(self.model.path).replace(".py", "_edited.py"),
        )
        if not path:
            return
        try:
            text = self.model.export_source()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.dirty = False
            self.status_var.set(f"Saved {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def current_glyph(self):
        return self.model.glyphs[self.current_code]

    def redraw(self):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        width = glyph["width"]
        height = self.model.height
        cw = width * self.scale + 1
        ch = height * self.scale + 1

        self.canvas.delete("all")
        self.canvas.config(scrollregion=(0, 0, cw, ch))
        self.canvas.config(width=max(300, min(cw + 20, 1000)), height=max(200, min(ch + 20, 800)))

        for y in range(height):
            for x in range(width):
                x0 = x * self.scale
                y0 = y * self.scale
                x1 = x0 + self.scale
                y1 = y0 + self.scale
                fill = "black" if glyph["pixels"][y][x] else "white"
                outline = "#ccc" if self.show_grid else fill
                self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline)

        ch_repr = chr(self.current_code)
        nice = repr(ch_repr)[1:-1]
        self.glyph_var.set(f"Glyph {self.current_code} ('{nice}')")
        self.width_var.set(f"Width: {width}px   Height: {height}px")

    def _paint(self, event, value):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        x = event.x // self.scale
        y = event.y // self.scale
        if 0 <= y < self.model.height and 0 <= x < glyph["width"]:
            if glyph["pixels"][y][x] != value:
                glyph["pixels"][y][x] = value
                self.dirty = True
                self.redraw()

    def on_canvas_left(self, event):
        self._paint(event, 1)

    def on_canvas_right(self, event):
        self._paint(event, 0)

    def prev_glyph(self):
        if not self.model.glyphs:
            return
        self.current_code -= 1
        if self.current_code < self.model.min_ch:
            self.current_code = self.model.max_ch
        self.redraw()

    def next_glyph(self):
        if not self.model.glyphs:
            return
        self.current_code += 1
        if self.current_code > self.model.max_ch:
            self.current_code = self.model.min_ch
        self.redraw()

    def clear_glyph(self):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        for y in range(self.model.height):
            for x in range(glyph["width"]):
                glyph["pixels"][y][x] = 0
        self.dirty = True
        self.redraw()

    def invert_glyph(self):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        for y in range(self.model.height):
            for x in range(glyph["width"]):
                glyph["pixels"][y][x] ^= 1
        self.dirty = True
        self.redraw()

    def shift(self, dx, dy):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        w = glyph["width"]
        h = self.model.height
        new_pixels = [[0 for _ in range(w)] for _ in range(h)]
        for y in range(h):
            for x in range(w):
                sx = x - dx
                sy = y - dy
                if 0 <= sx < w and 0 <= sy < h:
                    new_pixels[y][x] = glyph["pixels"][sy][sx]
        glyph["pixels"] = new_pixels
        self.dirty = True
        self.redraw()

    def set_width(self):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        new_width = simpledialog.askinteger(
            "Set glyph width",
            "New width in pixels:",
            initialvalue=glyph["width"],
            minvalue=1,
            maxvalue=128,
        )
        if not new_width or new_width == glyph["width"]:
            return

        old_width = glyph["width"]
        old_pixels = glyph["pixels"]
        new_pixels = []
        for row in old_pixels:
            if new_width > old_width:
                new_pixels.append(row + [0] * (new_width - old_width))
            else:
                new_pixels.append(row[:new_width])
        glyph["width"] = new_width
        glyph["pixels"] = new_pixels
        self.dirty = True
        self.redraw()

    def clone_glyph(self):
        if not self.model.glyphs:
            return
        target = simpledialog.askstring("Clone glyph", "Copy current glyph to character:")
        if not target:
            return
        if len(target) != 1:
            messagebox.showerror("Invalid character", "Enter exactly one character.")
            return
        code = ord(target)
        if code < self.model.min_ch or code > self.model.max_ch:
            messagebox.showerror(
                "Out of range",
                f"Character must be between {chr(self.model.min_ch)!r} and {chr(self.model.max_ch)!r}.",
            )
            return
        src = self.current_glyph()
        self.model.glyphs[code] = {
            "width": src["width"],
            "pixels": [row[:] for row in src["pixels"]],
        }
        self.dirty = True
        self.status_var.set(f"Cloned glyph to {target!r}")

    def change_scale(self, delta):
        self.scale = max(4, min(60, self.scale + delta))
        self.redraw()

    def toggle_current(self):
        if not self.model.glyphs:
            return
        glyph = self.current_glyph()
        cx = glyph["width"] // 2
        cy = self.model.height // 2
        glyph["pixels"][cy][cx] ^= 1
        self.dirty = True
        self.redraw()


def format_bytes_blob(blob, line_bytes=16):
    lines = []
    for i in range(0, len(blob), line_bytes):
        chunk = blob[i:i + line_bytes]
        lines.append("b'" + "".join(f"\\x{b:02x}" for b in chunk) + "'\\")
    if lines:
        lines[-1] = lines[-1][:-1]   # remove trailing \ from final line
    return "\n".join(lines)

def replace_bytes_literal(source, var_name, new_blob):
    marker = var_name + " =\
"
    start = source.find(marker)
    if start == -1:
        raise ValueError(f"Could not find {var_name} assignment in source file.")

    data_start = start + len(marker)
    end = data_start
    while end < len(source):
        line_end = source.find("""
""", end)
        if line_end == -1:
            line_end = len(source)
        line = source[end:line_end]
        stripped = line.strip()
        if stripped.startswith("\\") or stripped.startswith("b'") or stripped.startswith('b"') or stripped == "":
            end = line_end + 1
            continue
        break

    replacement = marker + format_bytes_blob(new_blob) + """
"""
    return source[:start] + replacement + source[end:]


def _old_replace_bytes_literal_placeholder(source, var_name, new_blob):
    marker = var_name + " =\\\n"
    start = source.find(marker)
    if start == -1:
        raise ValueError(f"Could not find {var_name} assignment in source file.")

    data_start = start + len(marker)
    end = data_start
    while end < len(source):
        line_end = source.find("\n", end)
        if line_end == -1:
            line_end = len(source)
        line = source[end:line_end]
        stripped = line.strip()
        if stripped.startswith("b'") or stripped.startswith('b"') or stripped == "":
            end = line_end + 1
            continue
        break

    replacement = marker + format_bytes_blob(new_blob) + "\n"
    return source[:start] + replacement + source[end:]


def main():
    root = tk.Tk()
    app = BitmapEditorApp(root)

    if len(sys.argv) > 1:
        try:
            app.model.load_py_font(sys.argv[1])
            app.current_code = max(app.model.min_ch, min(app.model.max_ch, ord("0")))
            app.redraw()
            app.status_var.set(f"Loaded {os.path.basename(sys.argv[1])}")
        except Exception as e:
            messagebox.showerror("Open failed", str(e))

    root.mainloop()


if __name__ == "__main__":
    main()
