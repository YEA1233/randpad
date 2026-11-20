#!/usr/bin/env python3
"""RandPad: virtual Xbox-style gamepad that either passes your real controller
through or sends random inputs. DuckStation (or anything) binds the virtual pad only."""
import queue
import random
import selectors
import threading
import time
import tkinter as tk
from tkinter import ttk

from evdev import UInput, InputDevice, AbsInfo, ecodes as e, list_devices

VNAME = "Microsoft X-Box 360 pad"

BTN_GROUPS = {
    "Face buttons": [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST],
    "Shoulders (L1/R1)": [e.BTN_TL, e.BTN_TR],
    "Stick clicks (L3/R3)": [e.BTN_THUMBL, e.BTN_THUMBR],
}
OTHER_GROUPS = ["D-pad", "Left stick", "Right stick", "Triggers (L2/R2)"]
DEFAULT_ON = {"Face buttons", "Shoulders (L1/R1)", "D-pad", "Left stick", "Right stick"}

ALL_BTNS = [b for g in BTN_GROUPS.values() for b in g]
EXTRA = [e.BTN_SELECT, e.BTN_START, e.BTN_MODE]  # forwarded from the real pad, never random
COMBOS = {"L3 + R3": {e.BTN_THUMBL, e.BTN_THUMBR},
          "Select + Start": {e.BTN_SELECT, e.BTN_START}}

# real DS4 (hid-playstation): X/Y left stick, Z/RZ right stick, RX/RY = L2/R2
STICK_MAP = {e.ABS_X: e.ABS_X, e.ABS_Y: e.ABS_Y, e.ABS_Z: e.ABS_RX, e.ABS_RZ: e.ABS_RY}
TRIG_MAP = {e.ABS_RX: e.ABS_Z, e.ABS_RY: e.ABS_RZ}
HAT_AXES = (e.ABS_HAT0X, e.ABS_HAT0Y)
ALL_VABS = (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY, e.ABS_Z, e.ABS_RZ, e.ABS_HAT0X, e.ABS_HAT0Y)


def list_gamepads():
    out = []
    for path in list_devices():
        try:
            d = InputDevice(path)
            caps = d.capabilities()
        except OSError:
            continue
        if d.name != VNAME and e.BTN_SOUTH in caps.get(e.EV_KEY, []) and e.EV_ABS in caps:
            out.append((f"{d.name} ({path})", path))
    return out


def list_keyboards():
    out = []
    for path in list_devices():
        try:
            d = InputDevice(path)
            keys = d.capabilities().get(e.EV_KEY, [])
        except OSError:
            continue
        if e.KEY_A in keys and e.KEY_F9 in keys and e.BTN_SOUTH not in keys:
            out.append(d)
    return out


