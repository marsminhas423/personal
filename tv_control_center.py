#!/usr/bin/env python3
"""
Fake Smart-TV Control Center
============================
One program that:
  1. Advertises fake TVs on the network (mDNS/Cast/Android-TV + SSDP/DIAL/Roku)
     so your phone's universal remote app can discover them.
  2. Serves a WEB CONTROL PANEL in your browser where you can:
       * turn each fake TV on/off individually,
       * see live which remote app discovered / connected to which TV,
     instead of squinting at the terminal.

RUN IT
------
    python -m pip install zeroconf      # needed for mDNS/Cast/Android-TV
    python tv_control_center.py         # run as Administrator on Windows

Then open the control panel it prints, e.g.:  http://localhost:8008/

REQUIREMENTS THAT ACTUALLY MATTER
---------------------------------
  * The PC running this must be on the SAME Wi-Fi as the phone.
  * mDNS/SSDP are link-local: they don't cross guest Wi-Fi, VPNs, or
    routers with "client isolation" (common on hotspots / public Wi-Fi).
  * First run, allow Python through the Windows Firewall on PRIVATE networks.
"""

import argparse
import http.server
import json
import socket
import socketserver
import struct
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

# ----------------------------------------------------------------------------
# Fake TVs. `enabled` can be toggled live from the web panel.
# ----------------------------------------------------------------------------
DEVICES = [
    dict(key="samsung", name="Samsung TV (Living Room)", brand="Samsung",
         model="QN90B Neo QLED", color="#1428A0", enabled=True,
         protocols=["dial", "rootdevice", "samsung", "airplay", "samsungmsf"]),
    dict(key="lg", name="LG webOS TV (Bedroom)", brand="LG Electronics",
         model="OLED C3", color="#A50034", enabled=True,
         protocols=["dial", "rootdevice", "lg", "airplay"]),
    dict(key="sony", name="Sony BRAVIA (Study)", brand="Sony",
         model="BRAVIA XR A80L", color="#0a84ff", enabled=True,
         protocols=["dial", "rootdevice", "googlecast", "androidtv"]),
    dict(key="tcl", name="TCL Google TV (Playroom)", brand="TCL",
         model="C845 QD-Mini LED", color="#00b3b3", enabled=True,
         protocols=["dial", "rootdevice", "googlecast", "androidtv"]),
    dict(key="shield", name="NVIDIA Shield (Office)", brand="NVIDIA",
         model="SHIELD Android TV Pro", color="#76b900", enabled=True,
         protocols=["googlecast", "androidtv"]),
    dict(key="roku", name="Roku (Guest Room)", brand="Roku",
         model="Roku Ultra 4802", color="#6f1ab1", enabled=True,
         protocols=["dial", "rootdevice", "roku"]),
]

HTTP_PORT = 8008
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SERVER_ID = "Linux/6.0 UPnP/1.1 FakeTV/1.0"

ST = {
    "rootdevice": "upnp:rootdevice",
    "dial":       "urn:dial-multiscreen-org:service:dial:1",
    "samsung":    "urn:samsung.com:device:RemoteControlReceiver:1",
    "lg":         "urn:lge-com:service:webos-second-screen:1",
    "roku":       "roku:ecp",
}
MDNS = {
    "googlecast": ("_googlecast._tcp.local.", 8009),
    "androidtv":  ("_androidtvremote2._tcp.local.", 6466),
    "airplay":    ("_airplay._tcp.local.", 7000),
    "samsungmsf": ("_samsungmsf._tcp.local.", 8001),
}
PROTO_LABEL = {
    "dial": "DIAL", "rootdevice": "UPnP", "samsung": "Samsung",
    "lg": "LG webOS", "roku": "Roku ECP", "googlecast": "Cast",
    "androidtv": "Android TV", "airplay": "AirPlay", "samsungmsf": "Samsung MSF",
}

