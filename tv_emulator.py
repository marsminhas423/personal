#!/usr/bin/env python3
"""
Fake Android TV / Smart TV emulator — makes discoverable "TVs" appear to
phone remote apps (universal remotes, Google TV/Home, YouTube cast, brand apps).

WHY THIS EXISTS
---------------
A phone remote app finds TVs by scanning the LOCAL network for advertised
services. Real apps use a mix of:
  * mDNS / DNS-SD   (_googlecast._tcp, _androidtvremote2._tcp, _airplay._tcp, ...)
  * SSDP / UPnP     (DIAL, Samsung, LG, Roku ECP, generic rootdevice)
This program advertises several fake TVs on ALL of those at once, so a
universal remote scanning any of them will list the devices.

HARD REQUIREMENT
----------------
Run this on a computer that is on the SAME Wi-Fi / LAN as the phone.
mDNS and SSDP are link-local multicast — they do NOT cross the internet,
VPNs, "guest" Wi-Fi isolation, or router AP-isolation. If the phone can't
ping this machine, it will never discover these devices.

This is a DISCOVERY/VISIBILITY test target: devices will appear in the app's
list. Full pairing/streaming control is protocol-specific and much deeper —
this emulator answers the handshake far enough to be seen and described.

USAGE
-----
    pip install zeroconf         # for mDNS/Cast/Android-TV discovery
    sudo python3 tv_emulator.py  # sudo/admin helps bind UDP 1900 (SSDP)

    python3 tv_emulator.py --ip 192.168.1.50   # force the advertised IP
    python3 tv_emulator.py --no-mdns           # SSDP only
"""

import argparse
import http.server
import socket
import socketserver
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Fake TVs to advertise. Each device lists which protocols it announces so the
# app can label it plausibly (a Shield is Android TV/Cast; Samsung is its own
# SSDP scheme; Roku uses ECP; etc.). "dial" + "rootdevice" are broad catch-alls
# that most universal remotes pick up.
# ----------------------------------------------------------------------------
DEVICES = [
    dict(key="samsung", name="Samsung TV (Living Room)", brand="Samsung",
         model="QN90B Neo QLED",
         protocols=["dial", "rootdevice", "samsung", "airplay", "samsungmsf"]),
    dict(key="lg", name="LG webOS TV (Bedroom)", brand="LG Electronics",
         model="OLED C3",
         protocols=["dial", "rootdevice", "lg", "airplay"]),
    dict(key="sony", name="Sony BRAVIA (Study)", brand="Sony",
         model="BRAVIA XR A80L",
         protocols=["dial", "rootdevice", "googlecast", "androidtv"]),
    dict(key="tcl", name="TCL Google TV (Playroom)", brand="TCL",
         model="C845 QD-Mini LED",
         protocols=["dial", "rootdevice", "googlecast", "androidtv"]),
    dict(key="shield", name="NVIDIA Shield (Office)", brand="NVIDIA",
         model="SHIELD Android TV Pro",
         protocols=["googlecast", "androidtv"]),
    dict(key="roku", name="Roku (Guest Room)", brand="Roku",
         model="Roku Ultra 4802",
         protocols=["dial", "rootdevice", "roku"]),
]

HTTP_PORT = 8008          # single HTTP server serves every device's description
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SERVER_ID = "Linux/6.0 UPnP/1.1 FakeTV/1.0"

# SSDP search-target strings per protocol
ST = {
    "rootdevice": "upnp:rootdevice",
    "dial":       "urn:dial-multiscreen-org:service:dial:1",
    "samsung":    "urn:samsung.com:device:RemoteControlReceiver:1",
    "lg":         "urn:lge-com:service:webos-second-screen:1",
    "roku":       "roku:ecp",
}

# mDNS service types per protocol
MDNS = {
    "googlecast": ("_googlecast._tcp.local.", 8009),
    "androidtv":  ("_androidtvremote2._tcp.local.", 6466),
    "airplay":    ("_airplay._tcp.local.", 7000),
    "samsungmsf": ("_samsungmsf._tcp.local.", 8001),
}


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


# Assign stable-per-run identifiers to each device.
def init_ids():
    for d in DEVICES:
        d["udn"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, "faketv-" + d["key"]))
        d["serial"] = d["udn"].replace("-", "")[:12].upper()
        d["host"] = d["key"] + "-faketv"


