#!/usr/bin/env python3
"""
Hisense VIDAA Remote Tester — Web UI
Emulates a real Hisense VIDAA TV on your laptop so you can test your own remote
app AND the official RemoteNOW app without physical hardware. Opens a browser
dashboard to start/stop the mock TV, pick the emulated model, choose the pairing
generation, see the on-screen pairing PIN, watch the virtual TV state, and follow
every MQTT packet on the wire. No coding needed to operate it.

Two real, selectable models (identity strings go on the wire, not placeholders):
  - Hisense 55A6K  (2023, VIDAA U7) — modern flow: PIN pairing + token issuance
  - Hisense 65U7QF (2020, VIDAA U4) — legacy flow: static creds, no token

Protocol references (reverse-engineered — verified across mqtt-hisensetv,
vidaa-control, ha_vidaatv):
  - MQTT v3.1.1 over TLS on TCP 36669 (self-signed; modern TVs also do mutual TLS —
    mock requests-but-does-not-require the client cert so both apps connect)
  - Legacy creds: user 'hisenseservice' / pass 'multimqttservice'
    Modern creds: dynamic per-device; mock accepts any well-formed creds (validate=off)
  - Publish (app->TV):  /remoteapp/tv/{service}/{clientid}/actions/{action}
    Subscribe (TV->app): /remoteapp/mobile/{clientid}/{service}/data/{datatype}
    Services: remote_service, ui_service, platform_service
  - Pairing: vidaa_app_connect -> TV shows 4-digit PIN -> authenticationcode
    {"authNum":"1234"} -> tokenissuance (modern only)
  - Discovery: SSDP on 239.255.255.250:1900 (UPnP/DLNA) so RemoteNOW auto-finds it
"""

import hashlib
import json
import os
import random
import socket
import ssl
import struct
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, Response, jsonify, render_template_string, request

# ── Emulated models — all wire identity lives here ────────────────────────────
# MAC OUI 2C:16:BD is a real Hisense allocation.
MODELS = {
    "55A6K": {
        "model_name": "55A6K",
        "series": "A6K",
        "friendly_name": "Hisense 55A6K",
        "vidaa_version": "VIDAA U7",
        "firmware": "V0000.01.00K.J1204",   # VIDAA-U7-style build string
        "year": 2023,
        "chipset": "MT9602",
        "wifi_mac": "2c:16:bd:5a:6b:01",
        "eth_mac": "2c:16:bd:5a:6b:02",
        "serial": "H55A6K23A0004217",
        "uuid": "uuid:8f3c10b4-a6k0-4a11-9c4e-2c16bd5a6b01",
        "tls_cn": "RemoteCA",
        "default_pairing": "modern",         # PIN + token issuance
        "transport_protocol": 3290,          # >= 3290 -> modern auth (per VIDAA APK)
    },
    "65U7QF": {
        "model_name": "65U7QF",
        "series": "U7QF",
        "friendly_name": "Hisense 65U7QF",
        "vidaa_version": "VIDAA U4",
        "firmware": "V0000.01.00U.K0530",
        "year": 2020,
        "chipset": "MT5658",
        "wifi_mac": "2c:16:bd:77:af:00",
        "eth_mac": "2c:16:bd:77:af:01",
        "serial": "H65U7QF20K0004217",
        "uuid": "uuid:8f3c10b4-u7qf-4a11-9c4e-2c16bd77af00",
        "tls_cn": "RemoteCA",
        "default_pairing": "legacy",          # static creds, no token
        "transport_protocol": 2000,           # < 3000 -> legacy auth (per VIDAA APK)
    },
}

# Legacy static MQTT credentials (real values from mqtt-hisensetv)
LEGACY_USER = "hisenseservice"
LEGACY_PASS = "multimqttservice"

MQTT_PORT = int(os.environ.get("HISENSE_MQTT_PORT", "36669"))
PANEL_PORT = int(os.environ.get("HISENSE_PANEL_PORT", "5000"))
UPNP_PORT = int(os.environ.get("HISENSE_UPNP_PORT", "38400"))   # rendererdevicedesc.xml
UDP_DISCOVERY_PORT = 36671   # Hisense-specific UDP discovery (VIDAA app broadcasts here)

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900

# Sources: sourceid -> display name (real VIDAA source ids)
SOURCES = {
    "0": "TV",
    "1": "AV",
    "3": "HDMI 1",
    "4": "HDMI 2",
    "5": "HDMI 3",
    "6": "HDMI 4",
    "10": "Component",
}

# Apps installed on the virtual TV (name shown in panel / applist response)
APPS = [
    {"name": "Netflix", "url": "netflix", "urlType": 37, "storeType": 0},
    {"name": "YouTube", "url": "youtube", "urlType": 37, "storeType": 0},
    {"name": "Prime Video", "url": "amazon", "urlType": 37, "storeType": 0},
    {"name": "Disney+", "url": "disneyplus", "urlType": 37, "storeType": 0},
    {"name": "VIDAA Free", "url": "vidaafree", "urlType": 37, "storeType": 0},
]

# Keys the TV accepts on remote_service/sendkey
KNOWN_KEYS = {
    "KEY_POWER", "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_OK",
    "KEY_HOME", "KEY_MENU", "KEY_EXIT", "KEY_RETURNS", "KEY_BACK",
    "KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_MUTE",
    "KEY_PLAY", "KEY_PAUSE", "KEY_STOP", "KEY_FORWARDS", "KEY_BACKWARD",
    "KEY_SUBTITLE", "KEY_INPUT", "KEY_CHANNELUP", "KEY_CHANNELDOWN",
    *(f"KEY_{d}" for d in range(10)),
}

# ── Shared state ──────────────────────────────────────────────────────────────
server_state = {
    "running": False,
    "model_key": "55A6K",
    "pairing_mode": "modern",     # "modern" | "legacy"
    "log": [],
    "tv": {
        "power": 1,
        "volume": 20,
        "muted": False,
        "sourceid": "0",
        "app": None,              # current app name or None (home)
        "channel": 1,
    },
    "stats": {"paired": 0, "commands": 0, "unknown": 0, "connects": 0},
    "active_pin": None,           # 4-digit PIN shown in browser ("TV screen")
    "pending_auth": {},           # clientid -> pin awaiting authenticationcode
    "tokens": {},                 # token -> clientid
}
stop_event = threading.Event()
mqtt_srv = {"server": None}
_lock = threading.Lock()


def model():
    return MODELS[server_state["model_key"]]


# Optional: set HISENSE_FOCUS_IP=192.168.x.y to only log traffic from that
# device (drops the constant SSDP/UPnP noise from every other box on the LAN,
# so a single client's full MQTT handshake stays readable).
FOCUS_IP = os.environ.get("HISENSE_FOCUS_IP", "").strip()


def add_log(direction, text, kind="cmd"):
    # When a focus IP is set, drop noisy discovery events from OTHER IPs.
    # MQTT/pairing events (kind != "disco") are always kept.
    if FOCUS_IP and kind == "disco" and FOCUS_IP not in text:
        return
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "direction": direction,   # "in" | "out" | "event"
        "text": text,
        "kind": kind,             # "pair" | "cmd" | "unknown" | "event" | "disco"
    }
    server_state["log"].append(entry)
    if len(server_state["log"]) > 2000:
        server_state["log"] = server_state["log"][-2000:]


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Minimal MQTT 3.1.1 broker (hand-rolled over a TLS socket — no paho/amqtt)
#  Implements only what the VIDAA protocol needs: CONNECT/CONNACK,
#  SUBSCRIBE/SUBACK, PUBLISH (both ways) + PUBACK, PINGREQ/PINGRESP, DISCONNECT.
# ══════════════════════════════════════════════════════════════════════════════
CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK = 8, 9, 10, 11
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14

