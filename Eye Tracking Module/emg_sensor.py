#!/usr/bin/env python3
"""
emg_sensor.py
=============
EMG muscle sensor (Arduino) as the click input: one squeeze = one Spacebar press.

Arduino sketch (user's test sketch): analogRead(A5) -> Serial.println(value), delay(10)
  -> one number per line, ~100 values/s, 115200 baud, 0..1023.

Signal path (one background thread, see EmgSensor.feed):
  raw value -> effort = average of the last `emg_smooth_ms` (a squeeze is a spiky burst that dips back to rest
  between the spikes; the average turns it into ONE hump) -> click detector -> Space press.

Click detector (works on the effort line; with Baseline tracking all lines move with the resting level):
  * Threshold         effort must rise above it ...
  * Minimum hold      ... and stay above it this long (twitches and single spikes are ignored)
  * Release level     after a click nothing fires until effort falls below this lower level (squeeze = 1 click)
  * Lockout           minimum time between two clicks
  * Long squeeze      (switch) held for `emg_long_ms` = gaze mouse on/off (like F5) INSTEAD of a click. While the
                      switch is on, a normal click fires when the squeeze ends (so a long squeeze never clicks).
  * Baseline tracking the resting level is followed slowly (electrodes drying, skin warming up)
  * Electrode guard   flat signal / signal at maximum / effort high for > 5 s -> no clicks, "Check electrodes"
  * Auto-reconnect    port "auto" finds the Arduino; unplugged or busy port is tried again every 2 s
  * Auto-calibrate    relax 3 s, then squeeze 3 times in 6 s -> threshold 40 %, release 15 % of (squeeze - rest)

A click is a real Space key press (keybd_event), so it does exactly what the physical Spacebar does: the AAC app
selects the hovered key when it is in front, the tracker's Spacebar click clicks anywhere else (both need Spacebar
Select on). With "EMG click" off (test mode) clicks are only shown in the EMG View.
The port is open only while EMG click is on or the EMG View is open (otherwise the Arduino IDE can use it).
"""

import time
import ctypes
import threading
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:                                  # the tracker keeps working without the EMG sensor
    serial = None
    list_ports = None

VK_SPACE, SCAN_SPACE, KEYEVENTF_KEYUP = 0x20, 0x39, 0x0002
KEY_HOLD_S = 0.08          # the gaze engine checks the Spacebar once per camera frame (33 ms): hold it longer

DEFAULT_RATE = 100.0       # values/s until measured
GRAPH_KEEP_S = 6.0
RETRY_S = 2.0              # auto-reconnect interval
NO_DATA_S = 3.0            # "no data" shown after this (an Uno restarts ~2 s after the port opens)
NO_DATA_REOPEN_S = 8.0     # then try the port again (auto: the next candidate)
GUARD_EVERY_S = 0.25
GUARD_FLAT_S, GUARD_FLAT_RANGE = 2.0, 3          # raw range <= 3 for 2 s = no signal
GUARD_MAX_VALUE, GUARD_MAX_SHARE = 1015, 0.2     # >= 20 % of the last second at the top of the range
GUARD_HIGH_S = 5.0                                # effort above the release level for 5 s without a break
BASELINE_TAU_S = 30.0
BASELINE_MAX_DRIFT = 150.0
CAL_RELAX_S, CAL_SQUEEZE_S = 3.0, 6.0
CAL_THR, CAL_REL = 0.40, 0.15
KNOWN_VID = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}   # Arduino, Arduino.org, CH340, FTDI, CP210x
KNOWN_NAMES = ("arduino", "ch340", "ch341", "usb-serial", "usb serial", "cp210", "ftdi")

# saved in binocular_settings.json (binocular_controller.DEFAULTS has the same keys)
DEFAULTS = {
    "emg_enabled": False, "emg_port": "auto", "emg_baud": 115200,
    "emg_threshold": 150, "emg_release": 70, "emg_rest": 40,
    "emg_smooth_ms": 150, "emg_hold_ms": 50, "emg_lockout_ms": 300,
    "emg_baseline": True, "emg_long_enabled": False, "emg_long_ms": 1000,
}

IDLE, ACTIVE, HOLDING, WAIT = "idle", "active", "holding", "wait"


def press_space():
    u = ctypes.windll.user32
    u.keybd_event(VK_SPACE, SCAN_SPACE, 0, 0)
    t = threading.Timer(KEY_HOLD_S, lambda: u.keybd_event(VK_SPACE, SCAN_SPACE, KEYEVENTF_KEYUP, 0))
    t.daemon = True
    t.start()


def serial_ports() -> List[str]:
    if list_ports is None:
        return []
    return sorted((p.device for p in list_ports.comports()), key=lambda d: (len(d), d))