# ----------------------------------------------------------------------------
# Shared live state (guarded by LOCK).
# ----------------------------------------------------------------------------
LOCK = threading.Lock()
EVENTS = deque(maxlen=250)
STATE = {"ip": "127.0.0.1", "mdns_on": False, "ssdp_on": False, "warning": ""}
_event_seq = 0


def dev(key):
    return next((d for d in DEVICES if d["key"] == key), None)


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def log(kind, msg, ip=None, key=None):
    """Add a live event; coalesce identical repeats within 3s to reduce noise."""
    global _event_seq
    with LOCK:
        if EVENTS:
            last = EVENTS[0]
            if (last["kind"] == kind and last["msg"] == msg
                    and last["ip"] == ip and time.time() - last["ts"] < 3):
                last["count"] += 1
                last["time"] = now_str()
                return
        _event_seq += 1
        EVENTS.appendleft(dict(id=_event_seq, kind=kind, msg=msg, ip=ip,
                               key=key, time=now_str(), ts=time.time(), count=1))
        if key:
            d = dev(key)
            if d is not None:
                d["last_ip"] = ip
                d["last_kind"] = kind
                d["last_time"] = now_str()
                if kind in ("connect", "control"):
                    d["hits"] = d.get("hits", 0) + 1


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def httpdate():
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def init_ids():
    for d in DEVICES:
        d["udn"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, "faketv-" + d["key"]))
        d["serial"] = d["udn"].replace("-", "")[:12].upper()
        d["host"] = d["key"] + "-faketv"
        d.setdefault("hits", 0)
        d.setdefault("last_ip", None)


# ============================================================================
# mDNS manager (zeroconf) — supports live register / unregister per device.
# ============================================================================
class MDNSManager:
    def __init__(self, ip):
        self.ip = ip
        self.zc = None
        self.infos = {}   # key -> [ServiceInfo]
        try:
            from zeroconf import Zeroconf
            self.zc = Zeroconf()
        except ImportError:
            self.available = False
            return
        self.available = True
        for d in DEVICES:
            if d["enabled"]:
                self.register(d["key"])

    def _make_infos(self, d):
        from zeroconf import ServiceInfo
        out = []
        for proto in d["protocols"]:
            if proto not in MDNS:
                continue
            stype, port = MDNS[proto]
            iname = f"{d['name'].replace('.', ' ')}.{stype}"
            if proto == "googlecast":
                props = {"id": d["udn"].replace("-", ""), "md": d["model"],
                         "fn": d["name"], "ca": "4101", "st": "0", "rs": "",
                         "ve": "05", "ic": "/setup/icon.png"}
            elif proto == "androidtv":
                props = {"bt": d["serial"], "bs": d["serial"]}
            elif proto == "airplay":
                props = {"deviceid": d["serial"], "model": d["model"],
                         "features": "0x5A7FFFF7,0x1E", "srcvers": "220.68"}
            else:
                props = {"id": d["udn"], "fn": d["name"], "md": d["model"]}
            out.append(ServiceInfo(
                stype, iname, addresses=[socket.inet_aton(self.ip)], port=port,
                properties=props, server=f"{d['host']}.local."))
        return out

    def register(self, key):
        if not self.zc or key in self.infos:
            return
        d = dev(key)
        infos = self._make_infos(d)
        for info in infos:
            try:
                # strict=False: real Android TVs use "_androidtvremote2" (16
                # chars), longer than the RFC limit zeroconf enforces by default.
                self.zc.register_service(info, allow_name_change=True,
                                         strict=False)
            except Exception as e:
                log("error", f"mDNS register failed ({key}): {e}")
        self.infos[key] = infos

    def unregister(self, key):
        if not self.zc or key not in self.infos:
            return
        for info in self.infos.pop(key):
            try:
                self.zc.unregister_service(info)
            except Exception:
                pass

    def count(self):
        return sum(len(v) for v in self.infos.values())

    def close(self):
        if self.zc:
            try:
                self.zc.close()
            except Exception:
                pass