# ============================================================================
# HTTP server — serves UPnP/DIAL device descriptions and Roku ECP info.
# LOCATION headers in SSDP point here.
# ============================================================================
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeTV/1.0"

    def log_message(self, *a):  # keep the console clean
        pass

    def _send(self, body: bytes, ctype="application/xml", extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ip = self.server.adv_ip
        path = self.path.split("?")[0]

        # UPnP / DIAL device description:  /dd/<key>.xml
        if path.startswith("/dd/") and path.endswith(".xml"):
            key = path[4:-4]
            dev = next((d for d in DEVICES if d["key"] == key), None)
            if dev:
                xml = f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:dial-multiscreen-org:device:dial:1</deviceType>
    <friendlyName>{dev['name']}</friendlyName>
    <manufacturer>{dev['brand']}</manufacturer>
    <modelName>{dev['model']}</modelName>
    <UDN>uuid:{dev['udn']}</UDN>
  </device>
</root>""".encode()
                # DIAL clients read the REST endpoint from this header
                self._send(xml, extra={
                    "Application-URL": f"http://{ip}:{HTTP_PORT}/apps/"})
                return

        # Roku ECP device info:  /query/device-info
        if path == "/query/device-info":
            dev = next((d for d in DEVICES if d["key"] == "roku"), None)
            if dev:
                xml = f"""<device-info>
  <udn>{dev['udn']}</udn>
  <serial-number>{dev['serial']}</serial-number>
  <device-id>{dev['serial']}</device-id>
  <vendor-name>Roku</vendor-name>
  <model-name>{dev['model']}</model-name>
  <friendly-device-name>{dev['name']}</friendly-device-name>
  <power-mode>PowerOn</power-mode>
  <supports-wake-on-wlan>true</supports-wake-on-wlan>
</device-info>""".encode()
                self._send(xml)
                return

        self.send_response(404)
        self.end_headers()

    # Roku ECP control keypresses arrive as POST /keypress/<key>; ack them.
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class HTTPServerThread(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ============================================================================
# SSDP responder — answers M-SEARCH and periodically NOTIFYs ssdp:alive.
# ============================================================================
class SSDP(threading.Thread):
    daemon = True

    def __init__(self, ip):
        super().__init__()
        self.ip = ip
        self.running = True
        self.services = self._build_services()

    def _location(self, dev, proto):
        if proto == "roku":
            return f"http://{self.ip}:{HTTP_PORT}/"
        return f"http://{self.ip}:{HTTP_PORT}/dd/{dev['key']}.xml"

    def _usn(self, dev, proto):
        if proto == "roku":
            return f"uuid:roku:ecp:{dev['serial']}"
        st = ST[proto]
        if proto == "rootdevice":
            return f"uuid:{dev['udn']}::{st}"
        return f"uuid:{dev['udn']}::{st}"

    # Flat list of (device, proto, st, usn, location) for every SSDP service.
    def _build_services(self):
        out = []
        for d in DEVICES:
            for proto in d["protocols"]:
                if proto not in ST:
                    continue
                out.append((d, proto, ST[proto],
                            self._usn(d, proto), self._location(d, proto)))
            # every device also answers to its bare UDN
            out.append((d, "udn", f"uuid:{d['udn']}",
                        f"uuid:{d['udn']}", self._location(d, "rootdevice")))
        return out

    def _response(self, st, usn, location):
        return ("HTTP/1.1 200 OK\r\n"
                "CACHE-CONTROL: max-age=1800\r\n"
                f"DATE: {httpdate()}\r\n"
                "EXT:\r\n"
                f"LOCATION: {location}\r\n"
                f"SERVER: {SERVER_ID}\r\n"
                f"ST: {st}\r\n"
                f"USN: {usn}\r\n"
                "BOOTID.UPNP.ORG: 1\r\n"
                "CONFIGID.UPNP.ORG: 1\r\n\r\n").encode()

    def _notify(self, nt, usn, location):
        return ("NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                "CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {location}\r\n"
                f"NT: {nt}\r\n"
                "NTS: ssdp:alive\r\n"
                f"SERVER: {SERVER_ID}\r\n"
                f"USN: {usn}\r\n"
                "BOOTID.UPNP.ORG: 1\r\n\r\n").encode()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("", SSDP_PORT))
        mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR),
                           socket.inet_aton(self.ip))
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            print(f"  ! could not join SSDP multicast group: {e}")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        # send multicast out the same interface we advertise on (multi-homed hosts)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(self.ip))
        except OSError:
            pass
        sock.settimeout(1.0)

        self._announce(sock)              # initial burst of alive notifies
        last_notify = time.time()

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                data, addr = None, None
            except OSError:
                break

            if data and data.startswith(b"M-SEARCH"):
                text = data.decode("utf-8", "ignore")
                if "ssdp:discover" not in text:
                    continue
                want = "ssdp:all"
                for line in text.split("\r\n"):
                    if line.upper().startswith("ST:"):
                        want = line.split(":", 1)[1].strip()
                self._reply(sock, addr, want)

            if time.time() - last_notify > 30:
                self._announce(sock)
                last_notify = time.time()

        # polite byebye on shutdown
        for dev, proto, st, usn, location in self.services:
            try:
                msg = self._notify(st, usn, location).replace(
                    b"ssdp:alive", b"ssdp:byebye")
                sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass
        sock.close()

    def _reply(self, sock, addr, want):
        for dev, proto, st, usn, location in self.services:
            if want == "ssdp:all" or want == st:
                try:
                    sock.sendto(self._response(st, usn, location), addr)
                except OSError:
                    pass

    def _announce(self, sock):
        for dev, proto, st, usn, location in self.services:
            try:
                sock.sendto(self._notify(st, usn, location),
                            (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass

    def stop(self):
        self.running = False


# ============================================================================
# mDNS advertiser (zeroconf) — Cast / Android TV Remote / AirPlay / Samsung MSF
# ============================================================================
def start_mdns(ip):
    try:
        from zeroconf import Zeroconf, ServiceInfo
    except ImportError:
        print("  ! zeroconf not installed — skipping mDNS/Cast/Android-TV "
              "advertising.  Install with:  pip install zeroconf")
        return None, []

    zc = Zeroconf()
    infos = []
    for d in DEVICES:
        for proto in d["protocols"]:
            if proto not in MDNS:
                continue
            stype, port = MDNS[proto]
            safe = d["name"].replace(".", " ")
            iname = f"{safe}.{stype}"
            if proto == "googlecast":
                props = {"id": d["udn"].replace("-", ""), "md": d["model"],
                         "fn": d["name"], "ca": "4101", "st": "0",
                         "rs": "", "ve": "05", "ic": "/setup/icon.png"}
            elif proto == "androidtv":
                props = {"bt": d["serial"], "bs": d["serial"]}
            elif proto == "airplay":
                props = {"deviceid": d["serial"], "model": d["model"],
                         "features": "0x5A7FFFF7,0x1E", "srcvers": "220.68"}
            else:  # samsungmsf
                props = {"id": d["udn"], "fn": d["name"], "md": d["model"]}
            info = ServiceInfo(
                stype, iname,
                addresses=[socket.inet_aton(ip)], port=port,
                properties=props, server=f"{d['host']}.local.")
            try:
                # strict=False: real Android TVs advertise "_androidtvremote2"
                # (16 chars), which violates the RFC 15-char limit zeroconf
                # enforces by default. We must match reality, not the RFC.
                zc.register_service(info, allow_name_change=True, strict=False)
                infos.append(info)
            except Exception as e:
                print(f"  ! mDNS register failed ({d['key']}/{proto}): {e}")
    return zc, infos


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="Fake Smart-TV discovery emulator")
    ap.add_argument("--ip", help="advertised IP (default: auto-detected LAN IP)")
    ap.add_argument("--no-mdns", action="store_true", help="disable mDNS/Cast")
    ap.add_argument("--no-ssdp", action="store_true", help="disable SSDP/DIAL")
    args = ap.parse_args()

    ip = args.ip or get_local_ip()
    init_ids()

    print("=" * 66)
    print("  Fake Smart-TV emulator — discovery test target")
    print("=" * 66)
    print(f"  Advertising on IP : {ip}")
    print(f"  HTTP descriptions : http://{ip}:{HTTP_PORT}/")
    if ip.startswith(("127.", "192.0.2.")):
        print("  ! This looks like a loopback/TEST-NET address, NOT a real")
        print("    LAN IP. A phone on your Wi-Fi will not reach it. Run this")
        print("    on a machine on the same network as the phone, or pass --ip.")
    print("-" * 66)
    print("  Devices:")
    for d in DEVICES:
        print(f"    • {d['name']:<28} [{', '.join(d['protocols'])}]")
    print("-" * 66)

    # HTTP
    httpd = HTTPServerThread(("", HTTP_PORT), Handler)
    httpd.adv_ip = ip
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"  ✓ HTTP server on :{HTTP_PORT}")

    # SSDP
    ssdp = None
    if not args.no_ssdp:
        ssdp = SSDP(ip)
        ssdp.start()
        print(f"  ✓ SSDP/DIAL responder on :{SSDP_PORT} "
              f"({len(ssdp.services)} services)")

    # mDNS
    zc = None
    if not args.no_mdns:
        zc, infos = start_mdns(ip)
        if zc:
            print(f"  ✓ mDNS advertising {len(infos)} services "
                  "(Cast / Android TV / AirPlay)")

    print("-" * 66)
    print("  Open your universal remote app and scan for devices.")
    print("  Press Ctrl+C to stop.")
    print("=" * 66)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down…")
    finally:
        if ssdp:
            ssdp.stop()
        if zc:
            zc.close()
        httpd.shutdown()


if __name__ == "__main__":
    main()
