#!/usr/bin/env python3
"""
roku_sim.py - Turn your computer into a simulated Roku device.

This makes your machine discoverable and controllable by the official Roku
mobile app (and any other app / script that speaks Roku's ECP protocol),
exactly like a real Roku TV or streaming stick.

It implements the two things a real Roku exposes on the local network:

  1. SSDP discovery  (UDP  :1900) -> lets the Roku app FIND this device
  2. ECP HTTP server (HTTP :8060) -> answers queries + receives button presses

Everything is pure Python standard library. No pip installs required.

Run it:
    python3 roku_sim.py

Then open the Roku app on a phone that's on the SAME Wi-Fi network. It should
discover a device named "My Roku Simulator". Every button you press in the app
prints here in the terminal, and the on-screen state updates like a little TV.

Docs on the protocol this speaks:
    https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md
"""

import argparse
import base64
import socket
import struct
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Discovery / protocol constants (these values are what real Roku devices use)
# ---------------------------------------------------------------------------
SSDP_ADDR = "239.255.255.250"   # standard SSDP multicast group
SSDP_PORT = 1900                # standard SSDP port
ECP_PORT = 8060                 # standard Roku ECP port (do not change lightly)

# Search targets a Roku responds to.
ROKU_SEARCH_TARGETS = {"roku:ecp", "ssdp:all", "upnp:rootdevice"}


