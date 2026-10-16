# fiiopeq

[<img src="https://www.headphonezone.in/cdn/shop/files/Headphone-Zone-Snowsky-Tiny-Silver-A-0001.jpg?v=1782386437&width=400" alt="SNOWSKY TINY USB DAC" width="260">](https://www.headphonezone.in/products/snowsky-tiny)

CLI to load, unload and reset the hardware parametric EQ (PEQ) of a FiiO / SNOWSKY USB DAC on macOS, as an alternative to the FiiO Control app.

Supported: SNOWSKY TINY A / TINY B (10 bands, ±12 dB, 3 user slots). Other FiiO models are refused because their slot layouts differ.

## Setup

```
cd fiio-peq
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run everything with `.venv/bin/python fiiopeq.py ...`. If macOS blocks access to the device, allow your terminal under System Settings > Privacy & Security > Input Monitoring.

## Commands

| Command | What it does |
|---|---|
| `status` | Show the active slot and its filters |
| `load FILE [--slot USER1\|USER2\|USER3]` | Write an EQ file to a user slot and activate it (default `USER1`) |
| `unload` | Turn PEQ off (Close EQ). Saved slots are kept |
| `reset USER1\|USER2\|USER3\|all` | Overwrite a slot with a flat 0 dB curve |

Use `--pid 0x0149` before the command if several supported DACs are connected.

## Examples

```
.venv/bin/python fiiopeq.py status
.venv/bin/python fiiopeq.py load chu2-harman-kazi.txt --slot USER3
.venv/bin/python fiiopeq.py unload
.venv/bin/python fiiopeq.py reset USER2
cat eq.txt | .venv/bin/python fiiopeq.py load - --slot USER1
```

## Input format

Equalizer APO / AutoEq text, for example:

```
Preamp: -3 dB
Filter 1: ON LSC Fc 105 Hz Gain -3.4 dB Q 0.70
Filter 2: ON PK Fc 5976 Hz Gain 2.5 dB Q 2.12
Filter 3: ON HSC Fc 10000 Hz Gain -1.6 dB Q 0.70
```

- Filter types: `PK`, `LS`/`LSC`/`LSQ`, `HS`/`HSC`/`HSQ`. Lines marked `OFF` are skipped.
- Limits: up to 10 filters, gain ±12 dB, Q 0.1–10, frequency 20–20000 Hz. Input outside these limits is rejected.
- Unused bands are written as flat 0 dB.
- The DAC stores preamp in whole dB, so it is rounded (negative values round down for headroom). A note is printed when this happens.

## Notes

- `load` and `reset` overwrite the chosen slot permanently on the DAC. `backup/` holds the original USER1–3 contents as loadable files, e.g. `load backup/USER3.txt --slot USER3` restores one.
- After writing, the CLI reads the slot back and warns if it differs. Differences of about 0.1 dB or 0.01 Q come from the DAC's own rounding.
- Protocol source: [devicePEQ](https://github.com/cscs54cs/devicePEQ). EQ files for many IEMs and headphones: [AutoEq](https://github.com/jaakkopasanen/AutoEq).
