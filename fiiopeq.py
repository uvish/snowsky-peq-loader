#!/usr/bin/env python3
"""Load / unload / reset the hardware PEQ of a FiiO / SNOWSKY USB DAC over USB HID.

Protocol taken from the open-source devicePEQ project (cscs54cs/devicePEQ).
Supported: SNOWSKY TINY A / TINY B (10 bands, +-12 dB, 3 user slots).
"""
import argparse
import math
import re
import struct
import sys
import time

import hid

VID = 0x2972
REPORT_ID = 7
REPORT_LEN = 63

SET_HDR = (0xAA, 0x0A)
GET_HDR = (0xBB, 0x0B)
END = 0xEE

CMD_PARAMS = 0x15
CMD_PRESET = 0x16
CMD_GAIN = 0x17
CMD_COUNT = 0x18
CMD_SAVE = 0x19

MODELS = {"SNOWSKY TINY A", "SNOWSKY TINY B"}
MAX_FILTERS = 10
FLAT = {"type": "PK", "freq": 1000, "gain": 0.0, "q": 1.0}
GAIN_LIMIT = 12.0
Q_MIN, Q_MAX = 0.1, 10.0
FREQ_MIN, FREQ_MAX = 20, 20000
CLOSE_EQ = 240

USER_SLOTS = {"USER1": 160, "USER2": 161, "USER3": 162}
SLOT_NAMES = {
    0: "Jazz", 1: "Pop", 2: "Rock", 3: "Dance", 4: "R&B", 5: "Classic", 6: "Hip-hop",
    160: "USER1", 161: "USER2", 162: "USER3", CLOSE_EQ: "Close EQ (PEQ off)",
}

TYPE_TO_CODE = {"PK": 0, "LS": 1, "HS": 2}
CODE_TO_TYPE = {v: k for k, v in TYPE_TO_CODE.items()}
APO_TYPES = {"PK": "PK", "PEAKING": "PK", "LS": "LS", "LSC": "LS", "LSQ": "LS",
             "HS": "HS", "HSC": "HS", "HSQ": "HS"}


class Dac:
    def __init__(self, pid=None):
        devs = [d for d in hid.enumerate(VID) if d["product_string"] in MODELS
                and (pid is None or d["product_id"] == pid)]
        if not devs:
            found = sorted({d["product_string"] or "?" for d in hid.enumerate(VID)})
            sys.exit("No supported DAC found (%s). FiiO devices seen: %s"
                     % (", ".join(sorted(MODELS)), ", ".join(found) or "none"))
        self.info = devs[0]
        self.dev = hid.device()
        self.dev.open_path(self.info["path"])
        self.dev.set_nonblocking(0)

    def close(self):
        self.dev.close()

    def _send(self, hdr, cmd, payload=b""):
        pkt = bytes([*hdr, 0, 0, cmd, len(payload)]) + payload + bytes([0, END])
        self.dev.write(bytes([REPORT_ID]) + pkt.ljust(REPORT_LEN, b"\0"))

    def _drain(self):
        self.dev.set_nonblocking(1)
        while self.dev.read(64):
            pass
        self.dev.set_nonblocking(0)

    def get(self, cmd, payload=b""):
        self._drain()
        self._send(GET_HDR, cmd, payload)
        deadline = time.time() + 2
        while time.time() < deadline:
            r = bytes(self.dev.read(64, 500))
            if r and r[0] == REPORT_ID:
                r = r[1:]
            if len(r) > 6 and r[0] == GET_HDR[0] and r[1] == GET_HDR[1] and r[4] == cmd:
                return r[6:6 + r[5]]
        sys.exit("Timed out waiting for reply to command 0x%02X" % cmd)

    def set(self, cmd, payload):
        any_reply = cmd == CMD_SAVE
        for _ in range(3):
            self._drain()
            self._send(SET_HDR, cmd, payload)
            deadline = time.time() + 1
            while time.time() < deadline:
                r = bytes(self.dev.read(64, 200))
                if r and r[0] == REPORT_ID:
                    r = r[1:]
                if r and any_reply:
                    return
                if len(r) > 5 and r[0] == SET_HDR[0] and r[1] == SET_HDR[1] and r[4] == cmd:
                    return
        sys.exit("DAC did not acknowledge command 0x%02X" % cmd)

    def get_slot(self):
        return self.get(CMD_PRESET)[0]

    def set_slot(self, slot):
        deadline = time.time() + 3
        while True:
            self.set(CMD_PRESET, bytes([slot]))
            time.sleep(0.15)
            if self.get_slot() == slot:
                return
            if time.time() > deadline:
                sys.exit("DAC did not switch to slot %d" % slot)

    def read_slot_state(self):
        """Read preamp + filters of the currently active slot (reads make the DAC report a scratch slot, so restore it)."""
        slot = self.get_slot()
        preamp = struct.unpack(">h", self.get(CMD_GAIN)[:2])[0] / 10
        count = self.get(CMD_COUNT)[0]
        filters = []
        for i in range(count):
            p = self.get(CMD_PARAMS, bytes([i]))
            gain, freq, q = struct.unpack(">hHH", p[1:7])
            filters.append({"type": CODE_TO_TYPE.get(p[7], "PK"), "freq": freq,
                            "gain": gain / 10, "q": q / 100})
        if slot in SLOT_NAMES:
            self.set_slot(slot)
        return preamp, filters

    def write_slot_state(self, slot, preamp, filters):
        self.set_slot(slot)
        self.set(CMD_GAIN, struct.pack(">h", round(preamp * 10)))
        for i, f in enumerate(filters):
            self.set(CMD_PARAMS, bytes([i]) + struct.pack(">hHH", round(f["gain"] * 10), f["freq"],
                                                          round(f["q"] * 100)) + bytes([TYPE_TO_CODE[f["type"]]]))
        time.sleep(0.1)
        self.set(CMD_SAVE, bytes([slot]))
        time.sleep(0.3)