_connections = []          # list of MQTTConnection (live)
_conn_lock = threading.Lock()


def _encode_remaining_length(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            break
    return bytes(out)


def _topic_matches(filt, topic):
    """MQTT topic-filter match with + and # wildcards."""
    f = filt.split("/")
    t = topic.split("/")
    for i, seg in enumerate(f):
        if seg == "#":
            return True
        if i >= len(t):
            return False
        if seg == "+":
            continue
        if seg != t[i]:
            return False
    return len(f) == len(t)


class MQTTConnection(threading.Thread):
    def __init__(self, sock, addr):
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.client_id = None
        self.subs = set()
        self.alive = True

    # -- low-level read helpers -------------------------------------------------
    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed")
            buf += chunk
        return buf

    def _read_remaining_length(self):
        multiplier = 1
        value = 0
        while True:
            b = self._read(1)[0]
            value += (b & 0x7F) * multiplier
            if not (b & 0x80):
                break
            multiplier *= 128
        return value

    @staticmethod
    def _read_str(buf, i):
        ln = struct.unpack(">H", buf[i:i + 2])[0]
        i += 2
        return buf[i:i + ln].decode("utf-8", "ignore"), i + ln

    def _send(self, data):
        try:
            self.sock.sendall(data)
        except Exception:
            self.alive = False

    # -- outbound PUBLISH from the TV ------------------------------------------
    def publish(self, topic, payload):
        tb = topic.encode()
        body = struct.pack(">H", len(tb)) + tb + payload.encode()
        pkt = bytes([PUBLISH << 4]) + _encode_remaining_length(len(body)) + body
        self._send(pkt)

    # -- main loop --------------------------------------------------------------
    def run(self):
        with _conn_lock:
            _connections.append(self)
        try:
            while self.alive and not stop_event.is_set():
                b1 = self._read(1)[0]
                ptype = b1 >> 4
                rlen = self._read_remaining_length()
                body = self._read(rlen) if rlen else b""
                self._dispatch(ptype, b1 & 0x0F, body)
        except Exception:
            pass
        finally:
            with _conn_lock:
                if self in _connections:
                    _connections.remove(self)
            try:
                self.sock.close()
            except Exception:
                pass

    def _dispatch(self, ptype, flags, body):
        if ptype == CONNECT:
            self._on_connect(body)
        elif ptype == SUBSCRIBE:
            self._on_subscribe(body)
        elif ptype == UNSUBSCRIBE:
            self._on_unsubscribe(body)
        elif ptype == PUBLISH:
            self._on_publish(flags, body)
        elif ptype == PINGREQ:
            self._send(bytes([PINGRESP << 4, 0]))
        elif ptype == DISCONNECT:
            self.alive = False
        # PUBACK from client (QoS1 downstream) — ignored

    def _on_connect(self, body):
        i = 0
        proto, i = self._read_str(body, i)
        level = body[i]; i += 1
        cflags = body[i]; i += 1
        keepalive = struct.unpack(">H", body[i:i + 2])[0]; i += 2
        client_id, i = self._read_str(body, i)
        # RemoteNOW ALWAYS sets a retained Will (f.java), so WillTopic+WillMessage
        # sit between ClientId and UserName. Consume them if the Will flag is set,
        # else username/password parse off-by-two and creds mis-log.
        if cflags & 0x04:
            _willtopic, i = self._read_str(body, i)
            _wlen = struct.unpack(">H", body[i:i + 2])[0]; i += 2
            i += _wlen  # will message is a length-prefixed binary blob
        username = password = None
        if cflags & 0x80:
            username, i = self._read_str(body, i)
        if cflags & 0x40:
            password, i = self._read_str(body, i)
        self.client_id = client_id or f"anon-{uuid.uuid4().hex[:8]}"
        server_state["stats"]["connects"] += 1

        # A fresh CONNECT means a fresh pairing attempt. If this client isn't
        # already paired (no token this session), drop any STALE pending PIN so
        # the upcoming SUBSCRIBE re-triggers a fresh /authentication push. Without
        # this, a client that got a PIN but abandoned it (dialog closed / timed
        # out) stays wedged in pending_auth forever and reconnect-loops every
        # keepalive because trigger_pairing keeps skipping it.
        already_paired = any(v == self.client_id
                             for v in server_state["tokens"].values())
        if not already_paired and self.client_id in server_state["pending_auth"]:
            server_state["pending_auth"].pop(self.client_id, None)
            if server_state.get("active_pin"):
                server_state["active_pin"] = None
            add_log("event", f"↻ fresh CONNECT from {self.client_id} — cleared "
                    "stale pending PIN so a new one will be issued", "pair")

        mode = server_state["pairing_mode"]
        cred_note = f"user={username!r}"
        if mode == "legacy":
            ok = (username == LEGACY_USER and password == LEGACY_PASS)
            if not ok:
                add_log("event", f"⚠ legacy CONNECT with non-standard creds "
                        f"({cred_note}) — accepting anyway (validate=off)", "unknown")
        add_log("in", f"🔌 CONNECT clientid={self.client_id!r} proto={proto}/{level} "
                f"{cred_note} keepalive={keepalive}s ({mode} mode)", "pair")
        # CONNACK: session-present=0, return-code=0 (accepted)
        self._send(bytes([CONNACK << 4, 2, 0, 0]))

    def _on_subscribe(self, body):
        pid = struct.unpack(">H", body[:2])[0]
        i = 2
        granted = []
        topics = []
        while i < len(body):
            topic, i = self._read_str(body, i)
            qos = body[i]; i += 1
            self.subs.add(topic)
            topics.append(topic)
            granted.append(min(qos, 1))
            if "#" in topic or "+" in topic:
                add_log("event", f"⚠ wildcard SUBSCRIBE {topic!r} — real TVs block "
                        "these; mock allows it for delivery", "unknown")
        add_log("in", f"📥 SUBSCRIBE {', '.join(topics)}", "cmd")
        resp = struct.pack(">H", pid) + bytes(granted)
        self._send(bytes([SUBACK << 4]) + _encode_remaining_length(len(resp)) + resp)
        # CRITICAL (from decompiled RemoteNOW): the app does NOT publish
        # vidaa_app_connect. It connects, subscribes, and WAITS for the TV to
        # spontaneously push /authentication (which pops the PIN dialog). So the
        # moment a mobile client subscribes to a topic that can receive it, the
        # TV must initiate pairing itself.
        for t in topics:
            if t.startswith("/remoteapp/mobile/") and (
                    "/ui_service/data/authentication" in t or t.endswith("/#")
                    or t.endswith("/ui_service/#")):
                cid = t.split("/remoteapp/mobile/", 1)[1].split("/", 1)[0]
                if cid and cid != "broadcast":
                    threading.Thread(target=trigger_pairing,
                                     args=(cid,), daemon=True).start()
                break

    def _on_unsubscribe(self, body):
        pid = struct.unpack(">H", body[:2])[0]
        i = 2
        while i < len(body):
            topic, i = self._read_str(body, i)
            self.subs.discard(topic)
        self._send(bytes([UNSUBACK << 4, 2]) + struct.pack(">H", pid))

    def _on_publish(self, flags, body):
        qos = (flags >> 1) & 0x03
        i = 0
        topic, i = self._read_str(body, i)
        pid = None
        if qos > 0:
            pid = struct.unpack(">H", body[i:i + 2])[0]; i += 2
        payload = body[i:].decode("utf-8", "ignore")
        if qos == 1 and pid is not None:
            self._send(bytes([PUBACK << 4, 2]) + struct.pack(">H", pid))
        route_publish(self, topic, payload)


def broker_publish(topic, payload_obj):
    """TV -> app(s): deliver to every connection subscribed to a matching filter."""
    payload = json.dumps(payload_obj, separators=(",", ":")) if not isinstance(
        payload_obj, str) else payload_obj
    delivered = 0
    with _conn_lock:
        conns = list(_connections)
    for c in conns:
        if any(_topic_matches(f, topic) for f in c.subs):
            c.publish(topic, payload)
            delivered += 1
    add_log("out", f"📤 {topic}  {payload}"
            + ("" if delivered else "  (no subscriber yet)"), "cmd")


# ── VIDAA message router: map action topics -> TV state + responses ───────────
def _mobile_topic(client_id, service, datatype):
    return f"/remoteapp/mobile/{client_id}/{service}/data/{datatype}"


def _broadcast_topic(service, datatype):
    return f"/remoteapp/mobile/broadcast/{service}/{datatype}"


def publish_data(client_id, service, datatype, payload, broadcast_as=None):
    """Publish a TV->app response on the per-client data topic, and (for state/
    volume) also on the broadcast topic the VIDAA app subscribes to. Real TVs
    emit state and volume on /remoteapp/mobile/broadcast/ui_service/{state,volume}."""
    broker_publish(_mobile_topic(client_id, service, datatype), payload)
    if broadcast_as:
        bsvc, bdata = broadcast_as
        broker_publish(_broadcast_topic(bsvc, bdata), payload)


def route_publish(conn, topic, payload):
    """Handle an app->TV PUBLISH. topic: /remoteapp/tv/{service}/{clientid}/actions/{action}"""
    server_state["stats"]["commands"] += 1
    parts = topic.strip("/").split("/")
    # ["remoteapp","tv",service,clientid,"actions",action]
    if len(parts) < 6 or parts[0] != "remoteapp" or parts[1] != "tv":
        add_log("in", f"❓ PUBLISH {topic}  {payload}  (unrecognized topic shape)",
                "unknown")
        server_state["stats"]["unknown"] += 1
        return
    service, client_id, action = parts[2], parts[3], parts[5]
    add_log("in", f"⬇ {service}/{action}  clientid={client_id}"
            + (f"  {payload}" if payload else ""), "cmd")

    try:
        data = json.loads(payload) if payload.strip() else {}
    except Exception:
        data = payload  # some actions send raw scalars (volume/key)

    handler = ROUTES.get((service, action))
    if handler:
        handler(client_id, data, payload)
    else:
        server_state["stats"]["unknown"] += 1
        add_log("event", f"❓ unknown action {service}/{action} "
                "(accepted silently like real firmware)", "unknown")


# -- pairing -------------------------------------------------------------------
# Verified against decompiled RemoteNOW (com.universal.remote.ms.manager.c.d):
#   datatype "/authentication"      -> UIInfo(3030): OPEN the PIN entry screen
#                                       (no payload read — pure trigger)
#   datatype "/authenticationcode"  -> AuthenResultInfo{result,info}:
#                                       result==1 -> UIInfo(3031) paired OK
#                                       result!=1 -> UIInfo(3032) wrong PIN
#   datatype "/authenticationcodeclose" -> UIInfo(3033): close the PIN screen
# The app never publishes vidaa_app_connect; the TV drives this on subscribe.
_pair_lock = threading.Lock()


def trigger_pairing(client_id):
    if not server_state.get("running"):
        return
    # Atomic guard: the app fires several SUBSCRIBEs (and a vidaa_app_connect) in
    # a burst, each of which would otherwise spawn its own PIN thread and race —
    # showing two different PINs where active_pin and pending_auth disagree. Claim
    # the slot under a lock BEFORE the sleep so only the first caller proceeds.
    with _pair_lock:
        if client_id in server_state["pending_auth"]:
            return
        if any(v == client_id for v in server_state["tokens"].values()):
            return
        pin = f"{random.randint(0, 9999):04d}"
        server_state["pending_auth"][client_id] = pin  # claim slot now
        server_state["active_pin"] = pin
    time.sleep(0.3)  # let the app finish its burst of SUBSCRIBEs first
    add_log("event", f"🔢 PIN shown on TV screen: {pin}  (enter it in the app) "
            f"— pushed /authentication to {client_id}", "pair")
    # Pure trigger: opens the PIN dialog in the app. Payload is ignored by the app.
    broker_publish(_mobile_topic(client_id, "ui_service", "authentication"), {})


def h_connect(client_id, data, raw):
    """vidaa_app_connect: the app publishes this on every (re)connect. Do NOT
    reply on its data topic — echoing a message there makes the app immediately
    drop and reconnect in a 2-4s loop (observed). Just (re)start the PIN flow;
    trigger_pairing is guarded so it won't re-prompt once paired."""
    trigger_pairing(client_id)


def _issue_token(client_id):
    token = uuid.uuid4().hex
    refresh = uuid.uuid4().hex
    server_state["tokens"][token] = client_id
    # The app subscribes to platform_service/data/tokenissuance and requests it
    # via platform_service/gettoken after the PIN. Field names are best-effort
    # (obfuscated in the app); cover the common cases. Sending SOMETHING on this
    # topic is what stops the post-pair reconnect loop.
    publish_data(client_id, "platform_service", "tokenissuance", {
        "result": 0,
        "accesstoken": token,
        "refreshtoken": refresh,
        "accessToken": token,
        "refreshToken": refresh,
        "token": token,
        "expire": 2592000,
        "expiretime": 2592000,
    })


def h_gettoken(client_id, data, raw):
    """platform_service/gettoken {"refreshtoken":""} -> issue a token so the app
    completes its post-pairing handshake (was the reconnect-loop cause)."""
    add_log("event", f"🎟️  gettoken from {client_id} — issuing token", "pair")
    _issue_token(client_id)


def _send_authcode_result(client_id, ok, info=""):
    # Success/failure BOTH go to datatype 'authenticationcode' (parsed as
    # AuthenResultInfo). Sending result to 'authentication' would just re-open
    # the PIN dialog -> infinite loop (the bug we had before).
    broker_publish(_mobile_topic(client_id, "ui_service", "authenticationcode"),
                   {"result": 1 if ok else 0, "info": info})


def h_authcode(client_id, data, raw):
    """authenticationcode {"authNum":"1234"} -> validate -> AuthenResultInfo."""
    got = str(data.get("authNum", data)) if isinstance(data, dict) else str(data)
    want = server_state["pending_auth"].get(client_id)
    if want and got == want:
        token = uuid.uuid4().hex
        server_state["tokens"][token] = client_id
        server_state["pending_auth"].pop(client_id, None)
        server_state["active_pin"] = None
        server_state["stats"]["paired"] += 1
        add_log("event", f"✅ PIN {got} correct — pairing OK", "pair")
        _send_authcode_result(client_id, True, "")
        # close the PIN dialog on the app
        broker_publish(_mobile_topic(client_id, "ui_service",
                                     "authenticationcodeclose"), {})
        # proactively issue a token (app requests it via gettoken right after)
        _issue_token(client_id)
    else:
        add_log("event", f"❌ wrong PIN: got {got}, expected {want}", "pair")
        _send_authcode_result(client_id, False, "auth failed")


# -- control -------------------------------------------------------------------
def _publish_state(client_id):
    tv = server_state["tv"]
    publish_data(client_id, "ui_service", "state", {
        "statetype": "app" if tv["app"] else "sourceswitch",
        "sourceid": tv["sourceid"],
        "sourcename": SOURCES.get(tv["sourceid"], "TV"),
        "appName": tv["app"] or "",
    }, broadcast_as=("ui_service", "state"))


def h_sendkey(client_id, data, raw):
    key = raw.strip().strip('"')
    tv = server_state["tv"]
    if key not in KNOWN_KEYS:
        add_log("event", f"❓ unknown key {key!r} (accepted like real firmware)",
                "unknown")
    if key == "KEY_VOLUMEUP":
        tv["volume"] = min(100, tv["volume"] + 1)
    elif key == "KEY_VOLUMEDOWN":
        tv["volume"] = max(0, tv["volume"] - 1)
    elif key == "KEY_MUTE":
        tv["muted"] = not tv["muted"]
    elif key == "KEY_POWER":
        tv["power"] = 0 if tv["power"] else 1
    elif key == "KEY_HOME":
        tv["app"] = None
    elif key in ("KEY_CHANNELUP", "KEY_CHANNELDOWN"):
        tv["channel"] = max(1, tv["channel"] + (1 if key.endswith("UP") else -1))
    add_log("event", f"🔘 key {key} → vol={tv['volume']} mute={tv['muted']} "
            f"power={'on' if tv['power'] else 'off'}", "cmd")


def h_changevolume(client_id, data, raw):
    try:
        vol = int(data if not isinstance(data, dict) else data.get("volume"))
        server_state["tv"]["volume"] = max(0, min(100, vol))
        add_log("event", f"🔊 volume → {server_state['tv']['volume']}", "cmd")
    except Exception:
        add_log("event", f"⚠ bad changevolume payload: {raw!r}", "unknown")


def h_getvolume(client_id, data, raw):
    vol = {"volume_type": 0, "volume_value": server_state["tv"]["volume"]}
    # App only reads volume on datatype 'getvolume' (or broadcast
    # platform_service/actions/volumechange) — 'volume' hits no case (verified).
    publish_data(client_id, "platform_service", "getvolume", vol)


def h_gettvinfo(client_id, data, raw):
    # Field names per decompiled CapabilityTvInfo.
    m = model()
    publish_data(client_id, "platform_service", "gettvinfo", {
        "brand": "his",
        "deviceid": m["wifi_mac"].replace(":", ""),
        "capability": "1",
        "featurecode": "",
        "fake_sleep": 0,
        "fake_sleep_state": 0,
        "audio_capture_version": 0,
    })


def h_getdeviceinfo(client_id, data, raw):
    # Field names per decompiled DeviceInfo.java (our old names were all wrong,
    # so every getter returned null and the device record stayed empty).
    m = model()
    publish_data(client_id, "platform_service", "getdeviceinfo", {
        "tv_name": m["friendly_name"],
        "model_name": m["model_name"],
        "ip": get_lan_ip(),
        "wlan0": m["wifi_mac"],
        "eth0": m["eth_mac"],
        "country": "US",
        "region": 6,
        "language": "eng",
        "platform": 1,
        "transport_protocol": m["transport_protocol"],
        "tv_version": m["firmware"],
        "vidaa_support": 1,
        "voice": 0,
    })


def h_capability(client_id, data, raw):
    publish_data(client_id, "ui_service", "capability", {
        "keys": sorted(KNOWN_KEYS),
        "sources": [{"sourceid": k, "sourcename": v} for k, v in SOURCES.items()],
        "app_launch": True,
        "voice": False,
    })


def h_changesource(client_id, data, raw):
    sid = str(data.get("sourceid")) if isinstance(data, dict) else str(data)
    if sid in SOURCES:
        server_state["tv"]["sourceid"] = sid
        server_state["tv"]["app"] = None
        add_log("event", f"📺 source → {SOURCES[sid]} (id {sid})", "cmd")
    else:
        add_log("event", f"⚠ unknown sourceid {sid!r}", "unknown")
    _publish_state(client_id)


def h_sourcelist(client_id, data, raw):
    lst = [{"sourceid": k, "sourcename": v, "displayname": v}
           for k, v in SOURCES.items()]
    broker_publish(_mobile_topic(client_id, "ui_service", "sourcelist"), lst)


def h_applist(client_id, data, raw):
    broker_publish(_mobile_topic(client_id, "ui_service", "applist"), APPS)


def h_launchapp(client_id, data, raw):
    name = data.get("name") if isinstance(data, dict) else None
    server_state["tv"]["app"] = name or "Unknown App"
    add_log("event", f"🚀 launch app → {server_state['tv']['app']}", "cmd")
    _publish_state(client_id)


def h_gettvstate(client_id, data, raw):
    _publish_state(client_id)


ROUTES = {
    ("ui_service", "vidaa_app_connect"): h_connect,
    ("ui_service", "authenticationcode"): h_authcode,
    ("remote_service", "sendkey"): h_sendkey,
    ("platform_service", "changevolume"): h_changevolume,
    ("platform_service", "getvolume"): h_getvolume,
    ("ui_service", "changesource"): h_changesource,
    ("ui_service", "sourcelist"): h_sourcelist,
    ("ui_service", "applist"): h_applist,
    ("ui_service", "launchapp"): h_launchapp,
    ("ui_service", "gettvstate"): h_gettvstate,
    ("ui_service", "capability"): h_capability,
    ("platform_service", "gettvinfo"): h_gettvinfo,
    ("platform_service", "getdeviceinfo"): h_getdeviceinfo,
    ("platform_service", "gettoken"): h_gettoken,
}


# ── TLS + accept loop ─────────────────────────────────────────────────────────
def _ensure_self_signed_cert():
    here = os.path.dirname(os.path.abspath(__file__))
    # PREFERRED: the real RemoteCA-signed server identity extracted from the
    # official app's own bundled remoteclientmobile.p12 (pw multiscreen123,
    # deobfuscated from d.a.e()). The app verifies the TV's server cert against
    # RemoteCA (res/raw/remoteca.bks) with hostname verification DISABLED
    # (f.java:115), so presenting this RemoteCA-issued cert makes the official
    # app's TLS handshake succeed — a self-signed cert triggers
    # SSLV3_ALERT_CERTIFICATE_UNKNOWN. Valid to 2043.
    real_cert = os.path.join(here, "remoteclient_cert.pem")
    real_key = os.path.join(here, "remoteclient_key.pem")
    if os.path.exists(real_cert) and os.path.exists(real_key):
        return real_cert, real_key
    certfile = os.path.join(here, "mock_hisense_cert.pem")
    keyfile = os.path.join(here, "mock_hisense_key.pem")
    if os.path.exists(certfile) and os.path.exists(keyfile):
        return certfile, keyfile
    import datetime as _dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, model()["tls_cn"])])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.utcnow() - _dt.timedelta(days=1))
            .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
            .sign(key, hashes.SHA256()))
    with open(keyfile, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()))
    with open(certfile, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return certfile, keyfile


def run_mqtt_server():
    try:
        certfile, keyfile = _ensure_self_signed_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Real Hisense broker is mosquitto 1.4.2 — TLS 1.2 only. Offering TLS 1.3
        # makes the VIDAA client abort with UNEXPECTED_END_OF_EARLY_DATA, so pin
        # the handshake to TLS 1.2.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # Old broker/client → broaden accepted ciphers (drop OpenSSL security
        # level so legacy suites the app offers are allowed).
        for cipher_spec in ("DEFAULT:@SECLEVEL=1", "DEFAULT:@SECLEVEL=0", "ALL"):
            try:
                ctx.set_ciphers(cipher_spec)
                break
            except ssl.SSLError:
                continue
        ctx.load_cert_chain(certfile, keyfile)
        # Do NOT request a client cert. The VIDAA app does mutual TLS, but if we
        # send a CertificateRequest we'd then try to verify its cert against a CA
        # we don't have and the handshake dies ("no certificate returned").
        # CERT_NONE = plain server-auth TLS; a cert-holding client still connects.
        # Real auth happens at the MQTT PIN layer, not the TLS layer.
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.check_hostname = False
        except Exception:
            pass

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", MQTT_PORT))
        srv.listen(16)
        srv.settimeout(0.5)
        mqtt_srv["server"] = srv
        m = model()
        add_log("event", f"Mock Hisense {m['model_name']} ({m['vidaa_version']}, "
                f"FW {m['firmware']}) — mqtts://{get_lan_ip()}:{MQTT_PORT} "
                f"(self-signed, CN={m['tls_cn']})", "event")
        print(f"\n✓ mqtts://0.0.0.0:{MQTT_PORT} listening "
              f"({m['model_name']}, CN={m['tls_cn']})\n", flush=True)

        while server_state["running"] and not stop_event.is_set():
            try:
                raw_sock, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                tls_sock = ctx.wrap_socket(raw_sock, server_side=True)
            except ssl.SSLError as e:
                add_log("event", f"⚠ TLS handshake failed from {addr[0]}: {e}",
                        "unknown")
                raw_sock.close()
                continue
            peer_cert = tls_sock.getpeercert(binary_form=True)
            add_log("event", f"🔒 TLS connect from {addr[0]} "
                    + ("(client presented a cert — mutual TLS)" if peer_cert
                       else "(no client cert)"), "disco")
            MQTTConnection(tls_sock, addr).start()
    except Exception as e:
        add_log("event", f"❌ MQTT server error: {type(e).__name__}: {e}", "unknown")
        print(f"❌ MQTT server error: {type(e).__name__}: {e}", flush=True)
        server_state["running"] = False
    finally:
        mqtt_srv["server"] = None
        add_log("event", "Mock TV MQTT broker stopped", "event")


# ── UPnP descriptor server on 38400 — what the VIDAA app actually validates ───
# Real TVs serve /MediaServer/rendererdevicedesc.xml here. The app parses
# modelDescription for key=value lines and REJECTS devices lacking vidaa_support=1.
# Vendor-specific UPnP service found bundled in the real VIDAA app's own assets
# (assets/MediaServer/hisense_lan_control.xml) — not documented anywhere public.
# The app ships this SCPD for ITS OWN embedded DLNA renderer/server (cast
# to/from phone), but the serviceType is a genuine Hisense extension, so real
# TVs plausibly expose the same service. Adding it + a SOAP listener costs
# nothing and, unlike the 36671 binary probe, SOAP-over-HTTP is plaintext —
# if the real app ever calls it, we see the exact bytes with no decryption
# or hardware needed.
HISENSE_LAN_CONTROL_SCPD = """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
<specVersion>
<major>1</major>
<minor>0</minor>
</specVersion>
<actionList>
<action>
<name>LanControlInfo</name>
<argumentList>
<argument>
<name>OpClass</name>
<direction>in</direction>
<relatedStateVariable>A_ARG_TYPE_HisenseLanControlInfo</relatedStateVariable>
</argument>
<argument>
<name>OpType</name>
<direction>in</direction>
<relatedStateVariable>A_ARG_TYPE_HisenseLanControlInfo</relatedStateVariable>
</argument>
<argument>
<name>OpPara</name>
<direction>in</direction>
<relatedStateVariable>A_ARG_TYPE_HisenseLanControlInfo</relatedStateVariable>
</argument>
</argumentList>
</action>
</actionList>
<serviceStateTable>
<stateVariable sendEvents="yes">
<name>A_ARG_TYPE_HisenseLanControlInfo</name>
<dataType>string</dataType>
</stateVariable>
</serviceStateTable>
</scpd>
"""


def renderer_device_desc_xml():
    m = model()
    # Keys + delimiting verified against decompiled RemoteNOW
    # (com.universal.remote.ms.manager.b.c): each value is read from "key=" up to
    # the NEXT "\n". The LISTING gate is: transport_protocol != 0 AND platform != 3
    # AND a non-empty macEthernet+macWifi. brand/vidaa_support are NOT checked
    # (kept for other clients / fidelity). Trailing "\n" is REQUIRED so the last
    # key is still delimited (else its parse throws and defaults to 0 -> dropped).
    desc_lines = "\n".join([
        f"macWifi={m['wifi_mac'].replace(':', '')}",
        f"macEthernet={m['eth_mac'].replace(':', '')}",
        f"transport_protocol={m['transport_protocol']}",
        f"platform=1",
        f"mqttport={MQTT_PORT}",
        f"model_name={m['model_name']}",
        f"tv_version={m['firmware']}",
        f"region=6",
        f"country=US",
        f"language=eng",
        f"voice=0",
        f"brand=his",
        f"vidaa_support=1",
        "",  # trailing empty element -> ensures the blob ends with "\n"
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>{m['friendly_name']}</friendlyName>
    <manufacturer>Hisense</manufacturer>
    <modelDescription>{desc_lines}</modelDescription>
    <modelName>{m['model_name']}</modelName>
    <modelNumber>{m['model_name']}</modelNumber>
    <serialNumber>{m['serial']}</serialNumber>
    <UDN>{m['uuid']}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <controlURL>/upnp/control/minguscmr</controlURL>
        <eventSubURL>/upnp/event/minguscmr</eventSubURL>
        <SCPDURL>/MediaServer/cm.xml</SCPDURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
        <controlURL>/upnp/control/mingusavtr</controlURL>
        <eventSubURL>/upnp/event/mingusavtr</eventSubURL>
        <SCPDURL>/MediaServer/avt.xml</SCPDURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
        <controlURL>/upnp/control/mingusrcr</controlURL>
        <eventSubURL>/upnp/event/mingusrcr</eventSubURL>
        <SCPDURL>/MediaServer/rc.xml</SCPDURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:HisenseLanControl:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:HisenseLanControl</serviceId>
        <controlURL>/upnp/control/hisense_lan_control</controlURL>
        <eventSubURL>/upnp/event/hisense_lan_control</eventSubURL>
        <SCPDURL>/MediaServer/hisense_lan_control.xml</SCPDURL>
      </service>
    </serviceList>
  </device>
</root>"""


class UPnPDescHandler:
    """Tiny HTTP server on UPNP_PORT serving the renderer descriptor +
    the HisenseLanControl SCPD/SOAP endpoint discovered in the real app."""

    def __call__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _xml(self, body):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.rstrip("/")
                if path == "/MediaServer/rendererdevicedesc.xml":
                    add_log("event", f"📡 rendererdevicedesc.xml fetched by "
                            f"{self.client_address[0]} (vidaa_support=1, "
                            f"transport_protocol={model()['transport_protocol']})",
                            "disco")
                    self._xml(renderer_device_desc_xml())
                elif path.startswith("/MediaServer/") and path.endswith(".xml"):
                    # Serve the byte-exact SCPD templates copied from the real
                    # VIDAA app (mediaserver_assets/), so the app's parser sees
                    # exactly what it expects for each service.
                    fname = os.path.basename(path)
                    fpath = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "mediaserver_assets", fname)
                    if os.path.isfile(fpath):
                        add_log("event", f"📡 {fname} fetched by "
                                f"{self.client_address[0]}", "disco")
                        with open(fpath, "rb") as fh:
                            self._xml(fh.read())
                    else:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                soapaction = self.headers.get("SOAPAction", "")
                path = self.path.rstrip("/")
                # This is the payoff: any SOAP call the app makes arrives here in
                # plaintext. Log the full body + action verbatim — that's the
                # ground truth we couldn't get from the 36671 binary probe.
                is_hlc = "hisense_lan_control" in path or "HisenseLanControl" in soapaction
                add_log("event", f"{'🎯' if is_hlc else '📡'} SOAP POST {path} from "
                        f"{self.client_address[0]} SOAPAction={soapaction!r} "
                        f"body={raw[:1200]!r}",
                        "unknown" if is_hlc else "disco")
                if path.startswith("/upnp/control/"):
                    # Parse the invoked action name from SOAPAction ("...#Action")
                    action = soapaction.strip('"').split("#")[-1] if "#" in soapaction else "Response"
                    svc = "urn:schemas-upnp-org:service:HisenseLanControl:1"
                    if "#" in soapaction:
                        svc = soapaction.strip('"').split("#")[0]
                    resp = (f'<?xml version="1.0"?>'
                            f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                            f'<s:Body><u:{action}Response xmlns:u="{svc}">'
                            f'</u:{action}Response></s:Body></s:Envelope>').encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/xml; charset=\"utf-8\"")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

        try:
            srv = ThreadingHTTPServer(("0.0.0.0", UPNP_PORT), H)
            srv.serve_forever(poll_interval=0.5)
        except Exception as e:
            add_log("event", f"⚠ UPnP descriptor server failed on {UPNP_PORT}: {e}",
                    "unknown")


# ── UDP discovery responder on 36671 — Hisense-specific broadcast path ────────
# ══════════════════════════════════════════════════════════════════════════════
#  ▐▌ iOS-ONLY SECTION — 36671 "HS" discovery (EXPERIMENTAL)                  ▐▌
#  ──────────────────────────────────────────────────────────────────────────
#  ⚠ ANDROID DOES NOT USE ANY OF THIS. Android discovers via SSDP(1900) +
#    UPnP descriptor(38400) + MQTT(36669) and is fully working. This block
#    only builds replies to the iPhone's proprietary UDP 36671 "HS" probes,
#    whose exact reply format is undocumented (not in the Android APK / native
#    libs / official docs). We rotate through candidate encodings — ONE per
#    probe, echoing the probe's real seq byte — and log which was sent, so if
#    the iOS app advances (opens MQTT 36669 / fetches XML / stops re-probing)
#    we can identify the winning format. Editing anything here CANNOT affect
#    the Android path.
# ══════════════════════════════════════════════════════════════════════════════
_ios_probe_idx = [0]  # rotating index, one candidate format per probe


def _ios_36671_replies(data, m):
    """Return [(label, bytes)] for one iOS 36671 probe. Rotates the candidate
    format each call so the app sees the whole battery across its probe burst.
    `data` = raw probe (e.g. b'\\x01\\x00\\x01\\x01HS'); byte[1] = seq."""
    name = m["friendly_name"]
    ip = get_lan_ip()
    macc = m["wifi_mac"]                 # colon form aa:bb:...
    machex = macc.replace(":", "")        # 12 hex chars
    mdl = m["model_name"]
    seq = data[1] if len(data) > 1 else 0
    hdr = bytes([0x01, seq, 0x01, 0x01])  # echoed request header
    magic = b"HS"
    try:
        ip_bytes = bytes(int(o) for o in ip.split("."))
    except Exception:
        ip_bytes = b"\x00\x00\x00\x00"
    mac_bytes = bytes(int(machex[i:i+2], 16) for i in range(0, 12, 2))
    js = json.dumps({
        "name": name, "devicename": name, "model": mdl, "brand": "Hisense",
        "mac": macc, "ip": ip, "port": MQTT_PORT, "vidaa_support": 1,
        "transport_protocol": m["transport_protocol"],
    }).encode()

    # Candidate reply builders — reasoned from the probe shape + Hisense
    # conventions (binary header echo, "HS" magic, identity payload). Each is a
    # (label, bytes) the app may or may not accept.
    candidates = [
        ("A:echo",                data),
        ("B:hdr+resp02(b0)",      bytes([0x02, seq, 0x01, 0x01]) + magic),
        ("C:hdr+resp02(b3)",      bytes([0x01, seq, 0x01, 0x02]) + magic),
        ("D:hdr+magic+name0",     hdr + magic + name.encode() + b"\x00"),
        ("E:hdr+magic+mac6+ip4",  hdr + magic + mac_bytes + ip_bytes),
        ("F:hdr+magic+hash",      hdr + magic + f"{machex}#{ip}#{name}".encode() + b"\x00"),
        ("G:hdr+magic+json",      hdr + magic + js),
        ("H:hdr+magic+len+name",  hdr + magic + bytes([len(name)]) + name.encode()),
        ("I:hash-descriptor",     f"{name}#{ip}#{machex}#0#0".encode() + b"\x00"),
        ("J:DISCOVERYACK",        b"DISCOVERYACK#VERSION102#" + ip.encode() + b"#" + name.encode() + b"#" + js),
        ("K:hdr+u16len+json",     hdr + magic + struct.pack(">H", len(js)) + js),
        ("L:plain-json",          js),
        ("M:echo+name0",          data + name.encode() + b"\x00"),
        ("N:hdr+TLV",             hdr + magic
                                  + bytes([0x01, len(name)]) + name.encode()
                                  + bytes([0x02, 4]) + ip_bytes
                                  + bytes([0x03, 6]) + mac_bytes),
    ]
    idx = _ios_probe_idx[0] % len(candidates)
    _ios_probe_idx[0] += 1
    label, payload = candidates[idx]
    add_log("event", f"🍏 iOS 36671 probe seq={seq} → trying candidate "
            f"[{idx+1}/{len(candidates)}] {label}", "disco")
    return [(label, payload)]


def _udp_discovery_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", UDP_DISCOVERY_PORT))
        while True:
            data, addr = sock.recvfrom(4096)
            if not server_state.get("running"):
                continue
            if not data:
                continue
            hexdump = data.hex()
            add_log("event", f"📡 UDP discovery probe from {addr[0]}:{addr[1]} "
                    f"→ {len(data)}B hex={hexdump} raw={data!r}", "disco")
            m = model()

            # Real app's proprietary binary handshake: short payload ending in
            # ASCII "HS" (Hisense magic). Observed: 01 00 01 01 48 53 and
            # 01 01 01 01 48 53 — looks like [flags/seq][...][H][S].
            # No public spec for the expected reply exists; try echoing the
            # same magic back first (common convention for tiny UDP pings),
            # since a JSON-only reply is provably ignored by the real app.
            is_binary_magic = data.endswith(b"HS") and len(data) <= 16

            if is_binary_magic:
                # iOS-only proprietary handshake — delegated to the isolated
                # iOS section below. (Android never sends 36671 "HS" probes.)
                replies_to_try = _ios_36671_replies(data, m)
            else:
                # Unknown / JSON probe — keep the JSON reply for compatibility
                # with non-VIDAA test clients (e.g. our own fidelity script).
                reply = json.dumps({
                    "devicename": m["friendly_name"],
                    "model": m["model_name"],
                    "brand": "Hisense",
                    "mac": m["wifi_mac"],
                    "ip": get_lan_ip(),
                    "port": MQTT_PORT,
                    "vidaa_support": 1,
                    "transport_protocol": m["transport_protocol"],
                }).encode()
                replies_to_try = [("json", reply)]

            for label, payload in replies_to_try:
                try:
                    sock.sendto(payload, addr)
                    add_log("event", f"  ↳ sent {label} reply "
                            f"({len(payload)}B hex={payload.hex()}) to {addr[0]}",
                            "disco")
                except Exception:
                    pass
    except Exception as e:
        add_log("event", f"⚠ UDP discovery listener unavailable on "
                f"{UDP_DISCOVERY_PORT}: {e}", "unknown")


# ── SSDP responder — official app / discovery path ────────────────────────────
def _ssdp_response(lan_ip, st):
    # LOCATION must point at the real-TV descriptor path on 38400
    location = f"http://{lan_ip}:{UPNP_PORT}/MediaServer/rendererdevicedesc.xml"
    m = model()
    return (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
        "EXT:\r\n"
        f"LOCATION: {location}\r\n"
        f"SERVER: Linux/4.9 UPnP/1.0 VIDAA/{m['vidaa_version']}\r\n"
        f"ST: {st}\r\n"
        f"USN: {m['uuid']}::{st}\r\n"
        "\r\n"
    ).encode()


ROOT_ST = "urn:schemas-upnp-org:device:MediaRenderer:1"


def _ssdp_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.bind(("", SSDP_PORT))
        mreq = struct.pack("4sL", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        while True:
            data, addr = sock.recvfrom(4096)
            if not server_state.get("running"):
                continue
            text = data.decode(errors="ignore")
            if "M-SEARCH" not in text:
                continue
            lan_ip = get_lan_ip()
            HLC_ST = "urn:schemas-upnp-org:service:HisenseLanControl:1"
            wanted = [st for st in ("ssdp:all", "upnp:rootdevice", ROOT_ST,
                                    HLC_ST, model()["uuid"]) if st in text]
            if not wanted:
                continue
            add_log("event", f"📡 SSDP M-SEARCH from {addr[0]} "
                    f"(ST: {', '.join(wanted)}) — replying", "disco")
            for st in ([ROOT_ST, "upnp:rootdevice"] if "ssdp:all" in wanted
                       else wanted):
                try:
                    sock.sendto(_ssdp_response(lan_ip, st), addr)
                    time.sleep(0.05)
                except Exception:
                    pass
    except Exception as e:
        add_log("event", f"⚠ SSDP listener unavailable ({e}) — on macOS port 1900 "
                "may be held by mDNSResponder; try sudo", "unknown")


def _ssdp_notifier():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    while True:
        if server_state.get("running"):
            lan_ip = get_lan_ip()
            location = (f"http://{lan_ip}:{UPNP_PORT}"
                        "/MediaServer/rendererdevicedesc.xml")
            m = model()
            for st in ("upnp:rootdevice", ROOT_ST, m["uuid"]):
                packet = (
                    "NOTIFY * HTTP/1.1\r\n"
                    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                    "CACHE-CONTROL: max-age=1800\r\n"
                    f"LOCATION: {location}\r\n"
                    f"NT: {st}\r\n"
                    "NTS: ssdp:alive\r\n"
                    f"SERVER: Linux/4.9 UPnP/1.0 VIDAA/{m['vidaa_version']}\r\n"
                    f"USN: {m['uuid']}::{st}\r\n"
                    "\r\n"
                ).encode()
                try:
                    sock.sendto(packet, (SSDP_ADDR, SSDP_PORT))
                    time.sleep(0.1)
                except Exception:
                    pass
        time.sleep(15)


threading.Thread(target=_ssdp_listener, daemon=True).start()
threading.Thread(target=_ssdp_notifier, daemon=True).start()
threading.Thread(target=_udp_discovery_listener, daemon=True).start()
threading.Thread(target=UPnPDescHandler(), daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  Flask control panel
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hisense VIDAA Remote Tester</title>
<style>
  :root {
    --bg:#0d0f14; --surface:#161a22; --surface2:#1e2430; --border:#2a3242;
    --text:#e6e9ef; --muted:#8b93a7; --accent:#00b3a4; --accent2:#3b82f6;
    --in:#3b82f6; --out:#00b3a4; --evt:#a78bfa; --warn:#f59e0b; --disco:#ec4899;
    --ok:#22c55e; --bad:#ef4444;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{padding:16px 24px;background:var(--surface);border-bottom:1px solid var(--border);
    display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  h1{font-size:17px;margin:0;font-weight:650}
  .badge{font-size:12px;color:var(--muted)}
  .wrap{display:grid;grid-template-columns:340px 1fr;gap:16px;padding:16px 24px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
    padding:16px;margin-bottom:16px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;
    color:var(--muted);margin:0 0 12px}
  button{font:inherit;font-weight:600;border:0;border-radius:8px;padding:10px 16px;
    cursor:pointer;color:#fff}
  .start{background:var(--accent)} .stop{background:var(--bad)}
  .ghost{background:var(--surface2);color:var(--text);border:1px solid var(--border)}
  button:disabled{opacity:.4;cursor:not-allowed}
  select{font:inherit;background:var(--surface2);color:var(--text);
    border:1px solid var(--border);border-radius:8px;padding:8px;width:100%}
  label{display:block;font-size:12px;color:var(--muted);margin:12px 0 4px}
  .row{display:flex;gap:8px;align-items:center}
  .pin{font-size:44px;font-weight:750;letter-spacing:.14em;text-align:center;
    color:var(--accent);padding:14px;background:var(--surface2);border-radius:10px;
    font-variant-numeric:tabular-nums}
  .pin.idle{font-size:15px;color:var(--muted);font-weight:500;letter-spacing:0}
  .kv{display:flex;justify-content:space-between;padding:5px 0;
    border-bottom:1px solid var(--border)}
  .kv:last-child{border:0}
  .kv .k{color:var(--muted)} .kv .v{font-weight:600}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
  .stats{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
  .stat{background:var(--surface2);border-radius:8px;padding:10px;text-align:center}
  .stat .n{font-size:22px;font-weight:700} .stat .l{font-size:11px;color:var(--muted)}
  #log{background:#0a0c10;border:1px solid var(--border);border-radius:10px;
    height:calc(100vh - 220px);overflow-y:auto;padding:10px;font:12.5px/1.6
    "SF Mono",Menlo,Consolas,monospace}
  .le{padding:2px 6px;border-radius:5px;margin-bottom:2px;white-space:pre-wrap;
    word-break:break-word}
  .le .t{color:var(--muted);margin-right:8px}
  .k-pair{color:var(--warn)} .k-cmd .b{color:var(--out)}
  .in{border-left:3px solid var(--in);padding-left:8px}
  .out{border-left:3px solid var(--out);padding-left:8px}
  .event{border-left:3px solid var(--evt);padding-left:8px;color:#c9cede}
  .k-unknown{color:var(--warn)} .k-disco{color:var(--disco)}
</style>
</head>
<body>
<header>
  <h1>📺 Hisense VIDAA Remote Tester</h1>
  <span class="badge" id="hdr">Mock TV of a real Hisense — for your app + official RemoteNOW</span>
  <span class="badge" style="margin-left:auto"><span class="dot" id="sdot"
    style="background:var(--bad)"></span><span id="sstate">stopped</span></span>
</header>
<div class="wrap">
  <div>
    <div class="card">
      <h2>Mock TV</h2>
      <div class="row" style="margin-bottom:6px">
        <button class="start" id="btnStart" onclick="start()">▶ Start</button>
        <button class="stop ghost" id="btnStop" onclick="stop()" disabled>■ Stop</button>
        <button class="ghost" onclick="clearLog()">Clear log</button>
      </div>
      <label>Emulated model</label>
      <select id="modelSel" onchange="setModel()">
        <option value="55A6K">Hisense 55A6K — VIDAA U7 (2023)</option>
        <option value="65U7QF">Hisense 65U7QF — VIDAA U4 (2020)</option>
      </select>
      <label>Pairing generation</label>
      <select id="pairSel" onchange="setPairing()">
        <option value="modern">Modern — PIN + token issuance (mTLS)</option>
        <option value="legacy">Legacy — static creds, no PIN</option>
      </select>
      <div class="badge" style="margin-top:10px" id="conn"></div>
    </div>

    <div class="card">
      <h2>TV screen — pairing PIN</h2>
      <div class="pin idle" id="pin">no pairing in progress</div>
    </div>

    <div class="card">
      <h2>Virtual TV state</h2>
      <div class="kv"><span class="k">Power</span><span class="v" id="tvPower">—</span></div>
      <div class="kv"><span class="k">Volume</span><span class="v" id="tvVol">—</span></div>
      <div class="kv"><span class="k">Muted</span><span class="v" id="tvMute">—</span></div>
      <div class="kv"><span class="k">Source</span><span class="v" id="tvSrc">—</span></div>
      <div class="kv"><span class="k">App</span><span class="v" id="tvApp">—</span></div>
    </div>

    <div class="card">
      <h2>Session</h2>
      <div class="stats">
        <div class="stat"><div class="n" id="stConn">0</div><div class="l">connects</div></div>
        <div class="stat"><div class="n" id="stPair">0</div><div class="l">paired</div></div>
        <div class="stat"><div class="n" id="stCmd">0</div><div class="l">commands</div></div>
        <div class="stat"><div class="n" id="stUnk">0</div><div class="l">unknown</div></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin:0">
    <h2>Wire log — every MQTT packet</h2>
    <div id="log"></div>
  </div>
</div>
<script>
let since=0, running=false;
async function start(){await fetch('/api/start',{method:'POST'});poll(true);}
async function stop(){await fetch('/api/stop',{method:'POST'});poll(true);}
async function clearLog(){await fetch('/api/clear-log',{method:'POST'});
  document.getElementById('log').innerHTML='';since=0;}
async function setModel(){await fetch('/api/model',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({model:document.getElementById('modelSel').value})});poll(true);}
async function setPairing(){await fetch('/api/pairing',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({mode:document.getElementById('pairSel').value})});}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function poll(reset){
  if(reset)since=0;
  const r=await fetch('/api/state?since='+since);const s=await r.json();
  running=s.running;
  document.getElementById('sdot').style.background=running?'var(--ok)':'var(--bad)';
  document.getElementById('sstate').textContent=running?'running':'stopped';
  document.getElementById('btnStart').disabled=running;
  document.getElementById('btnStop').disabled=!running;
  document.getElementById('modelSel').disabled=running;
  document.getElementById('modelSel').value=s.model_key;
  document.getElementById('pairSel').value=s.pairing_mode;
  document.getElementById('conn').textContent=
    s.model.friendly_name+' · mqtts '+s.lan_ip+':'+s.mqtt_port+' · CN='+s.model.tls_cn;
  const pin=document.getElementById('pin');
  if(s.active_pin){pin.className='pin';pin.textContent=s.active_pin;}
  else{pin.className='pin idle';pin.textContent='no pairing in progress';}
  document.getElementById('tvPower').textContent=s.tv.power?'ON':'OFF (standby)';
  document.getElementById('tvVol').textContent=s.tv.volume;
  document.getElementById('tvMute').textContent=s.tv.muted?'yes':'no';
  document.getElementById('tvSrc').textContent=s.tv.source;
  document.getElementById('tvApp').textContent=s.tv.app||'— (home)';
  document.getElementById('stConn').textContent=s.stats.connects;
  document.getElementById('stPair').textContent=s.stats.paired;
  document.getElementById('stCmd').textContent=s.stats.commands;
  document.getElementById('stUnk').textContent=s.stats.unknown;
  if(reset)document.getElementById('log').innerHTML='';
  const log=document.getElementById('log');
  for(const e of s.new_entries){
    const d=document.createElement('div');
    d.className='le '+e.direction+' k-'+e.kind;
    d.innerHTML='<span class="t">'+e.time+'</span>'+esc(e.text);
    log.appendChild(d);
  }
  if(s.new_entries.length){since+=s.new_entries.length;log.scrollTop=log.scrollHeight;}
}
setInterval(()=>poll(false),700);poll(true);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/start", methods=["POST"])
def api_start():
    if server_state["running"]:
        return jsonify({"ok": True})
    stop_event.clear()
    server_state["running"] = True
    server_state["stats"] = {"paired": 0, "commands": 0, "unknown": 0, "connects": 0}
    threading.Thread(target=run_mqtt_server, daemon=True).start()
    time.sleep(0.5)
    if not server_state["running"]:
        return jsonify({"ok": False, "error": "MQTT server failed (see log)"})
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    server_state["running"] = False
    stop_event.set()
    srv = mqtt_srv.get("server")
    if srv:
        try:
            srv.close()
        except Exception:
            pass
    with _conn_lock:
        for c in list(_connections):
            c.alive = False
            try:
                c.sock.close()
            except Exception:
                pass
    server_state["active_pin"] = None
    server_state["pending_auth"].clear()
    return jsonify({"ok": True})


@app.route("/api/model", methods=["POST"])
def api_model():
    if server_state["running"]:
        return jsonify({"ok": False, "error": "stop the mock before switching model"})
    key = (request.get_json(silent=True) or {}).get("model")
    if key in MODELS:
        server_state["model_key"] = key
        server_state["pairing_mode"] = MODELS[key]["default_pairing"]
        add_log("event", f"Model set → {MODELS[key]['friendly_name']} "
                f"(default {server_state['pairing_mode']} pairing)", "event")
    return jsonify({"ok": True})


@app.route("/api/pairing", methods=["POST"])
def api_pairing():
    mode = (request.get_json(silent=True) or {}).get("mode")
    if mode in ("modern", "legacy"):
        server_state["pairing_mode"] = mode
        add_log("event", f"Pairing generation → {mode}", "event")
    return jsonify({"ok": True})


@app.route("/api/clear-log", methods=["POST"])
def api_clear_log():
    server_state["log"].clear()
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    since = int(request.args.get("since", 0))
    tv = server_state["tv"]
    return jsonify({
        "running": server_state["running"],
        "new_entries": server_state["log"][since:],
        "model_key": server_state["model_key"],
        "pairing_mode": server_state["pairing_mode"],
        "model": model(),
        "lan_ip": get_lan_ip(),
        "mqtt_port": MQTT_PORT,
        "active_pin": server_state["active_pin"],
        "stats": server_state["stats"],
        "tv": {
            "power": tv["power"], "volume": tv["volume"], "muted": tv["muted"],
            "source": SOURCES.get(tv["sourceid"], tv["sourceid"]),
            "app": tv["app"],
        },
    })


@app.route("/ssdp/device-desc.xml")
def ssdp_device_desc():
    add_log("event", f"📡 UPnP device description fetched by {request.remote_addr}",
            "disco")
    lan_ip = get_lan_ip()
    m = model()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://{lan_ip}:{PANEL_PORT}</URLBase>
  <device>
    <deviceType>{ROOT_ST}</deviceType>
    <friendlyName>{m['friendly_name']}</friendlyName>
    <manufacturer>Hisense</manufacturer>
    <manufacturerURL>http://www.hisense.com</manufacturerURL>
    <modelDescription>Hisense VIDAA Smart TV</modelDescription>
    <modelName>{m['model_name']}</modelName>
    <modelNumber>{m['model_name']}</modelNumber>
    <modelURL>http://www.hisense.com</modelURL>
    <serialNumber>{m['serial']}</serialNumber>
    <UDN>{m['uuid']}</UDN>
  </device>
</root>"""
    return Response(xml, mimetype="text/xml")


if __name__ == "__main__":
    print("=" * 62)
    print("  Hisense VIDAA Remote Tester")
    m = model()
    print(f"  Emulating: {m['friendly_name']} ({m['vidaa_version']}, "
          f"FW {m['firmware']})")
    print(f"  Open in browser: http://127.0.0.1:{PANEL_PORT}")
    print(f"  MQTT broker: mqtts://0.0.0.0:{MQTT_PORT}  (start it from the panel)")
    print("  Stop: Ctrl+C")
    print("=" * 62)
    app.run(host="0.0.0.0", port=PANEL_PORT, debug=False, threaded=True)
