#!/usr/bin/env python3
"""
Hisense VIDAA mock — headless fidelity self-test.
Pairs and exercises every command against a RUNNING mock, so you can confirm the
mock behaves like a real TV independent of any app. Reads the pairing PIN from the
panel API (like a human reading the TV screen).

Usage:
  1. python3 hisense_tv_tester.py           # then Start Mock TV in the browser
  2. python3 hisense_fidelity_test.py       # (add --host <lan_ip> for another box)

Hand-rolls a tiny MQTT 3.1.1 client over TLS — no paho/amqtt needed.
"""

import argparse
import json
import socket
import ssl
import struct
import threading
import time
import urllib.request
import uuid

CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 12, 13, 14

received = []            # (topic, payload) pushed by the reader thread
_recv_lock = threading.Lock()


def _rl(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def _str(s):
    b = s.encode()
    return struct.pack(">H", len(b)) + b


class MiniMQTT:
    def __init__(self, host, port, client_id):
        self.host, self.port, self.client_id = host, port, client_id
        self.sock = None
        self.pid = 0

    def connect(self, user="hisenseservice", pw="multimqttservice"):
        raw = socket.create_connection((self.host, self.port), timeout=10)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE      # self-signed, like real clients
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        vh = _str("MQTT") + bytes([4]) + bytes([0xC2]) + struct.pack(">H", 60)
        payload = _str(self.client_id) + _str(user) + _str(pw)
        self._send(CONNECT, vh + payload)
        ptype, _ = self._read_packet()
        assert ptype == CONNACK, f"expected CONNACK, got {ptype}"
        threading.Thread(target=self._reader, daemon=True).start()
        print(f"  ✓ TLS + MQTT CONNECT ok (clientid={self.client_id})")

    def subscribe(self, topic):
        self.pid += 1
        vh = struct.pack(">H", self.pid) + _str(topic) + bytes([0])
        self._send(SUBSCRIBE, vh, flags=0x02)

    def publish(self, topic, payload):
        body = _str(topic) + (payload.encode() if isinstance(payload, str)
                              else json.dumps(payload).encode())
        self._send(PUBLISH, body)

    def _send(self, ptype, body, flags=0):
        self.sock.sendall(bytes([(ptype << 4) | flags]) + _rl(len(body)) + body)

    def _read_n(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("closed")
            buf += c
        return buf

    def _read_packet(self):
        b1 = self._read_n(1)[0]
        mult, val = 1, 0
        while True:
            b = self._read_n(1)[0]
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        body = self._read_n(val) if val else b""
        return b1 >> 4, body

    def _reader(self):
        try:
            while True:
                ptype, body = self._read_packet()
                if ptype == PUBLISH:
                    ln = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2 + ln].decode()
                    payload = body[2 + ln:].decode("utf-8", "ignore")
                    with _recv_lock:
                        received.append((topic, payload))
        except Exception:
            pass


def panel_state(base):
    with urllib.request.urlopen(base + "/api/state", timeout=5) as r:
        return json.loads(r.read())


def wait_for(pred, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        with _recv_lock:
            for t, p in received:
                if pred(t, p):
                    return t, p
        time.sleep(0.15)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=36669)
    ap.add_argument("--panel-port", type=int, default=5000)
    args = ap.parse_args()
    base = f"http://{args.host}:{args.panel_port}"

    st = panel_state(base)
    if not st["running"]:
        print("✗ Mock TV is not running. Start it in the browser panel first.")
        return
    mode = st["pairing_mode"]
    print(f"Mock: {st['model']['friendly_name']} ({st['model']['vidaa_version']}) "
          f"— {mode} pairing\n")

    cid = f"{uuid.uuid4().hex}$his$test_vidaacommon_001"
    c = MiniMQTT(args.host, args.mqtt_port, cid)
    print("1. Connecting…")
    c.connect()

    c.subscribe(f"/remoteapp/mobile/{cid}/#")
    time.sleep(0.3)

    print("2. Pairing…")
    c.publish(f"/remoteapp/tv/ui_service/{cid}/actions/vidaa_app_connect", "")
    if mode == "modern":
        time.sleep(0.8)
        pin = panel_state(base)["active_pin"]
        print(f"  ↳ PIN from TV screen: {pin}")
        c.publish(f"/remoteapp/tv/ui_service/{cid}/actions/authenticationcode",
                  {"authNum": pin})
        tok = wait_for(lambda t, p: "tokenissuance" in t)
        if tok:
            print(f"  ✓ token issued: {json.loads(tok[1])['accessToken'][:16]}…")
        else:
            print("  ✗ no tokenissuance received")
    else:
        auth = wait_for(lambda t, p: "authentication" in t)
        print(f"  ✓ legacy auth: {auth[1] if auth else 'no response'}")

    def cmd(label, service, action, payload, expect_sub=None):
        with _recv_lock:
            received.clear()
        c.publish(f"/remoteapp/tv/{service}/{cid}/actions/{action}", payload)
        if expect_sub:
            r = wait_for(lambda t, p: expect_sub in t)
            print(f"  {'✓' if r else '✗'} {label}: "
                  + (r[1][:90] if r else "no response"))
        else:
            time.sleep(0.25)
            print(f"  ✓ {label} sent")

    print("3. Commands…")
    cmd("getvolume", "platform_service", "getvolume", "", "platform_service/data/getvolume")
    cmd("sourcelist", "ui_service", "sourcelist", "", "ui_service/data/sourcelist")
    cmd("applist", "ui_service", "applist", "", "ui_service/data/applist")
    cmd("changevolume 42", "platform_service", "changevolume", "42")
    cmd("sendkey VOLUMEUP", "remote_service", "sendkey", "KEY_VOLUMEUP")
    cmd("sendkey MUTE", "remote_service", "sendkey", "KEY_MUTE")
    cmd("changesource HDMI1", "ui_service", "changesource", {"sourceid": "3"},
        "ui_service/data/state")
    cmd("launchapp Netflix", "ui_service", "launchapp",
        {"name": "Netflix", "url": "netflix", "urlType": 37, "storeType": 0},
        "ui_service/data/state")
    cmd("gettvstate", "ui_service", "gettvstate", "", "ui_service/data/state")

    time.sleep(0.4)
    fin = panel_state(base)
    print("\n4. Final TV state (from panel):")
    print(f"   power={fin['tv']['power']} volume={fin['tv']['volume']} "
          f"muted={fin['tv']['muted']} source={fin['tv']['source']} "
          f"app={fin['tv']['app']}")
    print(f"   stats: {fin['stats']}")
    print("\n✓ fidelity test complete")


if __name__ == "__main__":
    main()