def parse_apo(text):
    preamp, filters = 0.0, []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"preamp\s*:\s*(-?[\d.]+)\s*db", line, re.I)
        if m:
            preamp = float(m.group(1))
            continue
        m = re.match(r"filter\s*\d*\s*:\s*(on|off)\s+(\w+)\s+fc\s+([\d.]+)\s*hz\s+gain\s+(-?[\d.]+)\s*db"
                     r"(?:\s+q\s+([\d.]+))?", line, re.I)
        if not m:
            continue
        state, ftype, freq, gain, q = m.groups()
        if state.lower() == "off":
            continue
        ftype = APO_TYPES.get(ftype.upper())
        if ftype is None:
            sys.exit("Unsupported filter type in line: %s (supported: PK, LS/LSC, HS/HSC)" % line)
        filters.append({"type": ftype, "freq": round(float(freq)), "gain": float(gain),
                        "q": float(q) if q else 0.707})
    return preamp, filters


def pad(filters):
    return filters + [dict(FLAT)] * (MAX_FILTERS - len(filters))


def whole_db(preamp):
    return float(math.floor(preamp) if preamp < 0 else int(preamp + 0.5))


def validate(preamp, filters):
    if not filters:
        sys.exit("No usable filters found in input.")
    if len(filters) > MAX_FILTERS:
        sys.exit("This DAC supports at most %d filters, input has %d." % (MAX_FILTERS, len(filters)))
    if abs(preamp) > GAIN_LIMIT:
        sys.exit("Preamp %.1f dB is outside +-%d dB." % (preamp, GAIN_LIMIT))
    for i, f in enumerate(filters, 1):
        if abs(f["gain"]) > GAIN_LIMIT:
            sys.exit("Filter %d gain %.1f dB is outside +-%d dB." % (i, f["gain"], GAIN_LIMIT))
        if not Q_MIN <= f["q"] <= Q_MAX:
            sys.exit("Filter %d Q %.2f is outside %.1f-%.1f." % (i, f["q"], Q_MIN, Q_MAX))
        if not FREQ_MIN <= f["freq"] <= FREQ_MAX:
            sys.exit("Filter %d frequency %d Hz is outside %d-%d." % (i, f["freq"], FREQ_MIN, FREQ_MAX))