def get_local_ip() -> str:
    """Best-effort discovery of this machine's primary LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually send packets; just picks the interface used to reach
        # the internet, which is almost always the LAN interface we want.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Virtual device state - this is the "TV" we are pretending to be.
# ---------------------------------------------------------------------------
class RokuState:
    """Holds the pretend-TV state and the catalog of installed 'channels'."""

    def __init__(self, friendly_name: str, is_tv: bool):
        self.friendly_name = friendly_name
        self.is_tv = is_tv

        # A stable identity for this fake device. The UDN used in device-info
        # MUST match the USN advertised over SSDP, or the app gets confused.
        self.serial = "SIM" + uuid.uuid4().hex[:9].upper()
        self.udn = str(uuid.uuid4())

        # Installed "channels" (apps). id -> display name. These show up on the
        # Roku app's Channels tab. Add your own here.
        self.apps = {
            "12":    "Netflix",
            "13":    "Prime Video",
            "2285":  "Hulu",
            "837":   "YouTube",
            "13535": "Plex",
            "31012": "Spotify",
            "551012": "Apple TV",
            "593099": "Disney Plus",
        }

        # Runtime state that button presses change.
        self.active_app = "Home"     # what's "on screen"
        self.volume = 50             # 0-100
        self.muted = False
        self.powered_on = True
        self.last_key = "-"
        self.text_buffer = ""        # accumulates keyboard (Lit_*) input
        self.lock = threading.Lock()

    def app_name(self, app_id: str) -> str:
        return self.apps.get(app_id, f"App {app_id}")


# ---------------------------------------------------------------------------
# Pretty terminal "TV screen"
# ---------------------------------------------------------------------------
def render_screen(state: RokuState, ip: str):
    """Redraw a little status panel so it feels like a TV reacting to you."""
    power = "ON " if state.powered_on else "OFF"
    vol = "MUTE" if state.muted else f"{state.volume:>3}%"
    line = "=" * 60
    print("\n" + line)
    print(f"  {state.friendly_name}   ({'Roku TV' if state.is_tv else 'Roku Stick'})")
    print(line)
    print(f"  Address : http://{ip}:{ECP_PORT}")
    print(f"  Power   : {power}     Volume : {vol}")
    print(f"  Screen  : {state.active_app}")
    if state.text_buffer:
        print(f"  Typing  : {state.text_buffer!r}")
    print(f"  Last key: {state.last_key}")
    print(line + "\n")


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------------
# Key handling - this is where a remote button becomes an action.
# ---------------------------------------------------------------------------
def handle_key(state: RokuState, key: str, ip: str):
    """Apply a single remote keypress to the virtual device state.

    `key` is a Roku key name, e.g. Home, Up, Select, Play, VolumeUp, or a
    literal character as "Lit_a" (URL-decoded) for on-screen keyboard input.

    This is the natural place to hook keypresses up to REAL actions on your
    machine (control a media player, send OS media keys, etc.). See the
    'HOOK' comment below.
    """
    with state.lock:
        state.last_key = key
        k = key.lower()

        if key.startswith("Lit_"):
            # Literal keyboard character (search boxes, login fields, ...).
            ch = unquote(key[4:])
            state.text_buffer += ch
            log(f"TEXT   -> typed {ch!r}  (buffer: {state.text_buffer!r})")

        elif k == "home":
            state.active_app = "Home"
            state.text_buffer = ""
            log("NAV    -> Home")

        elif k in ("up", "down", "left", "right"):
            log(f"NAV    -> {key}")

        elif k in ("select", "ok"):
            log("NAV    -> Select / OK")

        elif k == "back":
            state.active_app = "Home"
            log("NAV    -> Back")

        elif k in ("play", "pause"):
            log("MEDIA  -> Play/Pause")

        elif k == "rev":
            log("MEDIA  -> Rewind")

        elif k == "fwd":
            log("MEDIA  -> Fast-forward")

        elif k == "instantreplay":
            log("MEDIA  -> Instant replay")

        elif k == "volumeup":
            state.muted = False
            state.volume = min(100, state.volume + 5)
            log(f"AUDIO  -> Volume up ({state.volume}%)")

        elif k == "volumedown":
            state.muted = False
            state.volume = max(0, state.volume - 5)
            log(f"AUDIO  -> Volume down ({state.volume}%)")

        elif k == "volumemute":
            state.muted = not state.muted
            log(f"AUDIO  -> {'Muted' if state.muted else 'Unmuted'}")

        elif k in ("poweroff", "power"):
            state.powered_on = not state.powered_on
            state.active_app = "Home" if state.powered_on else "(off)"
            log(f"POWER  -> {'On' if state.powered_on else 'Off'}")

        elif k == "backspace":
            state.text_buffer = state.text_buffer[:-1]
            log(f"TEXT   -> backspace (buffer: {state.text_buffer!r})")

        elif k in ("enter", "search"):
            log(f"ACTION -> {key} (buffer: {state.text_buffer!r})")

        else:
            log(f"KEY    -> {key}")

        # --- HOOK ---------------------------------------------------------
        # Want a real effect? Do it here. For example, to forward OS media
        # keys you could shell out to `xdotool` (Linux), `nircmd` (Windows),
        # or AppleScript (macOS). Left as a no-op to stay dependency-free.
        # ------------------------------------------------------------------

    render_screen(state, ip)


# ---------------------------------------------------------------------------
# ECP HTTP server - answers the Roku app's queries and button presses.
# ---------------------------------------------------------------------------
def make_handler(state: RokuState, ip: str):

    class ECPHandler(BaseHTTPRequestHandler):
        # Silence the default noisy access log; we do our own logging.
        def log_message(self, *args):
            pass

        # -- helpers -------------------------------------------------------
        def _send(self, body: bytes = b"", status: int = 200,
                  content_type: str = "text/xml; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Server", "Roku/12.0.0 UPnP/1.0 Roku/12.0.0")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _device_info_xml(self) -> bytes:
            model_name = "Roku TV" if state.is_tv else "Roku Streaming Stick+"
            model_num = "C000X" if state.is_tv else "3810X"
            xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
  <udn>{state.udn}</udn>
  <serial-number>{state.serial}</serial-number>
  <device-id>{state.serial}</device-id>
  <vendor-name>Roku</vendor-name>
  <model-name>{model_name}</model-name>
  <model-number>{model_num}</model-number>
  <model-region>US</model-region>
  <is-tv>{str(state.is_tv).lower()}</is-tv>
  <is-stick>{str(not state.is_tv).lower()}</is-stick>
  <supports-ethernet>true</supports-ethernet>
  <wifi-mac>00:11:22:33:44:55</wifi-mac>
  <network-type>wifi</network-type>
  <network-name>Simulated</network-name>
  <friendly-device-name>{state.friendly_name}</friendly-device-name>
  <friendly-model-name>{model_name}</friendly-model-name>
  <default-device-name>{model_name} - {state.serial}</default-device-name>
  <user-device-name>{state.friendly_name}</user-device-name>
  <user-device-location>Home</user-device-location>
  <software-version>12.0.0</software-version>
  <software-build>4209</software-build>
  <power-mode>{"PowerOn" if state.powered_on else "DisplayOff"}</power-mode>
  <supports-suspend>false</supports-suspend>
  <supports-find-remote>false</supports-find-remote>
  <supports-audio-guide>true</supports-audio-guide>
  <developer-enabled>true</developer-enabled>
  <keyed-developer-id></keyed-developer-id>
  <search-enabled>true</search-enabled>
  <voice-search-enabled>true</voice-search-enabled>
  <notifications-enabled>true</notifications-enabled>
  <headphones-connected>false</headphones-connected>
  <supports-ecs-textedit>true</supports-ecs-textedit>
  <supports-ecs-microphone>false</supports-ecs-microphone>
  <supports-wake-on-wlan>false</supports-wake-on-wlan>
  <has-play-on-roku>true</has-play-on-roku>
  <has-mobile-screensaver>false</has-mobile-screensaver>
</device-info>"""
            return xml.encode()

        def _apps_xml(self) -> bytes:
            items = "\n".join(
                f'  <app id="{aid}" type="appl" version="1.0.0">{name}</app>'
                for aid, name in state.apps.items()
            )
            return (f'<?xml version="1.0" encoding="UTF-8" ?>\n<apps>\n{items}\n</apps>').encode()

        def _active_app_xml(self) -> bytes:
            with state.lock:
                app = state.active_app
            if app == "Home":
                inner = '  <app>Roku</app>'
            else:
                # find id for the active app name if known
                aid = next((i for i, n in state.apps.items() if n == app), "dev")
                inner = f'  <app id="{aid}" type="appl" version="1.0.0">{app}</app>'
            return (f'<?xml version="1.0" encoding="UTF-8" ?>\n<active-app>\n{inner}\n</active-app>').encode()

        # -- routing -------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path == "/query/device-info":
                self._send(self._device_info_xml())
            elif path == "/query/apps":
                self._send(self._apps_xml())
            elif path == "/query/active-app":
                self._send(self._active_app_xml())
            elif path.startswith("/query/icon/"):
                # A tiny 1x1 transparent PNG so the app has something to show.
                png = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
                    "C0lEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg=="
                )
                self._send(png, content_type="image/png")
            elif path == "/":
                # Root device description (helps some discovery flows).
                self._send(self._root_desc())
            else:
                self._send(b"<status>OK</status>")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            # Drain any request body so the socket stays healthy.
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                try:
                    self.rfile.read(length)
                except Exception:
                    pass

            if path.startswith("/keypress/"):
                key = path[len("/keypress/"):]
                handle_key(state, key, ip)
                self._send(status=200)
            elif path.startswith("/keydown/") or path.startswith("/keyup/"):
                # Roku sends keydown+keyup for held buttons; we act on keydown.
                if path.startswith("/keydown/"):
                    handle_key(state, path[len("/keydown/"):], ip)
                self._send(status=200)
            elif path.startswith("/launch/"):
                app_id = path[len("/launch/"):].split("?", 1)[0]
                with state.lock:
                    state.active_app = state.app_name(app_id)
                    name = state.active_app
                log(f"LAUNCH -> {name} (id {app_id})")
                render_screen(state, ip)
                self._send(status=200)
            elif path.startswith("/install/"):
                app_id = path[len("/install/"):].split("?", 1)[0]
                log(f"INSTALL-> requested app id {app_id}")
                self._send(status=200)
            elif path.startswith("/input"):
                log("INPUT  -> received input event")
                self._send(status=200)
            elif path.startswith("/search"):
                log("SEARCH -> received search request")
                self._send(status=200)
            else:
                self._send(status=200)

        def _root_desc(self) -> bytes:
            xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:roku-com:device:player:1-0</deviceType>
    <friendlyName>{state.friendly_name}</friendlyName>
    <manufacturer>Roku</manufacturer>
    <modelName>{"Roku TV" if state.is_tv else "Roku Stick"}</modelName>
    <serialNumber>{state.serial}</serialNumber>
    <UDN>uuid:{state.udn}</UDN>
  </device>
