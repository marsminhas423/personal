# Roku Device Simulator

Turn your computer into a fake Roku TV / streaming stick that the **official
Roku mobile app** (and any Roku-compatible remote app) can discover and control
over your local network — just like a real Roku.

It's a single Python file, no dependencies. It speaks the two things a real
Roku exposes on the LAN:

| What | Port | Purpose |
|------|------|---------|
| **SSDP discovery** | UDP `1900` | Lets the Roku app *find* this machine |
| **ECP HTTP server** | TCP `8060` | Answers queries + receives button presses |

ECP = [External Control Protocol](https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md), the same API a real Roku uses.

---

## Requirements

- Python 3.8+ (uses only the standard library)
- The computer running this **and** your phone must be on the **same Wi-Fi / LAN**
- The app connects to your machine's real LAN IP, so this must run on **your own
  machine** — not a cloud container or a different network.

## Run it

```bash
python3 roku_sim.py
```

You'll see something like:

```
############################################################
#  Roku Device Simulator
############################################################
  Pretending to be : My Roku Simulator (TV)
  This machine's IP : 192.168.1.42
  Serial            : SIM3F9A1C2E4
  Open the Roku mobile app on the SAME Wi-Fi network to connect.
```

### Options

```bash
python3 roku_sim.py --name "Living Room TV"   # custom name in the app
python3 roku_sim.py --stick                   # advertise as a stick, not a TV
python3 roku_sim.py --ip 192.168.1.42         # force a specific LAN IP
```

## Connect from the Roku app

1. Install the **Roku** app (iOS App Store / Google Play) on your phone.
2. Make sure the phone is on the **same Wi-Fi** as this computer.
3. Open the app → **Devices** → it should auto-discover **"My Roku Simulator"**.
4. Tap it, then tap **Remote**. Every button you press shows up live in the
   terminal, and the little on-screen "TV" panel updates.

Try: navigation arrows, OK/Select, Home, Back, Play/Pause, Volume, and the
on-screen keyboard (type into a search box — the characters appear in the
terminal's `Typing:` line).

## Command-line control (no app needed)

Because it's just HTTP, you can drive it with `curl` too:

```bash
curl -d '' http://192.168.1.42:8060/keypress/Home
curl -d '' http://192.168.1.42:8060/keypress/Up
curl -d '' http://192.168.1.42:8060/keypress/Select
curl -d '' http://192.168.1.42:8060/keypress/VolumeUp
curl -d '' http://192.168.1.42:8060/launch/12          # launch Netflix
curl      http://192.168.1.42:8060/query/device-info   # device details
curl      http://192.168.1.42:8060/query/apps          # installed channels
```

## Making buttons do real things

Right now button presses update the simulated on-screen state and log to the
terminal. To make them control something real on your machine, edit the
`# --- HOOK ---` section inside `handle_key()` in `roku_sim.py`. For example you
could forward media keys to your OS:

- **Linux:** `xdotool key XF86AudioPlay`
- **Windows:** `nircmd sendkeypress media_play_pause`
- **macOS:** an AppleScript / `osascript` media-key call

## Customize the channel list

Edit the `self.apps` dictionary in `roku_sim.py` (id → name). These show up on
the Channels tab in the Roku app.

## Troubleshooting

- **App doesn't find the device:** confirm both are on the same subnet; some
  Wi-Fi networks block multicast or isolate wireless clients ("AP/client
  isolation") — turn that off, or use the app's "add device by IP" and enter
  `http://<your-ip>:8060`.
- **Port 8060 already in use:** you already have a real Roku app/emulator
  running, or another instance. Stop it first.
- **Binding to port 1900 fails:** another SSDP/UPnP service is running. On
  Linux that may be fine; on Windows the "SSDP Discovery" service can conflict.
- **Firewall:** allow inbound UDP 1900 and TCP 8060 for Python.

## What this is / isn't

This is a **protocol-level simulator**: it convincingly answers the Roku
control protocol so remote apps treat it as a Roku and send it commands. It
does **not** stream video or run real Roku channels — it's for controlling,
automating, testing, and learning how Roku devices are discovered and driven.