def slot_arg(name):
    key = name.upper()
    if key not in USER_SLOTS:
        sys.exit("Slot must be one of: %s" % ", ".join(USER_SLOTS))
    return USER_SLOTS[key]


def print_state(preamp, filters, show_flat=False):
    print("  preamp: %+.1f dB" % preamp)
    hidden = 0
    for i, f in enumerate(filters, 1):
        if f["gain"] == 0 and not show_flat:
            hidden += 1
            continue
        print("  %d: %-2s  %6d Hz  %+5.1f dB  Q %.2f" % (i, f["type"], f["freq"], f["gain"], f["q"]))
    if hidden:
        print("  (%d flat 0 dB filters not shown)" % hidden)


def verify(dac, slot, preamp, filters):
    got_preamp, got = dac.read_slot_state()
    ok = abs(got_preamp - preamp) < 0.05 and len(got) == len(filters) and all(
        a["type"] == b["type"] and a["freq"] == b["freq"] and abs(a["gain"] - b["gain"]) < 0.15
        and abs(a["q"] - b["q"]) < 0.02 for a, b in zip(got, filters))
    if not ok:
        print("WARNING: read-back differs from what was written:")
        print_state(got_preamp, got, show_flat=True)
    return ok


def cmd_status(dac, args):
    slot = dac.get_slot()
    print("%s  (active: %s)" % (dac.info["product_string"], SLOT_NAMES.get(slot, "slot %d" % slot)))
    if slot == CLOSE_EQ:
        return
    preamp, filters = dac.read_slot_state()
    print_state(preamp, filters)


def cmd_load(dac, args):
    text = sys.stdin.read() if args.file == "-" else open(args.file).read()
    preamp, filters = parse_apo(text)
    validate(preamp, filters)
    slot = slot_arg(args.slot)
    if whole_db(preamp) != preamp:
        print("Note: the DAC only stores whole-dB preamp; %.1f dB -> %.0f dB." % (preamp, whole_db(preamp)))
        preamp = whole_db(preamp)
    filters = pad(filters)
    print("Writing to %s:" % SLOT_NAMES[slot])
    print_state(preamp, filters)
    dac.write_slot_state(slot, preamp, filters)
    if verify(dac, slot, preamp, filters):
        print("Loaded and active.")


def cmd_unload(dac, args):
    dac.set_slot(CLOSE_EQ)
    print("PEQ disabled." if dac.get_slot() == CLOSE_EQ else "WARNING: device did not report PEQ off.")


def cmd_reset(dac, args):
    slots = list(USER_SLOTS.values()) if args.slot.lower() == "all" else [slot_arg(args.slot)]
    previous = dac.get_slot()
    flat = pad([])
    for slot in slots:
        dac.write_slot_state(slot, 0.0, flat)
        print("%s reset to flat." % SLOT_NAMES[slot])
    dac.set_slot(previous if previous not in slots else CLOSE_EQ)
    if previous in slots:
        print("Active slot was reset; PEQ is now off.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=lambda x: int(x, 0), help="USB product id (if several DACs are connected)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show active slot and its filters").set_defaults(fn=cmd_status)
    p = sub.add_parser("load", help="load an Equalizer APO / AutoEq text file into a user slot and activate it")
    p.add_argument("file", help="path to .txt file, or - for stdin")
    p.add_argument("--slot", default="USER1", help="USER1, USER2 or USER3 (default USER1)")
    p.set_defaults(fn=cmd_load)
    sub.add_parser("unload", help="turn PEQ off (Close EQ); saved slots are kept").set_defaults(fn=cmd_unload)
    p = sub.add_parser("reset", help="overwrite a user slot with a flat (0 dB) curve")
    p.add_argument("slot", help="USER1, USER2, USER3 or all")
    p.set_defaults(fn=cmd_reset)
    args = ap.parse_args()

    dac = Dac(args.pid)
    try:
        args.fn(dac, args)
    finally:
        dac.close()


if __name__ == "__main__":
    main()
