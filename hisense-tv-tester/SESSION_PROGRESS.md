# Hisense VIDAA Mock TV Tester — Progress

Mock of a real Hisense VIDAA TV to test the user's own remote app AND the official
**RemoteNOW** app without hardware. Same Flask control-panel pattern as the LG webOS
and Vizio SmartCast mocks.

## Status — 2026-07-03

Built + self-test PASSING for both pairing generations.

- `hisense_tv_tester.py` — mock + browser panel (single file).
- `hisense_fidelity_test.py` — headless MQTT client that pairs and drives every command.
- `mock_hisense_cert.pem` / `_key.pem` — auto-generated self-signed cert (CN=hisense.com).

## Run

```
pip3 install flask cryptography
python3 hisense_tv_tester.py          # panel at http://127.0.0.1:5000
# → open panel, pick model + pairing mode, Start Mock TV
sudo python3 hisense_tv_tester.py     # sudo only needed for SSDP inbound (port 1900)
python3 hisense_fidelity_test.py      # self-test against the running mock
```
Port overrides: `HISENSE_PANEL_PORT`, `HISENSE_MQTT_PORT`.

## What it emulates (real models, identity on the wire)

- **Hisense 55A6K** (2023, VIDAA U7) — default **modern** pairing: PIN + tokenissuance.
- **Hisense 65U7QF** (2020, VIDAA U4) — default **legacy** pairing: static creds, no PIN.
- Both models + both pairing modes are selectable in the panel.

## Protocol implemented (hand-rolled, no paho/amqtt)

- **Minimal MQTT 3.1.1 broker over TLS on 36669**: CONNECT/CONNACK, SUBSCRIBE/SUBACK,
  PUBLISH+PUBACK (both directions), PINGREQ/PINGRESP, UNSUBSCRIBE, DISCONNECT.
- **mutual TLS = CERT_OPTIONAL** — requests but doesn't require the client cert, so both
  a cert-presenting RemoteNOW and a plain client connect. Logs whether a client cert
  was presented.
- **Credentials**: legacy `hisenseservice`/`multimqttservice` checked (warn-not-reject);
  modern dynamic creds accepted (validate=off, like real self-signed setups).
- **Topics**: app→TV `/remoteapp/tv/{service}/{clientid}/actions/{action}`;
  TV→app `/remoteapp/mobile/{clientid}/{service}/data/{datatype}`.
- **Pairing**: `vidaa_app_connect` → PIN shown in panel → `authenticationcode`
  `{"authNum":"1234"}` → `tokenissuance` (modern) / immediate `authentication result:1`
  (legacy).
- **Commands verified**: sendkey (vol/mute/power/home/channel + all KEY_*), changevolume,
  getvolume, changesource, sourcelist, applist, launchapp, gettvstate. State broadcasts
  on `ui_service/data/state`.
- **Discovery**: SSDP responder + periodic ssdp:alive NOTIFY (UPnP MediaRenderer);
  `/ssdp/device-desc.xml` served from the panel with real Hisense identity.

## Self-test result (2026-07-03)

Both modes green. Modern: TLS+CONNECT ok, PIN read from panel, token issued, all commands
returned correct data (vol 42→VOLUMEUP→43, mute on, source→HDMI 1, app→Netflix, 0 unknown).
Legacy: no PIN, `authentication result:1`, commands ok.
Notably, real UPnP devices on the LAN fetched our `/ssdp/device-desc.xml` in response to
the NOTIFY beacon — confirms discovery advertising reaches the network.

## 2026-07-03 (later) — RemoteNOW discovery fix

First RemoteNOW attempt: app never touched MQTT (0 connects), only generic SSDP traffic
in log. Root cause found by reading `myhisense-tv`/`pyvidaa` (decompiled from the real
VIDAA APK): the app validates a Hisense-specific descriptor we didn't serve.

Real VIDAA TVs expose THREE discovery surfaces (mock now implements all):
1. **HTTP descriptor on port 38400**: `/MediaServer/rendererdevicedesc.xml`. The app
   parses `modelDescription` for key=value lines and REJECTS devices lacking
   **`vidaa_support=1`**. Also carries `mac`, `macWifi`, `macEthernet`, `brand=his`,
   **`transport_protocol`** (>= 3290 → modern auth; < 3000 → legacy; some firmwares use
   port 18400 instead — override with `HISENSE_UPNP_PORT`).
2. **UDP discovery on 36671**: app broadcasts `{"request":"discover"}` (and variants);
   TV replies JSON `{devicename, model, brand, mac, ip, port, vidaa_support, transport_protocol}`.
3. **SSDP 1900**: `LOCATION` must point at the 38400 descriptor (was pointing at the
   panel — fixed).

Verified locally: descriptor served with `vidaa_support=1` ✓, UDP 36671 replies ✓,
SSDP LOCATION correct ✓, MQTT fidelity test still fully green ✓.

Useful reference clone: `/tmp/myhisense-tv` (pyvidaa — protocol constants incl. modern
credential-generation algorithm in `credentials.py`, client cert in package).

## 2026-07-03 (later still) — RemoteNOW TLS + post-handshake fixes

Second RemoteNOW attempt: discovery worked, app reached MQTT on 36669, but TLS failed:
`[SSL: UNEXPECTED_END_OF_EARLY_DATA] no certificate returned`. Two root causes (both
fixed, cross-checked against decompiled `pyvidaa`):

1. **TLS 1.3 offered.** Real broker is mosquitto 1.4.2 = TLS 1.2 only; the VIDAA client
   aborts on 1.3 early-data. Fix: pin `minimum_version = maximum_version = TLSv1_2` and
   broaden ciphers (`DEFAULT:@SECLEVEL=1`, fallback 0). Verified: handshake now negotiates
   TLSv1.2.
2. **`CERT_OPTIONAL`** made us send a CertificateRequest, then fail validating the app's
   client cert against a CA we don't have. Fix: `CERT_NONE` — don't request a client cert;
   auth is at the MQTT PIN layer. A cert-holding client still connects. (`pyvidaa` confirms
   the reference client uses `tls_insecure_set(True)` / no server verify by default.)
3. **Server cert CN** now `RemoteCA` (real TVs present CN=RemoteCA, not an IP). Delete the
   .pem pair to regenerate if you change it.

Post-handshake mismatches that would have stalled pairing (fixed, from `pyvidaa/topics.py`
+ `client.py`):
- **tokenissuance** is on `platform_service` (not ui_service) with **lowercase**
  `accesstoken`/`refreshtoken` keys — we sent ui_service + camelCase. Now send correct
  service + both key cases.
- **state** and **volume** replies are also emitted on the **broadcast** topics
  `/remoteapp/mobile/broadcast/ui_service/{state,volume}` the app subscribes to (was
  per-client only). Now published to both.
- Added handlers RemoteNOW calls during connect: `gettvinfo`, `getdeviceinfo`,
  `capability` (were logged as unknown → app could stall waiting for a reply).

Full fidelity self-test still green over TLS 1.2 after all changes.

## Next / open

- [ ] Test the user's own remote app against the mock (LAN IP).
- [ ] Test the **official RemoteNOW app**: SSDP discover → PIN pair → control. This is the
  fidelity bar (as with LG/Vizio). Known risk: RemoteNOW may check an undocumented field,
  a follow-up endpoint, or pin the server cert CN — debug from the panel wire log and
  adjust the model constants (all identity is parameterized at the top of the file).
- [ ] If RemoteNOW requires a specific server-cert CN, delete the .pem files and set the
  model's `tls_cn` to the real value before restart (cert regenerates on next run).