# ============================================================================
# SSDP responder — honours per-device enabled flag; logs who searches.
# ============================================================================
class SSDP(threading.Thread):
    daemon = True

    def __init__(self, ip):
        super().__init__()
        self.ip = ip
        self.running = True
        self.sock = None

    def _services(self):
        """Live list of (dev, st, usn, location) for enabled devices only."""
        out = []
        for d in DEVICES:
            if not d["enabled"]:
                continue
            for proto in d["protocols"]:
                if proto not in ST:
                    continue
                st = ST[proto]
                if proto == "roku":
                    usn = f"uuid:roku:ecp:{d['serial']}"
                    loc = f"http://{self.ip}:{HTTP_PORT}/"
                else:
                    usn = f"uuid:{d['udn']}::{st}"
                    loc = f"http://{self.ip}:{HTTP_PORT}/dd/{d['key']}.xml"
                out.append((d, st, usn, loc))
            out.append((d, f"uuid:{d['udn']}", f"uuid:{d['udn']}",
                        f"http://{self.ip}:{HTTP_PORT}/dd/{d['key']}.xml"))
        return out

    def _response(self, st, usn, loc):
        return ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n"
                f"DATE: {httpdate()}\r\nEXT:\r\nLOCATION: {loc}\r\n"
                f"SERVER: {SERVER_ID}\r\nST: {st}\r\nUSN: {usn}\r\n"
                "BOOTID.UPNP.ORG: 1\r\nCONFIGID.UPNP.ORG: 1\r\n\r\n").encode()

    def _notify(self, st, usn, loc, alive=True):
        nts = "ssdp:alive" if alive else "ssdp:byebye"
        return ("NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\nCACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {loc}\r\nNT: {st}\r\nNTS: {nts}\r\n"
                f"SERVER: {SERVER_ID}\r\nUSN: {usn}\r\nBOOTID.UPNP.ORG: 1\r\n\r\n"
                ).encode()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("", SSDP_PORT))
        except OSError as e:
            log("error", f"SSDP could not bind :1900 ({e}). Try --no-ssdp or "
                         "run as Administrator.")
            STATE["ssdp_on"] = False
            return
        mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR),
                           socket.inet_aton(self.ip))
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            log("error", f"SSDP multicast join failed: {e}")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(self.ip))
        except OSError:
            pass
        sock.settimeout(1.0)
        self.sock = sock
        STATE["ssdp_on"] = True

        self._announce()
        last = time.time()
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except OSError:
                break
            if data and data.startswith(b"M-SEARCH"):
                text = data.decode("utf-8", "ignore")
                if "ssdp:discover" in text:
                    self._handle_search(text, addr)
            if time.time() - last > 30:
                self._announce()
                last = time.time()

        for d, st, usn, loc in self._services():
            try:
                sock.sendto(self._notify(st, usn, loc, alive=False),
                            (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass
        sock.close()

    def _handle_search(self, text, addr):
        want, ua = "ssdp:all", ""
        for line in text.split("\r\n"):
            u = line.upper()
            if u.startswith("ST:"):
                want = line.split(":", 1)[1].strip()
            elif u.startswith("USER-AGENT:"):
                ua = line.split(":", 1)[1].strip()
        matched = []
        for d, st, usn, loc in self._services():
            if want == "ssdp:all" or want == st:
                try:
                    self.sock.sendto(self._response(st, usn, loc), addr)
                    matched.append(d["key"])
                except OSError:
                    pass
        if matched:
            who = f" [{ua}]" if ua else ""
            uniq = sorted(set(matched))
            log("search", f"SSDP scan{who} → answered {', '.join(uniq)}",
                ip=addr[0], key=(uniq[0] if len(uniq) == 1 else None))

    def _announce(self):
        if not self.sock:
            return
        for d, st, usn, loc in self._services():
            try:
                self.sock.sendto(self._notify(st, usn, loc),
                                 (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass

    def stop(self):
        self.running = False


# ============================================================================
# HTTP: control panel + JSON API + device descriptions (for real remotes).
# ============================================================================
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeTV/1.0"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj):
        self._send(json.dumps(obj), "application/json")

    def do_GET(self):
        p = urlparse(self.path)
        path, q = p.path, parse_qs(p.query)
        ip = self.server.adv_ip
        ua = self.headers.get("User-Agent", "")
        client = self.client_address[0]

        if path == "/" or path == "/index.html":
            self._send(DASHBOARD_HTML, "text/html; charset=utf-8")
            return

        if path == "/api/state":
            self._json(self._state())
            return

        # UPnP / DIAL description — a real remote app fetching this = discovery.
        if path.startswith("/dd/") and path.endswith(".xml"):
            key = path[4:-4]
            d = dev(key)
            if d and d["enabled"]:
                who = f" [{ua.split('/')[0]}]" if ua else ""
                log("connect", f"Opened {d['name']} description{who}",
                    ip=client, key=key)
                xml = (f'<?xml version="1.0" encoding="utf-8"?>\n'
                       '<root xmlns="urn:schemas-upnp-org:device-1-0">'
                       '<specVersion><major>1</major><minor>0</minor>'
                       '</specVersion><device>'
                       '<deviceType>urn:dial-multiscreen-org:device:dial:1'
                       '</deviceType>'
                       f'<friendlyName>{d["name"]}</friendlyName>'
                       f'<manufacturer>{d["brand"]}</manufacturer>'
                       f'<modelName>{d["model"]}</modelName>'
                       f'<UDN>uuid:{d["udn"]}</UDN></device></root>')
                self._send(xml, "application/xml",
                           {"Application-URL": f"http://{ip}:{HTTP_PORT}/apps/"})
                return
            self.send_response(404)
            self.end_headers()
            return

        if path == "/query/device-info":
            d = dev("roku")
            if d and d["enabled"]:
                log("connect", f"Roku info read [{ua.split('/')[0] or '?'}]",
                    ip=client, key="roku")
                xml = (f"<device-info><udn>{d['udn']}</udn>"
                       f"<serial-number>{d['serial']}</serial-number>"
                       f"<device-id>{d['serial']}</device-id>"
                       f"<vendor-name>Roku</vendor-name>"
                       f"<model-name>{d['model']}</model-name>"
                       f"<friendly-device-name>{d['name']}</friendly-device-name>"
                       f"<power-mode>PowerOn</power-mode></device-info>")
                self._send(xml, "application/xml")
                return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        path, q = p.path, parse_qs(p.query)
        client = self.client_address[0]

        if path == "/api/toggle":
            key = q.get("key", [""])[0]
            d = dev(key)
            if d:
                d["enabled"] = not d["enabled"]
                mdns = getattr(self.server, "mdns", None)
                if mdns and mdns.available:
                    (mdns.register if d["enabled"] else mdns.unregister)(key)
                log("toggle", f"{d['name']} turned "
                    f"{'ON' if d['enabled'] else 'OFF'}", key=key)
            self._json(self._state())
            return

        if path == "/api/all":
            on = q.get("on", ["1"])[0] == "1"
            mdns = getattr(self.server, "mdns", None)
            for d in DEVICES:
                d["enabled"] = on
                if mdns and mdns.available:
                    (mdns.register if on else mdns.unregister)(d["key"])
            log("toggle", f"All devices turned {'ON' if on else 'OFF'}")
            self._json(self._state())
            return

        if path == "/api/clear":
            with LOCK:
                EVENTS.clear()
            self._json(self._state())
            return

        # Roku ECP keypress from a real remote = control interaction.
        if path.startswith("/keypress/"):
            btn = path.rsplit("/", 1)[-1]
            log("control", f"Roku key '{btn}' pressed", ip=client, key="roku")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def _state(self):
        with LOCK:
            devs = [dict(key=d["key"], name=d["name"], brand=d["brand"],
                         model=d["model"], color=d["color"],
                         enabled=d["enabled"],
                         protocols=[PROTO_LABEL.get(x, x) for x in d["protocols"]],
                         hits=d.get("hits", 0), last_ip=d.get("last_ip"),
                         last_time=d.get("last_time"),
                         last_kind=d.get("last_kind"))
                    for d in DEVICES]
            evs = list(EVENTS)[:60]
        return dict(ip=STATE["ip"], mdns_on=STATE["mdns_on"],
                    ssdp_on=STATE["ssdp_on"], warning=STATE["warning"],
                    http_port=HTTP_PORT, devices=devs, events=evs)


class HTTPServerThread(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ============================================================================
def main():
    global HTTP_PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", help="advertised LAN IP (default: auto)")
    ap.add_argument("--no-mdns", action="store_true")
    ap.add_argument("--no-ssdp", action="store_true")
    ap.add_argument("--port", type=int, default=HTTP_PORT)
    args = ap.parse_args()
    HTTP_PORT = args.port
    ip = args.ip or get_local_ip()
    STATE["ip"] = ip
    init_ids()

    if ip.startswith(("127.", "192.0.2.")):
        STATE["warning"] = ("Advertised IP looks like loopback/test — a phone "
                            "won't reach it. Run on the same Wi-Fi and/or pass --ip.")

    httpd = HTTPServerThread(("", HTTP_PORT), Handler)
    httpd.adv_ip = ip
    httpd.mdns = None   # set before serving so early requests don't race
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    ssdp = None
    if not args.no_ssdp:
        ssdp = SSDP(ip)
        ssdp.start()

    mdns = None
    if not args.no_mdns:
        mdns = MDNSManager(ip)
        STATE["mdns_on"] = bool(mdns and mdns.available)
        if not STATE["mdns_on"]:
            STATE["warning"] = (STATE["warning"] + "  ").strip() + \
                "  mDNS/Cast/Android-TV OFF — run:  python -m pip install zeroconf"
    httpd.mdns = mdns

    time.sleep(0.5)
    bar = "=" * 60
    print(bar)
    print("  Fake Smart-TV Control Center")
    print(bar)
    print(f"  Advertised to phones on : http://{ip}:{HTTP_PORT}")
    print(f"  OPEN THE CONTROL PANEL  : http://localhost:{HTTP_PORT}/")
    print(bar)
    print(f"  mDNS/Cast/Android-TV : {'ON' if STATE['mdns_on'] else 'OFF (pip install zeroconf)'}")
    print(f"  SSDP/DIAL/Roku       : {'ON' if not args.no_ssdp else 'disabled'}")
    if STATE["warning"]:
        print("  ! " + STATE["warning"])
    print(bar)
    print("  Manage everything from the browser panel. Ctrl+C to stop.")
    print(bar)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down…")
    finally:
        if ssdp:
            ssdp.stop()
        if mdns:
            mdns.close()
        httpd.shutdown()


# ============================================================================
# Web control panel (served at /). Polls /api/state, toggles devices, shows
# a live discovery/connection feed. Self-contained; no external assets.
# ============================================================================
DASHBOARD_HTML = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fake TV Control Center</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  background:radial-gradient(1000px 600px at 50% -10%,#181d24,#0b0c10 60%,#070809);color:#eef2f7;min-height:100vh}
header{padding:20px 24px 8px;display:flex;flex-wrap:wrap;align-items:center;gap:14px}
header h1{font-size:16px;letter-spacing:.14em;text-transform:uppercase;margin:0;color:#aab3c0;
  display:flex;align-items:center;gap:9px}
header h1 .d{width:9px;height:9px;border-radius:50%;background:#38e08a;box-shadow:0 0 12px #38e08a}
.meta{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.pill{font-size:12px;font-weight:600;padding:6px 11px;border-radius:999px;border:1px solid #2a2f38;color:#aeb6c2}
.pill.ok{color:#0a1a10;background:#38e08a;border-color:transparent}
.pill.bad{color:#2a0b0b;background:#ff6b6b;border-color:transparent}
.warn{margin:8px 24px;padding:11px 14px;border-radius:10px;background:#3a2a08;border:1px solid #7a5a12;
  color:#ffd98a;font-size:13px;line-height:1.5}
.wrap{display:grid;grid-template-columns:1.4fr .9fr;gap:18px;padding:12px 24px 40px;max-width:1200px;margin:0 auto}
@media(max-width:860px){.wrap{grid-template-columns:1fr}}
.section-h{display:flex;align-items:center;gap:10px;margin:2px 2px 12px}
.section-h h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#8b93a1;margin:0}
.section-h .sp{flex:1}
.btn{appearance:none;border:1px solid #2a2f38;background:#12161c;color:#cfd5de;font-size:12px;
  font-weight:600;padding:6px 12px;border-radius:8px;cursor:pointer}
.btn:hover{border-color:#3a4150;color:#fff}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:560px){.cards{grid-template-columns:1fr}}
.card{border:1px solid #232833;border-radius:14px;padding:14px;background:#101419;position:relative;overflow:hidden}
.card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--c)}
.card.off{opacity:.5}
.card .top{display:flex;align-items:flex-start;gap:10px}
.card .nm{font-weight:700;font-size:15px}
.card .md{color:#8b93a1;font-size:12px;margin-top:1px}
.tgl{margin-left:auto;flex:none;width:46px;height:26px;border-radius:999px;background:#2b3039;position:relative;
  cursor:pointer;transition:.15s;border:none}
.tgl.on{background:var(--c)}
.tgl::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:.15s}
.tgl.on::after{left:23px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}
.chip{font-size:10px;font-weight:700;letter-spacing:.02em;padding:3px 7px;border-radius:5px;
  background:#1b212a;color:#aeb6c2;border:1px solid #262c36}
.stat{margin-top:11px;font-size:12px;color:#9aa4b2;display:flex;align-items:center;gap:7px;min-height:18px}
.stat .live{width:7px;height:7px;border-radius:50%;background:#38e08a;box-shadow:0 0 8px #38e08a;flex:none}
.stat.idle .live{background:#3a404a;box-shadow:none}
.stat b{color:#e7ebf1}
.feed{border:1px solid #232833;border-radius:14px;background:#0d1117;max-height:70vh;overflow:auto}
.ev{display:flex;gap:10px;padding:9px 13px;border-bottom:1px solid #171c23;font-size:13px;align-items:baseline}
.ev:first-child{background:rgba(56,224,138,.05)}
.ev .t{color:#5b626e;font-variant-numeric:tabular-nums;font-size:11px;flex:none;width:58px}
.ev .m{flex:1}
.ev .ip{color:#8b93a1;font-size:11px}
.ev .tag{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 6px;border-radius:4px;flex:none}
.tag.connect{background:#0e3a24;color:#7ff0b5}.tag.control{background:#123a5a;color:#8ecbff}
.tag.search{background:#2a2440;color:#c3b3ff}.tag.toggle{background:#2a2f38;color:#cfd5de}
.tag.error{background:#3a1113;color:#ff9b9b}
.empty{padding:26px;text-align:center;color:#5b626e;font-size:13px}
.hintbar{margin:0 24px 10px;font-size:12px;color:#8b93a1}
.hintbar code{background:#12161c;border:1px solid #232833;border-radius:5px;padding:1px 6px;color:#cfd5de}
</style></head><body>
<header>
  <h1><span class="d"></span>Fake TV Control Center</h1>
  <div class="meta" id="meta"></div>
</header>
<div id="warn"></div>
<div class="hintbar" id="hint"></div>
<div class="wrap">
  <div>
    <div class="section-h"><h2>Fake TVs</h2><span class="sp"></span>
      <button class="btn" onclick="allDevices(1)">All on</button>
      <button class="btn" onclick="allDevices(0)">All off</button>
    </div>
    <div class="cards" id="cards"></div>
  </div>
  <div>
    <div class="section-h"><h2>Live discovery feed</h2><span class="sp"></span>
      <button class="btn" onclick="clearFeed()">Clear</button>
    </div>
    <div class="feed" id="feed"></div>
  </div>
</div>
<script>
const KIND_TAG={connect:"connect",control:"control",search:"search",toggle:"toggle",error:"error"};
async function api(path,method){const r=await fetch(path,{method:method||'GET'});return r.json();}
function render(s){
  // meta pills
  const m=document.getElementById('meta');
  m.innerHTML=
    `<span class="pill ${s.mdns_on?'ok':'bad'}">mDNS ${s.mdns_on?'on':'off'}</span>`+
    `<span class="pill ${s.ssdp_on?'ok':'bad'}">SSDP ${s.ssdp_on?'on':'off'}</span>`+
    `<span class="pill">IP ${s.ip}</span>`;
  document.getElementById('warn').innerHTML = s.warning?`<div class="warn">⚠ ${s.warning}</div>`:'';
  document.getElementById('hint').innerHTML =
    `On your phone, this should load if the network is OK: `+
    `<code>http://${s.ip}:${s.http_port}/dd/samsung.xml</code>`;
  // cards
  const c=document.getElementById('cards');c.innerHTML='';
  for(const d of s.devices){
    const el=document.createElement('div');
    el.className='card'+(d.enabled?'':' off');el.style.setProperty('--c',d.color);
    const active=d.enabled&&d.last_time;
    const statTxt = d.hits
      ? `<b>${d.hits}</b> interaction${d.hits>1?'s':''}${d.last_ip?` · last from ${d.last_ip}`:''}${d.last_time?` @ ${d.last_time}`:''}`
      : (d.enabled?'Advertising — waiting for a remote app…':'Off — not advertising');
    el.innerHTML=
      `<div class="top"><div><div class="nm">${d.name}</div><div class="md">${d.model}</div></div>`+
      `<button class="tgl ${d.enabled?'on':''}" onclick="toggle('${d.key}')"></button></div>`+
      `<div class="chips">${d.protocols.map(p=>`<span class="chip">${p}</span>`).join('')}</div>`+
      `<div class="stat ${active?'':'idle'}"><span class="live"></span><span>${statTxt}</span></div>`;
    c.appendChild(el);
  }
  // feed
  const f=document.getElementById('feed');
  if(!s.events.length){f.innerHTML='<div class="empty">No activity yet. Open your remote app and scan for devices — searches and connections will appear here.</div>';}
  else{f.innerHTML=s.events.map(e=>{
    const tag=KIND_TAG[e.kind]||'toggle';
    const cnt=e.count>1?` ×${e.count}`:'';
    return `<div class="ev"><span class="t">${e.time}</span>`+
      `<span class="tag ${tag}">${e.kind}</span>`+
      `<span class="m">${e.msg}${cnt}${e.ip?` <span class="ip">(${e.ip})</span>`:''}</span></div>`;
  }).join('');}
}
async function refresh(){try{render(await api('/api/state'));}catch(e){}}
async function toggle(k){render(await api('/api/toggle?key='+k,'POST'));}
async function allDevices(on){render(await api('/api/all?on='+on,'POST'));}
async function clearFeed(){render(await api('/api/clear','POST'));}
refresh();setInterval(refresh,1500);
</script></body></html>"""


if __name__ == "__main__":
    main()
