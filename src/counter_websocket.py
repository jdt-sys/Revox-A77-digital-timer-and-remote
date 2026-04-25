import network
import uasyncio as asyncio
import ubinascii
import uhashlib
import json


class CounterWebSocketServer:
    def __init__(self, ssid, password, title="Counter"):
        self.ssid = ssid
        self.password = password
        self.title = title

        self.wlan = network.WLAN(network.STA_IF)
        self.clients = set()

        self.current_text = "00000"
        self.server = None

        # single callback for all transport actions
        self.transport_callback = None

    # ---------------------------
    # Wi-Fi
    # ---------------------------
    def connect_wifi(self):
        self.wlan.active(True)

        if not self.wlan.isconnected():
            print("Connecting to Wi-Fi...")
            self.wlan.connect(self.ssid, self.password)

            while not self.wlan.isconnected():
                pass

        print("Wi-Fi connected:", self.wlan.ifconfig()[0])

    # ---------------------------
    # Server start
    # ---------------------------
    async def start(self, host="0.0.0.0", port=80):
        self.connect_wifi()
        self.server = await asyncio.start_server(self._handle_client, host, port)
        print("Web server ready at http://{}".format(self.wlan.ifconfig()[0]))

    # ---------------------------
    # Public API
    # ---------------------------
    def set_text(self, text):
        text = str(text)
        if text != self.current_text:
            self.current_text = text
            asyncio.create_task(self.broadcast_count())

    def set_transport_callback(self, callback):
        self.transport_callback = callback

    # ---------------------------
    # Broadcast
    # ---------------------------
    async def broadcast_count(self):
        if not self.clients:
            return

        dead = []
        msg = json.dumps({"count": self.current_text})

        for client in self.clients:
            try:
                await self._ws_send_text(client, msg)
            except Exception as e:
                print("WS send error:", e)
                dead.append(client)

        for client in dead:
            try:
                self.clients.remove(client)
                client.close()
            except:
                pass

    # ---------------------------
    # HTTP handler
    # ---------------------------
    async def _handle_client(self, reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                await writer.aclose()
                return

            request_line = request_line.decode().strip()
            parts = request_line.split(" ")
            if len(parts) < 2:
                await writer.aclose()
                return

            path = parts[1]

            # headers
            headers = {}
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                line = line.decode().strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            # WebSocket upgrade
            if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await self._handle_websocket(reader, writer, headers)
                return

            # Serve index
            if path == "/":
                await self._serve_file(writer, "index.html", "text/html")
                return

            # Serve static files
            try:
                await self._serve_file(writer, path.lstrip("/"))
            except:
                await self._send_404(writer)

        except Exception as e:
            print("Client error:", e)
            try:
                await writer.aclose()
            except:
                pass

    # ---------------------------
    # Static file server
    # ---------------------------
    async def _serve_file(self, writer, filename, content_type=None):
        try:
            with open(filename, "rb") as f:
                data = f.read()

            if content_type is None:
                if filename.endswith(".html"):
                    content_type = "text/html"
                elif filename.endswith(".js"):
                    content_type = "application/javascript"
                elif filename.endswith(".css"):
                    content_type = "text/css"
                else:
                    content_type = "application/octet-stream"

            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: {}\r\n"
                "Content-Length: {}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).format(content_type, len(data))

            writer.write(header.encode() + data)
            await writer.drain()
            await writer.aclose()

        except Exception as e:
            print("File serve error:", e)
            await self._send_404(writer)

    async def _send_404(self, writer):
        body = b"404 Not Found"
        header = (
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(len(body))

        writer.write(header.encode() + body)
        await writer.drain()
        await writer.aclose()

    # ---------------------------
    # WebSocket handling
    # ---------------------------
    def _make_accept(self, websocket_key):
        GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        data = (websocket_key + GUID).encode()
        sha1 = uhashlib.sha1(data).digest()
        return ubinascii.b2a_base64(sha1).strip().decode()

    async def _handle_websocket(self, reader, writer, headers):
        key = headers.get("sec-websocket-key")
        if not key:
            await writer.aclose()
            return

        accept = self._make_accept(key)

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: {}\r\n"
            "\r\n"
        ).format(accept)

        writer.write(response.encode())
        await writer.drain()

        self.clients.add(writer)
        print("WebSocket client connected")

        try:
            # send initial state
            await self._ws_send_text(writer, json.dumps({"count": self.current_text}))

            while True:
                msg = await self._ws_read_text(reader)
                if msg is None:
                    break

                # 🔥 single callback for everything
                if self.transport_callback:
                    self.transport_callback(msg)

        except Exception as e:
            print("WebSocket client error:", e)

        finally:
            print("WebSocket client disconnected")
            try:
                self.clients.remove(writer)
            except:
                pass
            try:
                writer.close()
            except:
                pass

    # ---------------------------
    # WebSocket framing
    # ---------------------------
    async def _ws_send_text(self, writer, text):
        payload = text.encode()
        length = len(payload)

        if length < 126:
            header = bytes([0x81, length])
        elif length < 65536:
            header = bytes([0x81, 126, (length >> 8) & 0xFF, length & 0xFF])
        else:
            raise ValueError("Payload too large")

        writer.write(header + payload)
        await writer.drain()

    async def _ws_read_exact(self, reader, n):
        buf = b""
        while len(buf) < n:
            chunk = await reader.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    async def _ws_read_text(self, reader):
        hdr = await self._ws_read_exact(reader, 2)
        if hdr is None:
            return None

        b1 = hdr[0]
        b2 = hdr[1]

        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        length = b2 & 0x7F

        if opcode == 0x8:
            return None

        if length == 126:
            ext = await self._ws_read_exact(reader, 2)
            if ext is None:
                return None
            length = (ext[0] << 8) | ext[1]

        elif length == 127:
            return None

        if not masked:
            return None

        mask = await self._ws_read_exact(reader, 4)
        if mask is None:
            return None

        payload = await self._ws_read_exact(reader, length)
        if payload is None:
            return None

        unmasked = bytearray(length)
        for i in range(length):
            unmasked[i] = payload[i] ^ mask[i & 3]

        return unmasked.decode()
