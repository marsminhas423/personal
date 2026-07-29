# Hisense VIDAA Mock TV Tester

> ⚠️ **The TLS keys are NOT in this repo.** `remoteclient_cert.pem` /
> `remoteclient_key.pem` are a RemoteCA-signed identity extracted from the
> official Hisense app and must not be published, so they're git-ignored here.
> Without them the mock auto-generates a self-signed cert — fine for testing
> **your own** remote app and for the self-test, but the **official RemoteNOW
> app** needs the real cert (a self-signed one triggers
> `SSLV3_ALERT_CERTIFICATE_UNKNOWN`). To get full RemoteNOW fidelity, drop your
> local copy of the two `.pem` files next to `hisense_tv_tester.py` before
> running — the code picks them up automatically.

A Python mock of a real Hisense VIDAA smart TV. It lets you develop and test a
remote-control app — including the **official Hisense RemoteNOW / VIDAA app** —
without owning a physical TV.

Discoverable on the LAN (SSDP + UPnP), speaks the real MQTT-over-TLS control
protocol on port 36669, and drives a browser control panel so you can watch
every packet, see the pairing PIN, and see the virtual TV's state live.

## Quick start

```bash
pip3 install flask cryptography
python3 hisense_tv_tester.py
```

Open the panel in a browser (default `http://127.0.0.1:5000` — if that port is
taken, e.g. by macOS AirPlay Receiver, set `HISENSE_PANEL_PORT=5055`):

```bash
HISENSE_PANEL_PORT=5055 python3 hisense_tv_tester.py
```

In the panel:
1. Pick a **model** (see below) and **pairing mode** — do this *before* pressing
   Start; the model can't be changed while the mock is running.
2. Click **Start**.
3. On your phone, open your remote app (or the official RemoteNOW / VIDAA app)
   on the same Wi-Fi — it should discover a TV named after the chosen model.
4. Tap it to connect. The mock pushes a PIN — it appears in the panel. Enter it
   in the app to pair.
5. Once paired, volume/keys/source/app-launch commands all work and are
   reflected live in the panel.

Run the self-test any time to verify the mock end-to-end without a phone:

```bash
python3 hisense_fidelity_test.py --panel-port 5055
```

## What it emulates

Two real, named models — both selectable in the panel, each with its own
pairing generation:

| Model | Year | VIDAA version | Pairing |
|---|---|---|---|
| **Hisense 55A6K** | 2023 | U7 | **Modern** — PIN + token issuance (`transport_protocol=3290`) |
| **Hisense 65U7QF** | 2020 | U4 | **Legacy** — static credentials, no PIN (`transport_protocol=2000`) |

All wire identity (model name, firmware string, MAC addresses, serial, UUID)
lives in the `MODELS` dict at the top of `hisense_tv_tester.py`.

## Protocol coverage

- **Discovery:** SSDP (port 1900) + UPnP device descriptor served on port
  38400 (`/MediaServer/rendererdevicedesc.xml`), matching the exact fields
  the real app's discovery filter checks (`transport_protocol`, `platform`,
  `macWifi`/`macEthernet`).
- **Control channel:** hand-rolled MQTT 3.1.1 broker over TLS 1.2 on port
  36669 (no `paho`/`amqtt` dependency) — mirrors the real Hisense broker
  (old mosquitto build, TLS 1.2 only).
- **Pairing:** TV-initiated PIN flow (`/authentication` → app shows PIN →
  app publishes `authenticationcode` → TV validates → `tokenissuance`), plus
  the post-pair `gettoken`/`tokenissuance` refresh cycle the real app expects.
- **Commands:** `sendkey` (full key set), `changevolume`, `changesource`,
  `launchapp`, `getdeviceinfo`, `gettvstate`, `applist`, `sourcelist`.

## Verified against

The protocol details (topic names, descriptor fields, pairing sequence, and
critically the **TLS certificate chain** the official app validates) were
confirmed by decompiling the genuine Android RemoteNOW app and cross-checking
against community references (`pyvidaa`, `mqtt-hisensetv`). The mock has been
tested end-to-end against the real, official **Android RemoteNOW app**:
discovery → TLS → PIN pairing → token handshake → live volume/key control —
all confirmed working.

**iOS note:** the iOS RemoteNOW/VIDAA app uses a separate, proprietary UDP
discovery protocol (port 36671) that could not be reverse-engineered without
either a real Hisense TV to packet-capture or a jailbroken device to decrypt
the app binary. iOS discovery is best-effort; Android is the fully verified
path.

## Files

| File | Purpose |
|---|---|
| `hisense_tv_tester.py` | The mock server + browser control panel (single file) |
| `hisense_fidelity_test.py` | Headless client that pairs and exercises every command |
| `mediaserver_assets/*.xml` | Static UPnP/DLNA service descriptors served to clients |
| `SESSION_PROGRESS.md` | Development log / findings from building this mock |

## Requirements

Python 3 with `flask` and `cryptography`. No other dependencies — the MQTT
broker and SSDP responder are hand-rolled over stdlib sockets. `sudo` is only
needed if you want inbound SSDP on port 1900 to work on macOS (mDNSResponder
sometimes holds it); the mock still works for direct app-to-IP connection
without it.