def _auto_candidates() -> List[str]:
    """Arduino-like ports first, then the rest."""
    if list_ports is None:
        return []
    good, other = [], []
    for p in list_ports.comports():
        text = f"{p.description} {p.manufacturer or ''} {p.hwid}".lower()
        (good if (p.vid in KNOWN_VID or any(k in text for k in KNOWN_NAMES)) else other).append(p.device)
    key = lambda d: (len(d), d)
    return sorted(good, key=key) + sorted(other, key=key)


def _pct(vals: List[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


class EmgSensor:
    def __init__(self, cfg: Dict[str, Any], save: Callable[..., None], on_click: Callable[[], None] = press_space,
                 on_long: Optional[Callable[[], Any]] = None):
        self.cfg, self.save, self.on_click, self.on_long = cfg, save, on_click, on_long
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ser = None
        self._reconnect = False
        self._next_try = 0.0
        self._cand_i = 0
        self.viewers = 0
        self.conn = "off"            # off | no_pyserial | searching | busy | connecting | connected | no_data
        self.port = ""
        self._opened_t = 0.0
        self._last_data = 0.0
        self.reset_signal()

    # ------------------------------------------------------------------ settings
    def _get(self, k):
        return self.cfg.get(k, DEFAULTS[k])

    def wanted(self) -> bool:
        return bool(self._get("emg_enabled")) or self.viewers > 0

    def reconnect(self):
        """Port setting changed: close and open again."""
        self._reconnect = True
        self._next_try = 0.0

    def add_viewer(self):
        self.viewers += 1

    def remove_viewer(self):
        self.viewers = max(0, self.viewers - 1)

    # ------------------------------------------------------------------ signal state
    def reset_signal(self):
        with self._lock:
            self._win: deque = deque()
            self._sum = 0.0
            self._times: deque = deque(maxlen=101)
            self.rate = DEFAULT_RATE
            self.raw = None
            self.env = 0.0
            self.hist: deque = deque(maxlen=int(GRAPH_KEEP_S * 250))    # (t, raw, effort)
            self.marks: deque = deque(maxlen=64)                       # (t, "click" | "long" | "ignored")
            self.state = WAIT                       # starts waiting for a rest level (no click at connect)
            self._t_start = 0.0
            self._last_fire = -1e9
            self._last_t = None
            self.baseline = float(self._get("emg_rest"))
            self.guard = ""                         # reason while the electrode guard blocks clicks
            self._guard_t = 0.0
            self._high_since = None
            self.clicks = 0
            self.longs = 0
            self.last_click_t = -1e9
            self._cal = None
            self.cal_msg, self.cal_ok, self.cal_seq = "", False, 0

    def lines(self) -> Tuple[float, float, float]:
        """(threshold, release, drift) as used right now (Baseline tracking moves both lines)."""
        drift = 0.0
        if bool(self._get("emg_baseline")):
            drift = max(-BASELINE_MAX_DRIFT, min(BASELINE_MAX_DRIFT, self.baseline - float(self._get("emg_rest"))))
        return float(self._get("emg_threshold")) + drift, float(self._get("emg_release")) + drift, drift

    # ------------------------------------------------------------------ detector (one value)
    def feed(self, raw: float, t: float):
        fire = None
        with self._lock:
            # sample rate (for the smoothing window) and the effort line
            self._times.append(t)
            if len(self._times) >= 20 and t > self._times[0]:
                self.rate = (len(self._times) - 1) / (t - self._times[0])
            n = max(1, int(round(float(self._get("emg_smooth_ms")) / 1000.0 * self.rate)))
            self._win.append(raw)
            self._sum += raw
            while len(self._win) > n:
                self._sum -= self._win.popleft()
            env = self._sum / len(self._win)
            self.raw, self.env = raw, env
            self.hist.append((t, raw, env))
            dt = 0.0 if self._last_t is None else min(0.1, max(0.0, t - self._last_t))
            self._last_t = t
            thr, rel, _ = self.lines()

            # electrode guard (checked 4x per second)
            self._high_since = (self._high_since or t) if env >= rel else None
            if t - self._guard_t >= GUARD_EVERY_S:
                self._guard_t = t
                self.guard = self._check_guard(t)

            if self._cal is not None:
                self._calibrate_step(t, env)
                self.state = WAIT
            elif self.guard:
                self.state = WAIT
            else:
                fire = self._step(t, env, thr, rel)

            # baseline: follow the resting level whenever no squeeze is going on (below the threshold), so a rest
            # level that creeps up past the release line (drying electrodes) is followed and clicks re-arm
            if bool(self._get("emg_baseline")) and self.state in (IDLE, WAIT) and env < thr and self._cal is None:
                self.baseline += (env - self.baseline) * (dt / BASELINE_TAU_S)
            if fire:
                self.marks.append((t, fire))
                if fire == "click":
                    self.clicks += 1
                    self.last_click_t = t
                elif fire == "long":
                    self.longs += 1
                    self.last_click_t = t
        if fire in ("click", "long") and bool(self._get("emg_enabled")):       # outside the lock
            try:
                if fire == "click":
                    self.on_click()
                elif self.on_long is not None:
                    self.on_long()
            except Exception as e:
                print(f"[EMG] action failed: {e}", flush=True)

    def _step(self, t, env, thr, rel) -> Optional[str]:
        hold = float(self._get("emg_hold_ms")) / 1000.0
        lockout = float(self._get("emg_lockout_ms")) / 1000.0
        long_on = bool(self._get("emg_long_enabled"))
        if self.state == WAIT:
            if env < rel:
                self.state = IDLE
            return None
        if self.state == IDLE:
            if env >= thr:
                if t - self._last_fire < lockout:
                    self.state = WAIT                       # squeeze during the lockout: ignored as a whole
                    return "ignored"
                self.state, self._t_start = ACTIVE, t
            return None
        if self.state == ACTIVE:
            if env < thr:
                self.state = IDLE                           # shorter than the minimum hold: a twitch
                return "ignored" if t - self._t_start > 0 else None
            if t - self._t_start >= hold:
                if not self._looks_like_muscle(t):
                    self.state = WAIT                       # flat / maxed-out step: electrode, not muscle
                    return "ignored"
                if long_on:
                    self.state = HOLDING                    # decide at the end: short = click, long = mouse
                    return None
                self.state, self._last_fire = WAIT, t
                return "click"
            return None
        if self.state == HOLDING:
            if env < thr:
                self.state, self._last_fire = WAIT, t
                return "click"
            if t - self._t_start >= float(self._get("emg_long_ms")) / 1000.0:
                self.state, self._last_fire = WAIT, t
                return "long"
        return None

    def _looks_like_muscle(self, t) -> bool:
        """The raw values since the effort crossed the threshold must vary (EMG is spiky) and not sit at the top."""
        lo = hi = None
        n = top = 0
        for ts, r, _ in reversed(self.hist):
            if ts < self._t_start and n >= 5:        # the squeeze so far (at least the last 5 values)
                break
            lo = r if lo is None else min(lo, r)
            hi = r if hi is None else max(hi, r)
            n += 1
            top += r >= GUARD_MAX_VALUE
        return n == 0 or (hi - lo > GUARD_FLAT_RANGE and top / n < 0.5)

    def _check_guard(self, t) -> str:
        if not self.hist or t - self.hist[0][0] < GUARD_FLAT_S:
            return ""
        lo = hi = None
        top = n1 = 0
        for ts, r, _ in reversed(self.hist):
            age = t - ts
            if age > GUARD_FLAT_S:
                break
            lo = r if lo is None else min(lo, r)
            hi = r if hi is None else max(hi, r)
            if age <= 1.0:
                n1 += 1
                top += r >= GUARD_MAX_VALUE
        if n1 and top / n1 >= GUARD_MAX_SHARE:
            return "signal at maximum"
        if hi is not None and hi - lo <= GUARD_FLAT_RANGE:
            return "no signal (flat)"
        if self._high_since is not None and t - self._high_since >= GUARD_HIGH_S:
            return "stays high"
        return ""

    # ------------------------------------------------------------------ auto-calibrate
    def start_calibration(self):
        with self._lock:
            self._cal = {"t0": None, "relax": [], "squeeze": []}
            self.cal_msg, self.cal_ok = "", False

    def cancel_calibration(self):
        with self._lock:
            self._cal = None

    def calibration_phase(self) -> Tuple[str, float]:
        """("relax" | "squeeze" | "", seconds left in that phase)."""
        c = self._cal
        if c is None:
            return "", 0.0
        if c["t0"] is None or self._last_t is None:
            return "relax", CAL_RELAX_S
        el = self._last_t - c["t0"]
        if el < CAL_RELAX_S:
            return "relax", CAL_RELAX_S - el
        return "squeeze", max(0.0, CAL_RELAX_S + CAL_SQUEEZE_S - el)

    def _calibrate_step(self, t, env):
        c = self._cal
        if c["t0"] is None:
            c["t0"] = t
        el = t - c["t0"]
        if el < CAL_RELAX_S:
            c["relax"].append(env)
            return
        if el < CAL_RELAX_S + CAL_SQUEEZE_S:
            c["squeeze"].append(env)
            return
        self._cal = None
        self.cal_seq += 1
        if len(c["relax"]) < 10 or len(c["squeeze"]) < 10:
            self.cal_msg, self.cal_ok = "Not enough data, try again", False
            return
        rest, rest_hi = _pct(c["relax"], 0.5), _pct(c["relax"], 0.98)
        peak = _pct(c["squeeze"], 0.98)
        span = peak - rest
        if span < max(30.0, 3.0 * (rest_hi - rest)):
            self.cal_msg, self.cal_ok = "No clear squeeze, try again", False
            return
        thr = max(rest + CAL_THR * span, rest_hi + 0.1 * span)
        rel = min(rest + CAL_REL * span, thr - 10)
        thr, rel, rest = int(round(thr)), int(round(max(rest + 3, rel))), int(round(rest))
        self.save(emg_threshold=thr, emg_release=rel, emg_rest=rest)
        self.baseline = float(rest)
        self.cal_msg, self.cal_ok = f"Threshold {thr} · Release {rel} · Rest {rest}", True
        print(f"[EMG] auto-calibrate: rest {rest} (high {rest_hi:.0f}), squeeze {peak:.0f} -> threshold {thr}, "
              f"release {rel}", flush=True)

    # ------------------------------------------------------------------ read-outs (any thread)
    def graph(self, seconds: float):
        with self._lock:
            now = self._last_t or 0.0
            pts = [p for p in self.hist if now - p[0] <= seconds]
            marks = [m for m in self.marks if now - m[0] <= seconds]
        return pts, marks, now

    def text(self) -> str:
        if self.conn == "no_pyserial":
            return "pyserial missing"
        if self.conn == "off":
            return "Sensor off"
        if self.conn == "searching":
            return "Looking for the Arduino…"
        if self.conn == "busy":
            return f"{self.port} busy · close the Arduino Serial Monitor"
        if self.conn == "connecting":
            return f"Connecting {self.port}…"
        if self.conn == "no_data":
            return f"{self.port} · no data"
        if self.guard:
            return f"Check electrodes · {self.guard}"
        return f"{self.port} · {self.rate:.0f} Hz"

    def status(self) -> Dict[str, Any]:
        thr, rel, _ = self.lines()
        return {
            "available": serial is not None,
            "enabled": bool(self._get("emg_enabled")),
            "state": self.conn, "port": self.port, "text": self.text(),
            "ok": self.conn == "connected" and not self.guard, "guard": self.guard,
            "value": self.raw, "effort": round(self.env, 1), "rate": round(self.rate, 1),
            "threshold": round(thr), "release": round(rel), "clicks": self.clicks,
        }

    # ------------------------------------------------------------------ serial thread
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="EmgSensor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._close()

    def _close(self):
        s, self._ser = self._ser, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def _open(self):
        want = str(self._get("emg_port") or "auto")
        if want.lower() == "auto":
            cands = _auto_candidates()
            if not cands:
                self.conn, self.port = "searching", ""
                self._next_try = time.perf_counter() + RETRY_S
                return
            port = cands[self._cand_i % len(cands)]
        else:
            port = want
        self.port = port
        try:
            self._ser = serial.Serial(port, int(self._get("emg_baud")), timeout=0.2)
        except serial.SerialException as e:
            busy = "denied" in str(e).lower() or "access" in str(e).lower() or "busy" in str(e).lower()
            self.conn = "busy" if busy else "searching"
            self._cand_i += 1
            self._next_try = time.perf_counter() + RETRY_S
            return
        self.conn = "connecting"
        self._opened_t = self._last_data = time.perf_counter()
        self.reset_signal()
        print(f"[EMG] opened {port}", flush=True)

    def _run(self):
        while self._running:
            if serial is None:
                self.conn = "no_pyserial"
                time.sleep(0.5)
                continue
            if self._reconnect:
                self._reconnect = False
                self._close()
                self._cand_i = 0
            if not self.wanted():
                if self._ser is not None:
                    self._close()
                    print("[EMG] port closed (EMG off, view closed)", flush=True)
                self.conn = "off"
                time.sleep(0.2)
                continue
            if self._ser is None:
                if time.perf_counter() < self._next_try:
                    time.sleep(0.1)
                    continue
                self._open()
                continue
            try:
                line = self._ser.readline()
            except (serial.SerialException, OSError, TypeError, AttributeError):
                print(f"[EMG] {self.port} lost, reconnecting", flush=True)
                self._close()
                self.conn = "searching"
                self._next_try = time.perf_counter() + RETRY_S
                continue
            now = time.perf_counter()
            if line:
                try:
                    v = float(line.strip())
                except ValueError:
                    v = None                      # half line right after opening, or text
                if v is not None:
                    self._last_data = now
                    self.conn = "connected"
                    self.feed(v, now)
                    continue
            silent = now - self._last_data
            if silent > NO_DATA_REOPEN_S:
                self._close()                     # wrong port or a hung board: try again (auto: next port)
                self._cand_i += 1
                self._next_try = now + 0.5
            elif silent > NO_DATA_S:
                self.conn = "no_data"
