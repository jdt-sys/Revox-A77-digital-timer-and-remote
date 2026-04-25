import time
import json


class RollingCounter:
    def __init__(
        self,
        modulus=100000,
        filename="counter.json",
        save_interval_ms=2000,
        bits=28,
        steps_per_count=100,
        direction=-1,
        on_transport=None,
    ):
        self.modulus = modulus
        self.filename = filename
        self.save_interval_ms = save_interval_ms
        self.bits = bits
        self.steps_per_count = steps_per_count
        self.direction = direction
        self.on_transport = on_transport

        self.mask = (1 << bits) - 1
        self.sign_bit = 1 << (bits - 1)

        self.last_x = None
        self.count = 0
        self._step_accum = 0

        self._last_save_time = time.ticks_ms()
        self._dirty = False
        self._last_move_time = 0

        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self.goto_stop_time = 0

        self.brake_counts = {
            "ff": 20,
            "rwd": 20,
        }

        self.min_run_counts = 8

        self.load()

    # ---------- counter core ----------

    def _to_signed(self, v):
        v &= self.mask
        return v if v < self.sign_bit else v - (1 << self.bits)

    def update(self, raw_x):
        raw_x &= self.mask

        if self.last_x is None:
            self.last_x = raw_x
            return self.count

        delta = self._to_signed(raw_x - self.last_x)
        self.last_x = raw_x

        if delta != 0:
            self._last_move_time = time.ticks_ms()
            self._step_accum += delta * self.direction

            whole = int(self._step_accum / self.steps_per_count)

            if whole != 0:
                self.count = (self.count + whole) % self.modulus
                self._step_accum -= whole * self.steps_per_count
                self._dirty = True

        self.tick()
        return self.count

    def tick(self):
        now = time.ticks_ms()

        self._goto_tick(now)

        if self._dirty and time.ticks_diff(now, self._last_save_time) >= self.save_interval_ms:
            self.save()
            self._last_save_time = now
            self._dirty = False

    def value(self):
        return self.count

    def formatted(self):
        return f"{self.count:05d}"

    def reset(self, value=0):
        self.count = value % self.modulus
        self.last_x = None
        self._step_accum = 0
        self._dirty = True
        self.tick()

    def counterMovedInLastSecond(self):
        now = time.ticks_ms()
        return time.ticks_diff(now, self._last_move_time) < 1000

    # ---------- goto / locate ----------

    def goto(self, target):
        target = int(target) % self.modulus

        if target == self.count:
            return

        forward = (target - self.count + self.modulus) % self.modulus
        backward = (self.count - target + self.modulus) % self.modulus

        self.goto_dir = "ff" if forward <= backward else "rwd"
        self.goto_target = target
        self.goto_start = self.count
        self.goto_active = True
        self.goto_stopping = False
        self.goto_stop_time = 0

        print(
            "Goto start:",
            "from=", self.goto_start,
            "target=", self.goto_target,
            "dir=", self.goto_dir,
            "forward=", forward,
            "backward=", backward,
            "brake=", self.brake_counts.get(self.goto_dir),
        )

        self._transport(self.goto_dir)

    def cancel_goto(self):
        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self._transport("stop")

    def _goto_tick(self, now):
        if not self.goto_active:
            return

        if self.goto_stopping:
            if (
                time.ticks_diff(now, self.goto_stop_time) > 1200
                and not self.counterMovedInLastSecond()
            ):
                self._learn_brake_distance()
                self._finish_goto()
            return

        distance = self._distance_in_direction(
            self.count,
            self.goto_target,
            self.goto_dir
        )

        travelled = self._distance_travelled()
        brake = self.brake_counts.get(self.goto_dir, 20)

        if travelled < self.min_run_counts:
            return

        if distance <= brake:
            self.goto_stopping = True
            self.goto_stop_time = now
            self._transport("stop")

    # ---------- distance helpers ----------

    def _distance_in_direction(self, current, target, direction):
        current %= self.modulus
        target %= self.modulus

        if direction == "ff":
            return (target - current + self.modulus) % self.modulus
        else:
            return (current - target + self.modulus) % self.modulus

    def _distance_travelled(self):
        if self.goto_start is None or self.goto_dir is None:
            return 0

        return self._distance_in_direction(
            self.goto_start,
            self.count,
            self.goto_dir
        )

    def _signed_error_to_target(self):
        return (
            (self.goto_target - self.count + self.modulus // 2)
            % self.modulus
        ) - self.modulus // 2

    def _has_overshot(self):
        if self.goto_target is None or self.goto_dir is None:
            return False

        error = self._signed_error_to_target()

        if self.goto_dir == "ff" and error < 0:
            return True

        if self.goto_dir == "rwd" and error > 0:
            return True

        return False

    # ---------- learning ----------

    def _learn_brake_distance(self):
        if self.goto_target is None or self.goto_dir is None:
            return

        error = self._signed_error_to_target()
        amount = abs(error)
        overshot = self._has_overshot()

        brake = self.brake_counts[self.goto_dir]

        if overshot:
            brake += max(1, amount // 2)
        else:
            brake -= max(1, amount // 4)

        brake = max(2, min(brake, 500))
        self.brake_counts[self.goto_dir] = brake

        self._dirty = True
        self.save()
        self._dirty = False

        print(
            "Goto done:",
            "target=", self.goto_target,
            "actual=", self.count,
            "error=", error,
            "overshot=", overshot,
            "dir=", self.goto_dir,
            "new brake=", brake,
        )

    def _finish_goto(self):
        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self._dirty = True

    # ---------- transport ----------

    def _transport(self, cmd):
        if self.on_transport:
            self.on_transport(cmd)
        else:
            print("transport:", cmd)

    # ---------- persistence ----------

    def load(self):
        try:
            with open(self.filename, "r") as f:
                data = json.loads(f.read())

            self.count = int(data.get("count", 0)) % self.modulus

            saved_brake = data.get("brake_counts", {})
            self.brake_counts["ff"] = int(saved_brake.get("ff", self.brake_counts["ff"]))
            self.brake_counts["rwd"] = int(saved_brake.get("rwd", self.brake_counts["rwd"]))

            self.brake_counts["ff"] = max(2, min(self.brake_counts["ff"], 500))
            self.brake_counts["rwd"] = max(2, min(self.brake_counts["rwd"], 500))

            print("Loaded counter:", self.count)
            print("Loaded brake counts:", self.brake_counts)

        except Exception as e:
            print("Load failed, using defaults:", e)
            self.count = 0

    def save(self):
        try:
            data = {
                "count": self.count,
                "brake_counts": self.brake_counts,
            }

            with open(self.filename, "w") as f:
                f.write(json.dumps(data))

        except Exception as e:
            print("Save failed:", e)