class Engine(threading.Thread):
    def __init__(self, real_path, cfg, out_q):
        super().__init__(daemon=True)
        self.real_path, self.cfg, self.out_q = real_path, cfg, out_q
        self.cmd_q = queue.Queue()
        self.stop_flag = threading.Event()
        self.on = False

    # ---- virtual pad helpers ----
    def neutral(self):
        for b in ALL_BTNS + EXTRA:
            self.ui.write(e.EV_KEY, b, 0)
        for a in ALL_VABS:
            self.ui.write(e.EV_ABS, a, 0)
        self.ui.syn()

    def fwd_abs(self, code, value):
        if code in STICK_MAP:
            lo, hi = self.rng[code]
            v = int((value - lo) / (hi - lo) * 65535 - 32768)
            self.ui.write(e.EV_ABS, STICK_MAP[code], max(-32768, min(32767, v)))
        elif code in TRIG_MAP:
            lo, hi = self.rng[code]
            self.ui.write(e.EV_ABS, TRIG_MAP[code], int((value - lo) / (hi - lo) * 255))
        elif code in HAT_AXES:
            self.ui.write(e.EV_ABS, code, value)

    def resync(self):
        """Copy the real pad's current state to the virtual pad."""
        self.neutral()
        for b in self.real.active_keys():
            if b in ALL_BTNS + EXTRA:
                self.ui.write(e.EV_KEY, b, 1)
        for code, info in self.real.capabilities().get(e.EV_ABS, []):
            self.fwd_abs(code, self.real.absinfo(code).value)
        self.ui.syn()

    def send_random(self):
        g, ui = self.cfg["groups"], self.ui
        pool = [b for n, bs in BTN_GROUPS.items() if n in g for b in bs]
        n = random.randint(0, min(self.cfg["maxbtn"], len(pool)))
        pressed = set(random.sample(pool, n))
        for b in ALL_BTNS:
            ui.write(e.EV_KEY, b, 1 if b in pressed else 0)
        for name, axes in (("Left stick", (e.ABS_X, e.ABS_Y)), ("Right stick", (e.ABS_RX, e.ABS_RY))):
            for a in axes:
                ui.write(e.EV_ABS, a, random.randint(-32768, 32767) if name in g else 0)
        for a in (e.ABS_Z, e.ABS_RZ):
            ui.write(e.EV_ABS, a, random.choice([0, 255]) if "Triggers (L2/R2)" in g else 0)
        for a in HAT_AXES:
            ui.write(e.EV_ABS, a, random.choice([-1, 0, 1]) if "D-pad" in g else 0)
        ui.syn()

    def set_on(self, state):
        self.on = state
        if state:
            self.neutral()
        else:
            self.resync()
        self.out_q.put(("state", state))

    # ---- main loop ----
    def run(self):
        self.ui = self.real = None
        kbs = []
        try:
            stick = AbsInfo(0, -32768, 32767, 16, 128, 0)
            trig = AbsInfo(0, 0, 255, 0, 0, 0)
            hat = AbsInfo(0, -1, 1, 0, 0, 0)
            caps = {e.EV_KEY: ALL_BTNS + EXTRA,
                    e.EV_ABS: [(e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_RX, stick),
                               (e.ABS_RY, stick), (e.ABS_Z, trig), (e.ABS_RZ, trig),
                               (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)]}
            self.ui = UInput(caps, name=VNAME, vendor=0x045E, product=0x028E, version=0x110)
            self.real = InputDevice(self.real_path)
            self.rng = {c: (i.min, i.max) for c, i in self.real.capabilities().get(e.EV_ABS, [])
                        if c in STICK_MAP or c in TRIG_MAP}
            kbs = list_keyboards()
            sel = selectors.DefaultSelector()
            for d in [self.real] + kbs:
                sel.register(d, selectors.EVENT_READ)
            self.real.grab()
        except Exception as ex:
            self.out_q.put(("error", f"{type(ex).__name__}: {ex}"))
            self.cleanup(kbs)
            return

        kbmsg = (f"{len(kbs)} keyboard(s) watched for F9." if kbs else
                 "F9 hotkey unavailable (add yourself to the 'input' group); pad combo still works.")
        self.out_q.put(("started", f"Running. Real pad grabbed; {kbmsg}"))
        held, combo_active = set(), False
        next_tick = time.monotonic()
        try:
            self.resync()
            while not self.stop_flag.is_set():
                try:
                    while True:
                        cmd = self.cmd_q.get_nowait()
                        if cmd == "toggle":
                            self.set_on(not self.on)
                except queue.Empty:
                    pass

                tick = 1.0 / max(1, self.cfg["hz"])
                for key, _ in sel.select(min(0.02, max(0, next_tick - time.monotonic()))):
                    dev = key.fileobj
                    try:
                        events = list(dev.read())
                    except (BlockingIOError, OSError):
                        continue
                    for ev in events:
                        if dev is self.real:
                            if ev.type == e.EV_KEY:
                                (held.add if ev.value else held.discard)(ev.code)
                                combo = COMBOS[self.cfg["combo"]]
                                if combo <= held and not combo_active:
                                    combo_active = True
                                    self.set_on(not self.on)
                                elif not combo <= held:
                                    combo_active = False
                            if not self.on:
                                if ev.type == e.EV_KEY and ev.code in ALL_BTNS + EXTRA:
                                    self.ui.write(e.EV_KEY, ev.code, ev.value)
                                elif ev.type == e.EV_ABS:
                                    self.fwd_abs(ev.code, ev.value)
                                elif ev.type == e.EV_SYN:
                                    self.ui.syn()
                        elif ev.type == e.EV_KEY and ev.code == e.KEY_F9 and ev.value == 1:
                            self.set_on(not self.on)
                if time.monotonic() >= next_tick:
                    if self.on:
                        self.send_random()
                    next_tick = max(next_tick + tick, time.monotonic() - tick)
        except Exception as ex:
            self.out_q.put(("error", f"{type(ex).__name__}: {ex}"))
        finally:
            self.cleanup(kbs)
            self.out_q.put(("stopped", ""))

    def cleanup(self, kbs):
        try:
            if self.real:
                self.real.ungrab()
        except Exception:
            pass
        try:
            if self.ui:
                self.neutral()
                self.ui.close()
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RandPad")
        self.resizable(False, False)
        self.q = queue.Queue()
        self.engine = None
        self.cfg = {"hz": 60, "maxbtn": 3, "groups": set(DEFAULT_ON), "combo": "L3 + R3"}
        self.pads = {}
        pad = {"padx": 10, "pady": 4}

        top = ttk.LabelFrame(self, text="Real controller")
        top.pack(fill="x", **pad)
        self.pad_var = tk.StringVar()
        self.pad_box = ttk.Combobox(top, textvariable=self.pad_var, state="readonly", width=52)
        self.pad_box.pack(side="left", padx=6, pady=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=4)

        self.run_btn = ttk.Button(self, text="Start engine", command=self.toggle_engine)
        self.run_btn.pack(fill="x", **pad)

        self.status = tk.Label(self, text="Engine stopped", fg="gray")
        self.status.pack(**pad)

        self.big = tk.Button(self, text="RANDOM: OFF", font=("sans", 20, "bold"), height=2,
                             bg="#2e7d32", fg="white", state="disabled", command=self.hit_toggle)
        self.big.pack(fill="x", **pad)

        opt = ttk.LabelFrame(self, text="Settings (apply live)")
        opt.pack(fill="x", **pad)
        self.hz_var = tk.IntVar(value=60)
        self.mb_var = tk.IntVar(value=3)
        self.row(opt, 0, "Inputs per second", self.hz_var, 1, 240, self.on_change)
        self.row(opt, 1, "Max buttons at once", self.mb_var, 0, 8, self.on_change)
        ttk.Label(opt, text="Toggle combo (pad)").grid(row=2, column=0, sticky="w", padx=6)
        self.combo_var = tk.StringVar(value="L3 + R3")
        cb = ttk.Combobox(opt, textvariable=self.combo_var, values=list(COMBOS), state="readonly", width=16)
        cb.grid(row=2, column=1, sticky="w", pady=4)
        cb.bind("<<ComboboxSelected>>", lambda _: self.on_change())
        ttk.Label(opt, text="Keyboard toggle: F9").grid(row=2, column=2, padx=10)

        grp = ttk.LabelFrame(self, text="What gets randomized")
        grp.pack(fill="x", **pad)
        self.gvars = {}
        for i, name in enumerate(list(BTN_GROUPS) + OTHER_GROUPS):
            v = tk.BooleanVar(value=name in DEFAULT_ON)
            self.gvars[name] = v
            ttk.Checkbutton(grp, text=name, variable=v, command=self.on_change).grid(
                row=i // 2, column=i % 2, sticky="w", padx=8, pady=2)

        self.log = tk.Label(self, text="", fg="gray", wraplength=420, justify="left")
        self.log.pack(**pad)
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(50, self.poll)

    def row(self, parent, r, label, var, lo, hi, cmd):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=6)
        s = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=var,
                     length=220, command=lambda _: cmd())
        s.grid(row=r, column=1, columnspan=2, sticky="w")

    def on_change(self):
        self.cfg["hz"] = self.hz_var.get()
        self.cfg["maxbtn"] = self.mb_var.get()
        self.cfg["combo"] = self.combo_var.get()
        self.cfg["groups"] = {n for n, v in self.gvars.items() if v.get()}

    def refresh(self):
        self.pads = dict(list_gamepads())
        names = list(self.pads)
        self.pad_box["values"] = names
        pick = next((n for n in names if "Wireless Controller" in n), names[0] if names else "")
        self.pad_var.set(pick)
        self.say("Found %d controller(s)." % len(names) if names else
                 "No controller found (check permissions: are you in the 'input' group?)")

    def say(self, msg, err=False):
        self.log.config(text=msg, fg="red" if err else "gray")

    def toggle_engine(self):
        if self.engine and self.engine.is_alive():
            self.engine.stop_flag.set()
            return
        path = self.pads.get(self.pad_var.get())
        if not path:
            self.say("Pick a controller first.", True)
            return
        self.on_change()
        self.engine = Engine(path, self.cfg, self.q)
        self.engine.start()
        self.run_btn.config(state="disabled")

    def hit_toggle(self):
        if self.engine:
            self.engine.cmd_q.put("toggle")

    def show_state(self, on):
        if on:
            self.big.config(text="RANDOM: ON", bg="#c62828")
            self.status.config(text="Sending random input", fg="#c62828")
        else:
            self.big.config(text="RANDOM: OFF", bg="#2e7d32")
            self.status.config(text="Passing your real controller through", fg="#2e7d32")

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "started":
                    self.run_btn.config(text="Stop engine", state="normal")
                    self.big.config(state="normal")
                    self.show_state(False)
                    self.say(val)
                elif kind == "state":
                    self.show_state(val)
                elif kind == "error":
                    self.say(val, True)
                elif kind == "stopped":
                    self.run_btn.config(text="Start engine", state="normal")
                    self.big.config(state="disabled", text="RANDOM: OFF", bg="#2e7d32")
                    self.status.config(text="Engine stopped", fg="gray")
        except queue.Empty:
            pass
        self.after(50, self.poll)

    def close(self):
        if self.engine and self.engine.is_alive():
            self.engine.stop_flag.set()
            self.engine.join(timeout=2)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