</root>"""
            return xml.encode()

    return ECPHandler


def run_ecp_server(state: RokuState, ip: str):
    handler = make_handler(state, ip)
    httpd = ThreadingHTTPServer(("0.0.0.0", ECP_PORT), handler)
    log(f"ECP HTTP server listening on http://{ip}:{ECP_PORT}")
    httpd.serve_forever()


# ---------------------------------------------------------------------------
# SSDP discovery responder - answers the Roku app's "who's out there?" search.
# ---------------------------------------------------------------------------
def run_ssdp_responder(state: RokuState, ip: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass

    sock.bind(("", SSDP_PORT))

    # Join the SSDP multicast group so we receive M-SEARCH discovery packets.
    mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(ip))
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    location = f"http://{ip}:{ECP_PORT}/"
    usn = f"uuid:roku:ecp:{state.serial}"

    log(f"SSDP responder listening on {SSDP_ADDR}:{SSDP_PORT} (advertising {location})")

    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            break
        text = data.decode(errors="ignore")
        if not text.startswith("M-SEARCH"):
            continue

        # Parse the ST (search target) header.
        st = None
        for line in text.split("\r\n"):
            if line.lower().startswith("st:"):
                st = line.split(":", 1)[1].strip()
                break
        if st is None:
            continue

        if st in ROKU_SEARCH_TARGETS or st.startswith("roku"):
            reply = (
                "HTTP/1.1 200 OK\r\n"
                "Cache-Control: max-age=3600\r\n"
                "ST: roku:ecp\r\n"
                f"USN: {usn}\r\n"
                "Ext: \r\n"
                "Server: Roku UPnP/1.0 MiniUPnPd/1.4\r\n"
                f"LOCATION: {location}\r\n"
                "device-group.roku.com: \r\n"
                "\r\n"
            )
            sock.sendto(reply.encode(), addr)
            log(f"DISCOVERY -> replied to {addr[0]} (ST={st})")


def send_notify_alive(state: RokuState, ip: str):
    """Periodically shout our presence so the app can find us proactively."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    location = f"http://{ip}:{ECP_PORT}/"
    usn = f"uuid:roku:ecp:{state.serial}"
    notify = (
        "NOTIFY * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        "Cache-Control: max-age=3600\r\n"
        f"LOCATION: {location}\r\n"
        "NT: roku:ecp\r\n"
        "NTS: ssdp:alive\r\n"
        "Server: Roku UPnP/1.0 MiniUPnPd/1.4\r\n"
        f"USN: {usn}\r\n"
        "\r\n"
    ).encode()
    while True:
        try:
            sock.sendto(notify, (SSDP_ADDR, SSDP_PORT))
        except OSError:
            pass
        time.sleep(30)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Simulate a Roku TV / stick on your LAN.")
    parser.add_argument("--name", default="My Roku Simulator",
                        help="Friendly device name shown in the Roku app.")
    parser.add_argument("--stick", action="store_true",
                        help="Advertise as a streaming stick instead of a TV.")
    parser.add_argument("--ip", default=None,
                        help="Override the LAN IP to advertise (auto-detected by default).")
    args = parser.parse_args()

    ip = args.ip or get_local_ip()
    state = RokuState(friendly_name=args.name, is_tv=not args.stick)

    print("\n" + "#" * 60)
    print("#  Roku Device Simulator")
    print("#" * 60)
    print(f"  Pretending to be : {args.name} ({'stick' if args.stick else 'TV'})")
    print(f"  This machine's IP : {ip}")
    print(f"  Serial            : {state.serial}")
    print("  Open the Roku mobile app on the SAME Wi-Fi network to connect.")
    print("  Press Ctrl+C to stop.")
    print("#" * 60)

    threads = [
        threading.Thread(target=run_ecp_server, args=(state, ip), daemon=True),
        threading.Thread(target=run_ssdp_responder, args=(state, ip), daemon=True),
        threading.Thread(target=send_notify_alive, args=(state, ip), daemon=True),
    ]
    for t in threads:
        t.start()

    render_screen(state, ip)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down. Bye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
