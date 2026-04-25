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

        # ---- measured motion ----
        self._last_motion_time = time.ticks_ms()
        self.speed_cps = 0.0          # counts per second
        self.accel_cps2 = 0.0         # counts per second^2
        self._last_speed_cps = 0.0

        # smoothing
        self.speed_alpha = 0.35
        self.accel_alpha = 0.25

        # ---- goto state ----
        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self.goto_stop_time = 0
        self.goto_stop_count = None

        # learned stop distance at normal running speed
        self.brake_counts = {
            "ff": 20,
            "rwd": 20,
        }

        # crude physics knobs
        self.min_run_counts = 8
        self.min_stop_counts = 2
        self.max_stop_counts = 800

        # Higher = assumes tape stops sooner.
        # Lower = assumes tape coasts farther.
        self.brake_friction = {
            "ff": 1.0,
            "rwd": 1.0,
        }

        # Reel load bias.
        # Positive means direction tends to coast farther near that end.
        self.reel_bias = {
            "ff": 0.12,
            "rwd": 0.12,
        }

        # Helps avoid scary late stops
        self.safety_margin_counts = 3

        self.load()

    # ---------- counter core ----------

    def _to_signed(self, v):
        v &= self.mask
        return v if v < self.sign_bit else v - (1 << self.bits)

    def update(self, raw_x):
        raw_x &= self.mask
        now = time.ticks_ms()

        if self.last_x is None:
            self.last_x = raw_x
            self._last_motion_time = now
            return self.count

        delta = self._to_signed(raw_x - self.last_x)
        self.last_x = raw_x

        count_delta = 0

        if delta != 0:
            self._last_move_time = now
            self._step_accum += delta * self.direction

            whole = int(self._step_accum / self.steps_per_count)

            if whole != 0:
                old_count = self.count
                self.count = (self.count + whole) % self.modulus
                self._step_accum -= whole * self.steps_per_count
                self._dirty = True

                count_delta = self._signed_counter_delta(old_count, self.count)

        self._update_motion_model(count_delta, now)
        self.tick()
        return self.count

    def _signed_counter_delta(self, old, new):
        return ((new - old + self.modulus // 2) % self.modulus) - self.modulus // 2

    def _update_motion_model(self, count_delta, now):
        dt_ms = time.ticks_diff(now, self._last_motion_time)

        if dt_ms <= 0:
            return

        dt = dt_ms / 1000.0
        self._last_motion_time = now

        instant_speed = abs(count_delta) / dt if count_delta else 0.0

        self.speed_cps = (
            self.speed_cps * (1.0 - self.speed_alpha)
            + instant_speed * self.speed_alpha
        )

        instant_accel = (self.speed_cps - self._last_speed_cps) / dt
        self.accel_cps2 = (
            self.accel_cps2 * (1.0 - self.accel_alpha)
            + instant_accel * self.accel_alpha
        )

        self._last_speed_cps = self.speed_cps

        # decay speed if no movement
        if count_delta == 0 and time.ticks_diff(now, self._last_move_time) > 250:
            self.speed_cps *= 0.75
            if self.speed_cps < 0.05:
                self.speed_cps = 0.0
                self.accel_cps2 = 0.0

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
        self.speed_cps = 0.0
        self.accel_cps2 = 0.0
        self._last_speed_cps = 0.0
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
        self.goto_stop_count = None

        print(
            "Goto start:",
            "from=", self.goto_start,
            "target=", self.goto_target,
            "dir=", self.goto_dir,
            "forward=", forward,
            "backward=", backward,
            "learned brake=", self.brake_counts.get(self.goto_dir),
        )

        self._transport(self.goto_dir)

    def cancel_goto(self):
        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self.goto_stop_count = None
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

        if travelled < self.min_run_counts:
            return

        predicted_stop = self._predict_stop_distance(self.goto_dir)

        if distance <= predicted_stop:
            self.goto_stopping = True
            self.goto_stop_time = now
            self.goto_stop_count = self.count

            print(
                "Stopping:",
                "count=", self.count,
                "target=", self.goto_target,
                "distance=", distance,
                "predicted_stop=", predicted_stop,
                "speed=", round(self.speed_cps, 2),
                "accel=", round(self.accel_cps2, 2),
            )

            self._transport("stop")

    # ---------- physics-ish model ----------

    def _predict_stop_distance(self, direction):
        learned = self.brake_counts.get(direction, 20)

        speed = max(0.0, self.speed_cps)
        accel = self.accel_cps2

        # speed multiplier:
        # faster tape = more coast
        # 20 cps is an arbitrary "normal-ish" baseline
        speed_factor = max(0.35, min(4.0, speed / 20.0))

        # if still accelerating, assume it will need more room
        accel_factor = 1.0
        if accel > 0:
            accel_factor += min(0.8, accel / 80.0)

        # reel position factor:
        # crude estimate of pack imbalance using counter position.
        reel_factor = self._reel_load_factor(direction)

        friction = self.brake_friction.get(direction, 1.0)
        if friction <= 0:
            friction = 1.0

        predicted = learned * speed_factor * accel_factor * reel_factor / friction
        predicted += self.safety_margin_counts

        predicted = int(predicted)

        return max(self.min_stop_counts, min(predicted, self.max_stop_counts))

    def _reel_load_factor(self, direction):
        pos = self.count / float(self.modulus - 1)

        # In FF, higher counter usually means takeup reel is fuller.
        # In RWD, lower counter usually means supply reel is fuller.
        if direction == "ff":
            load = pos
        else:
            load = 1.0 - pos

        bias = self.reel_bias.get(direction, 0.0)

        # range roughly 1.0 to 1.0 + bias
        return 1.0 + (load * bias)

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

        # Simple adaptive learning.
        # Overshot? stop earlier next time.
        # Undershot? let it run longer next time.
        if overshot:
            brake += max(1, amount // 2)
            self.brake_friction[self.goto_dir] *= 0.97
        else:
            brake -= max(1, amount // 4)
            self.brake_friction[self.goto_dir] *= 1.02

        brake = max(2, min(brake, 500))
        self.brake_counts[self.goto_dir] = brake

        self.brake_friction[self.goto_dir] = max(
            0.4,
            min(self.brake_friction[self.goto_dir], 2.5)
        )

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
            "friction=", round(self.brake_friction[self.goto_dir], 3),
        )

    def _finish_goto(self):
        self.goto_active = False
        self.goto_target = None
        self.goto_start = None
        self.goto_dir = None
        self.goto_stopping = False
        self.goto_stop_count = None
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

            saved_friction = data.get("brake_friction", {})
            self.brake_friction["ff"] = float(saved_friction.get("ff", self.brake_friction["ff"]))
            self.brake_friction["rwd"] = float(saved_friction.get("rwd", self.brake_friction["rwd"]))

            self.brake_friction["ff"] = max(0.4, min(self.brake_friction["ff"], 2.5))
            self.brake_friction["rwd"] = max(0.4, min(self.brake_friction["rwd"], 2.5))

            print("Loaded counter:", self.count)
            print("Loaded brake counts:", self.brake_counts)
            print("Loaded brake friction:", self.brake_friction)

        except Exception as e:
            print("Load failed, using defaults:", e)
            self.count = 0

    def save(self):
        try:
            data = {
                "count": self.count,
                "brake_counts": self.brake_counts,
                "brake_friction": self.brake_friction,
            }

            with open(self.filename, "w") as f:
                f.write(json.dumps(data))

        except Exception as e:
            print("Save failed:", e)
