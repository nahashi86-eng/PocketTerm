#!/usr/bin/env python3
"""
Network Globe
A spinning black-and-white 3D Earth. Every device found on your local network
(WiFi or Ethernet) appears as a point. Click a point, or a row in the list, to
see its details: hostname, MAC, OS guess, open ports and more.

Requires:  pip install PySide6
Run:       python network_globe.py

How devices are found (so that nothing is missed)
- The real subnet mask is read from the OS (so /23, /22 ... networks work, not just /24).
- ICMP ping sweep (gives TTL and latency).
- Devices that block ping are still found by: a NetBIOS name query, a TCP connect probe
  (an answer OR a "connection refused" both prove the host is alive), mDNS and SSDP
  multicast queries, and finally the OS ARP table, which lists every device that answered ARP.
- The scan repeats automatically every 40 seconds. A device is only removed after it was
  missed by two scans in a row, so phones that sleep don't flicker in and out.

Notes
- Devices on a LAN have no real-world location, so each point is placed on the
  globe at a position derived from its IP address. The placement is stable but
  arbitrary.
- Scanning only touches your own subnet.
"""

import sys
import json
import os
import re
import math
import time
import errno
import socket
import zlib
import base64
import hashlib
import platform
import subprocess
import ipaddress
import ctypes
import csv
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF, QRectF
from PySide6.QtGui import (QIcon, QColor, QPainter, QPen, QBrush, QRadialGradient, QFont, QFontMetrics,
                           QPolygonF, QPainterPath, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QGridLayout, QLabel, QPushButton, QListWidget, QListWidgetItem,
                               QFrame, QProgressBar, QSizePolicy, QDialog, QTabWidget, QTextEdit,
                               QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
                               QInputDialog, QComboBox, QLineEdit, QHeaderView)

# ============================================================
# THEME (same palette as PocketTerm, Violet accent)
# ============================================================

TEXT, TEXT_BODY, TEXT_MUTED, TEXT_FAINT = "#f3f0ff", "#d9d3f2", "#a69fc4", "#857ca8"
OK_COLOR, ERR_COLOR, WARN_COLOR = "#6ee7b7", "#fda4af", "#fcd34d"
ACCENT, STRONG, SOFT, LIGHT, LIGHTER, BORDER, HOVER_BORDER = (
    "#8b5cf6", "#7c3aed", "#a78bfa", "#c4b5fd", "#ddd6fe", "#3b2a66", "#5b3fb5")
BACKGROUND = "#08070d"

# ============================================================
# APP ICON (same lookup as PocketTerm: Images/icon.png, else the first PNG in that folder)
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
ICON_NAME = "icon.png"
ICON_DIRS = [APP_DIR / "Images", SCRIPT_DIR / "Images", SCRIPT_DIR.parent / "Images", Path.cwd() / "Images"]


def find_icon():
    seen = []
    for folder in ICON_DIRS:
        if folder in seen or not folder.is_dir():
            continue
        seen.append(folder)
        if (folder / ICON_NAME).is_file():
            return folder / ICON_NAME
        pngs = sorted(folder.glob("*.png")) + sorted(folder.glob("*.PNG"))
        if pngs:
            return pngs[0]
    return None


STYLE = f"""
QMainWindow, QWidget {{ background-color: {BACKGROUND}; color: {TEXT_BODY}; font-family: 'Segoe UI'; font-size: 13px; }}
QFrame#Panel {{ background-color: #0e0a18; border: none; border-left: 1px solid #231a3a; }}
QFrame#Card {{ background-color: #130e21; border: 1px solid #2a1f45; border-radius: 12px; }}
QLabel {{ background: transparent; }}
QLabel#Title {{ color: {TEXT}; font-size: 22px; font-weight: 600; }}
QLabel#Subtitle {{ color: {TEXT_MUTED}; }}
QLabel#Key {{ color: {TEXT_FAINT}; }}
QLabel#Value {{ color: {TEXT_BODY}; }}
QLabel#DetailIp {{ color: {TEXT}; font-size: 18px; font-weight: 600; }}
QLabel#Hint {{ color: {TEXT_FAINT}; font-size: 12px; }}
QPushButton#Primary {{ background-color: {STRONG}; color: #ffffff; border: 1px solid {ACCENT}; border-radius: 8px;
    padding: 9px 16px; font-weight: 600; }}
QPushButton#Primary:hover {{ background-color: {ACCENT}; }}
QPushButton#Primary:disabled {{ background-color: #1a1230; color: #6f6590; border: 1px solid #1f1736; }}
QListWidget {{ background-color: #0e0a18; border: 1px solid #2a1f45; border-radius: 8px; padding: 6px; outline: 0; }}
QListWidget::item {{ border-radius: 6px; border: 1px solid transparent; }}
QListWidget::item:hover {{ background-color: #181129; }}
QListWidget::item:selected {{ background-color: #1e1535; border: 1px solid {HOVER_BORDER}; }}
QProgressBar {{ background-color: #0e0a18; border: 1px solid #2a1f45; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: {BACKGROUND}; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2a1f45; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {HOVER_BORDER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""

# ============================================================
# EARTH DATA (1-degree land mask from Natural Earth 110m, public domain)
# ============================================================

LAND_MASK_B64 = (
    "eNrtmU9r3UYQwEeW8XNax/KtgRjLpYceawjUKXEsQw/5Cj0U4tIv4P6BOMRYCy3kmGsPpYZ+gh57KK1CDr4EnGNvlptS5xDI"
    "ui54Tdaa7mq1/7Sr12d6zYLNe3o/jWZmZ2dnRwBvRnwUWAMgkknYEhH5PfFvAnaEZnjXt2Jwblgqvt0Vd7Q3JTzC3hQwK1q4"
    "AkjRMGUdsEvFg1f4m5IsLExa8Uq9QHS2Dr/mTWZg2GX6p2Q38MTZHcQmRa0GLKrL4r6vpEZp5cDv4XFrWKklK5tZa3cjnoxW"
    "86RZh0Nkcl5ceKmdKcml51Z0go9PEQ87/5nL27DRmnHCEmq1SLE5xlKIlL8Z6wXxoNULedIaUkNCJIwnWAilLTxLBNxZjFzo"
    "siGtLPAFfPQQ8a7wMpFz3ihHiAfvpK9tDNCpVIYZPpIIPhdOk3CtrJB2OiziaVPr+OHl6RFWiXaG0OeoLtEfPLGfn+9JJWkX"
    "23jxCvvjof1ICjmH1dz3XXCPG6+/wZ/FcyCXFuJxE/KPnJg/wxcy2jIZC3gQEQcn5uPBVk5B+GJUS/siejQihPSopNcKFQMl"
    "jyltlx6H682GhjEG15BZOG0WIFchV4bmZWICZvDgioFvmsXAiwD+UVz/oFZR3AiNuoBLyX2e+yi5tt4uSAoquMRca7jOWNlz"
    "hEoM+QaoKadJF3AwquapD3PByF+kNHVlX6+orMp6QcRUsuliv52VLjwhp1k/FJaTRq+qQktW2eI2C32c6ESSqFR0bGAewBRM"
    "LsisbnLcwHGwcepKt9iD8VOYbhuV16aKcbAWRbHLOcF4AU5GUuPEgxsHXg7hi3m9FbXWWm0ad7Mw8WLhA6wtzDZDmIGF98vK"
    "8dzXgRrNpmbPDpDktQPzANZXxIZxjKORVaN5zELJRou5c8Q9mtu89SyAmdke5qRQ5sBVYKC+UtC5/NRx3RmFMTCb5SLbUg3/"
    "yRLS2+TtzpPTVXbfkdzwt4lXPXjwAa58ITQ18FpGgl2emAKhgSVnZdE1DGFw4FkHZreaoNjgzq1enLIRdeFU1xnaAkqd5cJH"
    "rueUmNqBz9HZqXgKY+FePvKHuPQH9b3uZbpe6cVM6aRNez0suYYve8v3AntrQo9bAr7nFW1iuZZOOeYXajV82Id1iP7TgxcF"
    "POMlM7GrsE77lz14prQ6dwILbDr4qcFmNWwycAfviUlXWs/Zkq6biPp2Trw5ORRwIVWns3bySAfv6AgotORGfKQmQbRw1a27"
    "7bTx1DgQk5FhVXLXc92mIwKxq1FzvR9x4UUomno+LJ9pOuvBx2LmWhgCGKzrO/i5LCek2mQpgG/Y9dpVW+LCiMEqksUATgxc"
    "2GBLpSYkVAP6kmWwCTjFKgvhjLzlw08ELKuAaq4KjxOk9OG8rV3TvHonhFPSZY6RcwSRMKxG4IseLCdU6DwPn29GLOwSWOrA"
    "ohj6Fj4OTx/CoyrpJs46/VRo11Z6gR56I3XgRUhqeBKHldKFqupYuzQW64RuxOFW6XVV9KjQXKJpRLCFuzWrmIxm1cAhjzjZ"
    "QMEjerdeiMO1A6sbU1Y+/SR+IqwcWJ9WEL+Lw9zqrDMDLQ+vV/GzpoV17mTlXvpXHGZGDZ07efHL1VfVSgzmJkVreD3/YRfZ"
    "YnDuNpomTqJdm9qDQ74YPR8TDet5y+E6vM9PY5I75zmSxQoXx4ij3kE/i8Myq6UIdQwmfRiKXTo6JddiDYAQBnGA/axejMFm"
    "K7R71BUC79YzYYjqSfZgSNhWLJ5d2O4OaQgnzm4qZ9Amz9XLwCXdAVgJEocbG7ajgLyFSX8KmYUb+8hGwAtpMCsWPuKO5fLG"
    "mcDRFibcuT4OluqDW2CFDaORneRSbNzMNZxANQZG06ZIVTDWg4mjcOAu46yEMAzCoc58CGYRmBqYGyUngNlAoRqBcwdOo3Bm"
    "4WwS2OpJo6HrwZWB6/+WTAxQ+5JZBLa2Egem4+EUvUqkCOpRAZvF4fbckr+FJwVcBdVrbNSqBbfZg+lAazOR8FbYRR2AhQ7r"
    "E8Jtd6MH88EW65T425kUvirc/rt/pHg5CItjW+LPS8mGO70EEt/P5aB9kMrmy6SwLeEng9cHqtHoWOvB9SWa5iFcjYFJGG8w"
    "LgAmVyO9hNJlQiaHi0uwkF/Gddll4Dfjf4xLzUp1Cbi3crbVa5lpUZhEYD8jpfLrNWgbppFcRXs9j0OSDfRDAKbdjDQjuaqI"
    "v3KS9pmHLdxRBfI+eu+cPLh72LTpIe17nWKb+Z6JNdj47cm2ExF795XRZSib/rsxjMDZznJmv+dxltu+UW4dNADTXo+JDvSI"
    "Tck+k/haJThG5ZH7PgGmocAxks/s10rmmcH3LTJ8t50HpSKRIo71nJ2j8iGSoRc6rG/QOZSDL394tJU3NIRFyxPDHNx3NW/G"
    "v2y5fSI="
)

_MASK_ROWS = 180
_MASK_COLS = 360
_MASK = zlib.decompress(base64.b64decode(LAND_MASK_B64))


def is_land(lat, lon):
    y = min(_MASK_ROWS - 1, max(0, int(90 - lat)))
    x = min(_MASK_COLS - 1, max(0, int(lon + 180)))
    return (_MASK[y * (_MASK_COLS // 8) + x // 8] >> (7 - x % 8)) & 1


def unit_vec(lat_deg, lon_deg):
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    return (math.cos(la) * math.sin(lo), math.sin(la), math.cos(la) * math.cos(lo))


def build_land(step=2.0):
    """Evenly spaced dots (fewer per row towards the poles) that fall on land."""
    ll, xyz = [], []
    lat = -90.0 + step / 2
    while lat < 90:
        n = max(1, round(360 * math.cos(math.radians(lat)) / step))
        for i in range(n):
            lon = -180 + 360 * (i + 0.5) / n
            if is_land(lat, lon):
                ll.append((lat, lon))
                xyz.append(unit_vec(lat, lon))
        lat += step
    return ll, xyz


LAND_LL, LAND_XYZ = build_land()


def build_graticule():
    lines = []
    for lon in range(-180, 180, 30):
        lines.append([unit_vec(lat, lon) for lat in range(-90, 91, 5)])
    for lat in (-60, -30, 0, 30, 60):
        lines.append([unit_vec(lat, lon) for lon in range(-180, 181, 5)])
    return lines


GRATICULE = build_graticule()


def place_device(ip, used):
    """Stable, arbitrary spot on land derived from the IP address."""
    idx = int(hashlib.sha1(ip.encode()).hexdigest()[:8], 16) % len(LAND_LL)
    while idx in used:
        idx = (idx + 1) % len(LAND_LL)
    used.add(idx)
    lat, lon = LAND_LL[idx]
    return {"lat": lat, "lon": lon, "lat_rad": math.radians(lat), "lon_rad": math.radians(lon),
            "xyz": LAND_XYZ[idx]}


# ============================================================
# NETWORK HELPERS
# ============================================================

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAC_RE = r"([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})"

PORTS = {
    22: "SSH", 53: "DNS", 80: "HTTP", 135: "Windows RPC", 139: "NetBIOS", 443: "HTTPS",
    445: "SMB file sharing", 548: "Apple file sharing", 554: "RTSP video", 631: "IPP printing",
    3389: "Remote Desktop", 5000: "UPnP / NAS web", 5555: "Android debug", 7000: "AirPlay",
    8008: "Google Cast", 8009: "Google Cast", 8080: "HTTP (alt)", 8443: "HTTPS (alt)",
    9100: "Raw printing", 62078: "iOS device sync",
}

# Ports used only to prove that a host is alive: an open port OR a refused connection means it answered.
ALIVE_PORTS = (80, 443, 445, 22, 135, 139, 8080, 62078, 5555, 8009)
REFUSED = {errno.ECONNREFUSED, 10061}

# Small, partial vendor list: only prefixes that identify VMs and Raspberry Pi boards.
OUI = {
    "00:0C:29": "VMware", "00:50:56": "VMware", "00:05:69": "VMware",
    "08:00:27": "VirtualBox", "00:15:5D": "Microsoft Hyper-V",
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
}

SCAN_INTERVAL_MS = 40000       # automatic rescan
MISSES_BEFORE_REMOVAL = 2      # a device must be missed by this many scans in a row to disappear


def run(cmd, timeout=4):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW).stdout
    except Exception:
        return ""


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _interface_pairs():
    """[(ip, prefix)] for every IPv4 address on this PC (psutil if installed, else the OS tools)."""
    pairs = []
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.netmask:
                    try:
                        pairs.append((a.address, ipaddress.IPv4Network("0.0.0.0/" + a.netmask).prefixlen))
                    except ValueError:
                        pass
        if pairs:
            return pairs
    except Exception:
        pass
    system = platform.system()
    try:
        if system == "Windows":
            out = run(["ipconfig"], 8)
            # "IPv4 Address . . . : 192.168.1.5" is followed by "Subnet Mask . . . : 255.255.255.0"
            # (labels are translated, so only "ip line, then label: dotted value" is matched).
            for ip, mask in re.findall(r"(\d+\.\d+\.\d+\.\d+)[^\r\n]*\r?\n\s*[^\r\n:]*:\s*(\d+\.\d+\.\d+\.\d+)", out):
                try:
                    pairs.append((ip, ipaddress.IPv4Network("0.0.0.0/" + mask).prefixlen))
                except ValueError:
                    pass  # not a subnet mask (e.g. a DNS server line)
        elif system == "Darwin":
            out = run(["ifconfig"], 6)
            for ip, hexmask in re.findall(r"inet (\d+\.\d+\.\d+\.\d+) netmask 0x([0-9a-fA-F]+)", out):
                pairs.append((ip, bin(int(hexmask, 16)).count("1")))
        else:
            out = run(["ip", "-o", "-4", "addr", "show"], 6)
            for ip, prefix in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out):
                pairs.append((ip, int(prefix)))
    except (ValueError, OSError):
        pass
    return pairs


def local_networks():
    """Return the LAN interface selected by the OS routing table.

    Scanning every private interface also scans virtual networks (for example,
    VirtualBox's 192.168.56.0/24 host-only adapter). That makes this PC appear
    as multiple devices and wastes time scanning networks that are not the Wi-Fi
    LAN. The UDP route probe in local_ip() identifies the source address used for
    normal outbound traffic, so only that interface is scanned by default.
    """
    primary = local_ip()
    if primary.startswith("127."):
        return [(primary, ipaddress.ip_network("127.0.0.1/32", strict=False))]

    for ip, prefix in _interface_pairs():
        if ip != primary:
            continue
        try:
            addr = ipaddress.ip_address(ip)
            if (addr.is_loopback or addr.is_link_local or addr.is_multicast
                    or not addr.is_private):
                break
            if prefix < 21 or prefix > 30:
                prefix = 24
            return [(ip, ipaddress.ip_network(f"{ip}/{prefix}", strict=False))]
        except ValueError:
            break

    # Fallback only when the OS interface listing cannot match the routed address.
    # Do not add every adapter: that would reintroduce virtual-adapter duplicates.
    try:
        addr = ipaddress.ip_address(primary)
        if addr.is_private:
            return [(primary, ipaddress.ip_network(f"{primary}/24", strict=False))]
    except ValueError:
        pass
    return []


def default_gateway(self_ip):
    system = platform.system()
    base = ".".join(self_ip.split(".")[:3]) + "."
    if system == "Windows":
        out = run(["route", "print", "-4", "0.0.0.0"])
        rows = re.findall(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)",
                          out, re.M)
        for gw, iface in rows:
            if iface == self_ip:
                return gw
        for gw, _ in rows:
            if gw.startswith(base):
                return gw
        return rows[0][0] if rows else None
    if system == "Darwin":
        out = run(["route", "-n", "get", "default"])
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", out)
    else:
        out = run(["ip", "route", "show", "default"])
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def norm_mac(raw):
    return ":".join(part.zfill(2) for part in re.split(r"[:-]", raw)).upper()


def read_arp(nets=None):
    """ip -> MAC from the OS neighbour table. Every device that answered ARP is listed here,
    even when it ignores ping and has every port closed."""
    found = {}
    out = run(["arp", "-a"], 8)
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\)?\s+(?:at\s+)?" + MAC_RE, out):
        found[ip] = norm_mac(mac)
    out = run(["ip", "neigh"], 4)
    for line in out.splitlines():
        if re.search(r"FAILED|INCOMPLETE", line):
            continue
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+dev\s+\S+\s+lladdr\s+" + MAC_RE, line)
        if m:
            found[m.group(1)] = norm_mac(m.group(2))
    result = {}
    for ip, mac in found.items():
        if mac.startswith(("FF:FF:FF", "01:00:5E", "33:33")) or mac == "00:00:00:00:00:00":
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if nets is not None and not any(
                addr in n and addr not in (n.network_address, n.broadcast_address) for n in nets):
            continue
        result[ip] = mac
    return result


def ping(ip, timeout_ms=900):
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-W", str(timeout_ms), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    out = run(cmd, timeout=5)
    m = re.search(r"ttl=(\d+)", out, re.I)
    if not m:
        return None
    t = re.search(r"time[=<]\s*([\d.]+)\s*ms", out, re.I)
    return int(m.group(1)), float(t.group(1)) if t else None


def tcp_alive(ip):
    """True if the host answers a TCP connect: either accepts it or actively refuses it."""
    for port in ALIVE_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.25)
        try:
            r = s.connect_ex((ip, port))
        except OSError:
            r = -1
        finally:
            s.close()
        if r == 0 or r in REFUSED:
            return True
    return False


# NetBIOS node status request ("what are your names?"); Windows PCs, NAS boxes and printers answer it.
NBSTAT_QUERY = (b"\xa2\x48\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x20" + b"CK" + b"A" * 30 +
                b"\x00\x00\x21\x00\x01")

MDNS_QUERY = (b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
              b"\x09_services\x07_dns-sd\x04_udp\x05local\x00\x00\x0c\x00\x01")
SSDP_QUERY = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
              b"MX: 2\r\nST: ssdp:all\r\n\r\n")


def parse_nbstat(data):
    """First unique NetBIOS name from a node status reply, or None."""
    try:
        count = data[56]
        off = 57
        for _ in range(count):
            name = data[off:off + 15].decode("latin-1").strip()
            suffix, flags = data[off + 15], data[off + 16]
            if suffix == 0 and not flags & 0x80 and name:
                return name
            off += 18
    except (IndexError, UnicodeDecodeError):
        pass
    return None


def nbstat_sweep(ips, seconds=2.5):
    """Send a NetBIOS query to every address and collect replies. Returns {ip: name_or_None}."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            s.ioctl(socket.SIO_UDP_CONNRESET, False)  # Windows: don't raise on ICMP "port unreachable"
        except (AttributeError, OSError, ValueError):
            pass
        s.settimeout(0.2)
        for ip in ips:
            try:
                s.sendto(NBSTAT_QUERY, (ip, 137))
            except OSError:
                pass
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                continue
            found[addr[0]] = parse_nbstat(data) if len(data) > 60 else found.get(addr[0])
    finally:
        s.close()
    return found


def multicast_probe(self_ip, net, seconds=3.0):
    """Ask the network with mDNS and SSDP and return the addresses that replied."""
    found = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        try:
            s.ioctl(socket.SIO_UDP_CONNRESET, False)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            s.bind((self_ip, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self_ip))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            return found
        s.settimeout(0.3)
        for payload, dest in ((MDNS_QUERY, ("224.0.0.251", 5353)), (SSDP_QUERY, ("239.255.255.250", 1900))):
            try:
                s.sendto(payload, dest)
            except OSError:
                pass
        end = time.time() + seconds
        resent = False
        while time.time() < end:
            if not resent and end - time.time() < seconds / 2:
                resent = True
                for payload, dest in ((MDNS_QUERY, ("224.0.0.251", 5353)), (SSDP_QUERY, ("239.255.255.250", 1900))):
                    try:
                        s.sendto(payload, dest)
                    except OSError:
                        pass
            try:
                _data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                if ipaddress.ip_address(addr[0]) in net:
                    found.add(addr[0])
            except ValueError:
                pass
    finally:
        s.close()
    return found


def port_open(ip, port, timeout=0.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def http_peek(ip, port):
    data = b""
    try:
        with socket.create_connection((ip, port), timeout=0.8) as s:
            s.settimeout(0.8)
            s.sendall(f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: NetworkGlobe\r\n\r\n".encode())
            try:
                while len(data) < 6000:
                    chunk = s.recv(2048)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass
    except OSError:
        return {}
    text = data.decode("latin-1", "replace")
    server = re.search(r"^Server:\s*(.+?)\s*$", text, re.I | re.M)
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return {"server": server.group(1)[:80] if server else None,
            "title": " ".join(title.group(1).split())[:80] if title else None}


def mac_is_random(mac):
    return bool(mac) and bool(int(mac[:2], 16) & 0x02)


def guess_os(d):
    """Best guess from ping TTL and open ports. Returns (label, basis)."""
    if d["kind"] == "self":
        return f"{platform.system()} {platform.release()}", "this computer"
    probe = d.get("probe")
    ports = set(probe["ports"]) if probe else set()
    ttl = d.get("ttl")
    basis = []
    if ttl:
        basis.append(f"ping TTL {ttl}")
    else:
        basis.append("no reply to ping")
    if ports:
        basis.append("open ports " + ", ".join(str(p) for p in sorted(ports)))
    if 62078 in ports:
        label = "iPhone or iPad (iOS)"
    elif 3389 in ports or {135, 445} <= ports:
        label = "Windows"
    elif 5555 in ports:
        label = "Android"
    elif ports & {8008, 8009}:
        label = "Chromecast or Google TV"
    elif ports & {9100, 631}:
        label = "Printer"
    elif 554 in ports:
        label = "IP camera or video recorder"
    elif ports & {548, 7000}:
        label = "Apple device (Mac, Apple TV or AirPlay speaker)"
    elif d["kind"] == "gateway":
        label = "Router"
    elif 22 in ports and ttl and ttl <= 64:
        label = "Linux or Unix"
    elif ttl:
        if ttl <= 64:
            label = "Linux, Android, macOS or iOS"
        elif ttl <= 128:
            label = "Windows"
        else:
            label = "Network gear (router, switch or access point)"
    else:
        label = "Unknown"
    if mac_is_random(d.get("mac")) and label in ("Unknown", "Linux, Android, macOS or iOS"):
        label = "Probably a phone or tablet"
        basis.append("randomized MAC address")
    return label, ", ".join(basis)


# ============================================================
# WORKER THREADS
# ============================================================

# ============================================================
# NODE.JS NETWORK WORKER BRIDGE
# ============================================================

NODE_HELPER = SCRIPT_DIR / "Netscanner_helper.js"


def _node_executable():
    """Find Node without assuming a fixed USB drive letter."""
    configured = os.environ.get("POCKETTERM_NODE")
    candidates = [configured, "node"]
    # PocketTerm portable layouts can place Node beside its other runtimes.
    for base in (SCRIPT_DIR, SCRIPT_DIR.parent, SCRIPT_DIR.parent.parent):
        candidates.extend((str(base / "Node" / "node.exe"),
                           str(base / "node.exe"),
                           str(base / "Runtime" / "node.exe")))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True,
                                    text=True, timeout=3, creationflags=NO_WINDOW)
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _node_request(action, payload, timeout=120):
    node = _node_executable()
    if not node:
        raise RuntimeError("Node.js was not found. Install Node or set POCKETTERM_NODE to node.exe.")
    if not NODE_HELPER.is_file():
        raise RuntimeError(f"Missing helper file: {NODE_HELPER}")
    request = json.dumps({"action": action, "payload": payload})
    proc = subprocess.run([node, str(NODE_HELPER)], input=request, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Node helper exited with code {proc.returncode}")
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Node helper returned invalid JSON: " + proc.stdout[:300]) from exc
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Unknown Node helper error"))
    return response.get("result", {})


class ScanThread(QThread):
    hostFound = Signal(dict)
    hostName = Signal(str, str)
    progress = Signal(int, int)
    error = Signal(str)

    def __init__(self, interfaces, parent=None):
        super().__init__(parent)
        self.interfaces = list(interfaces)

    def run(self):
        try:
            payload = [{"ip": ip, "cidr": net.with_prefixlen} for ip, net in self.interfaces]
            result = _node_request("scan", payload, timeout=180)
            for item in result.get("hosts", []):
                info = dict(item)
                name = info.pop("name", None)
                ip = info.get("ip")
                if ip:
                    self.hostFound.emit(info)
                    if name:
                        self.hostName.emit(ip, name)
            self.progress.emit(1000, 1000)
        except Exception as exc:
            self.error.emit(str(exc))


class NameThread(QThread):
    resolved = Signal(str, str)

    def __init__(self, ips, parent=None):
        super().__init__(parent)
        self.ips = list(ips)

    def run(self):
        try:
            result = _node_request("resolve", self.ips, timeout=30)
            for ip, name in result.get("names", {}).items():
                if name:
                    self.resolved.emit(ip, name)
        except Exception:
            # Name lookup is optional; the rest of the application remains usable.
            pass


class ProbeThread(QThread):
    done = Signal(str, dict)

    def __init__(self, ip, is_self, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.is_self = is_self

    def run(self):
        try:
            result = _node_request("probe", {"ip": self.ip, "isSelf": self.is_self}, timeout=45)
        except Exception:
            result = {"hostname": socket.gethostname() if self.is_self else None,
                      "ports": {}, "web": None}
        self.done.emit(self.ip, result)


# ============================================================
# POCKETTERM NETWORK CENTER - local history and device intelligence
# ============================================================
DATA_DIR = SCRIPT_DIR / "NetworkData"
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / "network_state.json"


def identify_device(device):
    """Conservative device guess based on hostname, vendor and observed services."""
    name = ((device.get("name") or "") + " " +
            ((device.get("probe") or {}).get("hostname") or "")).lower()
    vendor = (OUI.get((device.get("mac") or "")[:8]) or "").lower()
    ports = set(map(int, ((device.get("probe") or {}).get("ports") or {}).keys()))
    if any(x in name for x in ("iphone", "ipad")) or "apple" in vendor or 62078 in ports:
        return "Apple device"
    if any(x in name for x in ("android", "galaxy", "pixel")) or "samsung" in vendor or 5555 in ports:
        return "Android device"
    if any(x in name for x in ("tv", "roku", "firestick", "chromecast")) or ports & {7000,8008,8009,554}:
        return "TV / media device"
    if ports & {9100,631}:
        return "Printer"
    if ports & {445,135,139,3389}:
        return "Windows / PC"
    if ports & {22}:
        return "Linux / network device"
    if "router" in name or "gateway" in name:
        return "Router"
    return "Unknown device"


# ============================================================
# GLOBE
# ============================================================

class GlobeWidget(QWidget):
    pointClicked = Signal(str)

    def __init__(self, devices):
        super().__init__()
        self.devices = devices          # shared with the main window
        self.setMouseTracking(True)
        self.setMinimumSize(300, 300)
        self.setCursor(Qt.OpenHandCursor)
        self.default_tilt = math.radians(18)
        self.yaw, self.tilt, self.tilt_target = 0.0, self.default_tilt, self.default_tilt
        self.spin = 0.0035
        self.selected = None
        self.hover = None
        self.focus_yaw = None
        self.dragging = False
        self._press = None
        self._last = QPointF()
        self.hits = []
        self.caption = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ----- state -----
    def select(self, ip):
        self.selected = ip or None
        d = self.devices.get(ip)
        if d:
            self.focus_yaw = -d["lon_rad"]
            self.tilt_target = max(-0.9, min(0.9, d["lat_rad"]))
        else:
            self.focus_yaw = None
            self.tilt_target = self.default_tilt

    def _tick(self):
        if not self.dragging:
            if self.focus_yaw is not None:
                diff = (self.focus_yaw - self.yaw + math.pi) % (2 * math.pi) - math.pi
                self.yaw += diff * 0.10
                if abs(diff) < 0.001:
                    self.yaw, self.focus_yaw = self.focus_yaw, None
            elif self.selected is None:
                self.yaw += self.spin
        self.yaw %= 2 * math.pi
        self.tilt += (self.tilt_target - self.tilt) * 0.08
        self.update()

    # ----- input -----
    def _hit(self, pos):
        best, best_d = None, 13.0
        for ip, sx, sy in self.hits:
            dist = math.hypot(pos.x() - sx, pos.y() - sy)
            if dist < best_d:
                best, best_d = ip, dist
        return best

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position()
            self._last = e.position()
            self.dragging = False

    def mouseMoveEvent(self, e):
        pos = e.position()
        if (e.buttons() & Qt.LeftButton) and self._press is not None:
            if not self.dragging and (pos - self._press).manhattanLength() > 4:
                self.dragging = True
                self.focus_yaw = None
            if self.dragging:
                self.yaw += (pos.x() - self._last.x()) * 0.008
                self._last = pos
            return
        hov = self._hit(pos)
        if hov != self.hover:
            self.hover = hov
            self.setCursor(Qt.PointingHandCursor if hov else Qt.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            was_drag = self.dragging
            self.dragging = False
            self._press = None
            if not was_drag:
                self.pointClicked.emit(self._hit(e.position()) or "")

    # ----- drawing -----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(BACKGROUND))
        cx, cy = w / 2, h / 2 - 8
        R = max(100.0, min(w, h) * 0.38)

        cyaw, syaw = math.cos(self.yaw), math.sin(self.yaw)
        ct, st = math.cos(self.tilt), math.sin(self.tilt)

        # soft white rim light
        glow = QRadialGradient(cx, cy, R * 1.3)
        glow.setColorAt(0.0, QColor(255, 255, 255, 0))
        glow.setColorAt(0.74, QColor(255, 255, 255, 0))
        glow.setColorAt(0.77, QColor(255, 255, 255, 38))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), R * 1.3, R * 1.3)

        # sphere body
        body = QRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 1.5)
        body.setColorAt(0.0, QColor(26, 26, 30))
        body.setColorAt(0.6, QColor(4, 4, 6))
        body.setColorAt(1.0, QColor(0, 0, 0))
        p.setBrush(QBrush(body))
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # graticule (front half only)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(Qt.NoBrush)
        for line in GRATICULE:
            path = QPainterPath()
            pen_down = False
            for x0, y0, z0 in line:
                x = x0 * cyaw + z0 * syaw
                z1 = z0 * cyaw - x0 * syaw
                y = y0 * ct - z1 * st
                z = y0 * st + z1 * ct
                if z > 0:
                    pt = QPointF(cx + R * x, cy - R * y)
                    if pen_down:
                        path.lineTo(pt)
                    else:
                        path.moveTo(pt)
                        pen_down = True
                else:
                    pen_down = False
            p.drawPath(path)

        # land dots, grouped by depth so each group is one draw call
        bins = ([], [], [], [])
        for x0, y0, z0 in LAND_XYZ:
            x = x0 * cyaw + z0 * syaw
            z1 = z0 * cyaw - x0 * syaw
            y = y0 * ct - z1 * st
            z = y0 * st + z1 * ct
            b = 0 if z < 0 else 1 if z < 0.25 else 2 if z < 0.6 else 3
            bins[b].append(QPointF(cx + R * x, cy - R * y))
        for pts, alpha, size in zip(bins, (16, 95, 175, 255), (1.6, 2.0, 2.5, 2.9)):
            p.setPen(QPen(QColor(255, 255, 255, alpha), size, Qt.SolidLine, Qt.RoundCap))
            p.drawPoints(QPolygonF(pts))

        # devices
        pulse = (math.sin(time.time() * 4) + 1) / 2
        self.hits = []
        front, back = [], []
        for ip, d in list(self.devices.items()):
            x0, y0, z0 = d["xyz"]
            x = x0 * cyaw + z0 * syaw
            z1 = z0 * cyaw - x0 * syaw
            y = y0 * ct - z1 * st
            z = y0 * st + z1 * ct
            (front if z > 0 else back).append((ip, d, cx + R * x, cy - R * y, z))
        for ip, d, sx, sy, z in back:
            p.setPen(QPen(QColor(139, 92, 246, 50), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawPoint(QPointF(sx, sy))
        front.sort(key=lambda item: item[0] == self.selected)
        for ip, d, sx, sy, z in front:
            self.hits.append((ip, sx, sy))
            base = QColor(OK_COLOR if d["kind"] == "self" else WARN_COLOR if d["kind"] == "gateway" else ACCENT)
            sel = ip == self.selected
            fade = 0.45 + 0.55 * min(1.0, z * 1.6)
            halo = QRadialGradient(sx, sy, 16)
            c0 = QColor(base)
            c0.setAlpha(int(110 * fade))
            c1 = QColor(base)
            c1.setAlpha(0)
            halo.setColorAt(0, c0)
            halo.setColorAt(1, c1)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(QPointF(sx, sy), 16, 16)
            core = QColor(LIGHTER) if sel else QColor(base)
            core.setAlpha(int(255 * fade))
            p.setBrush(core)
            p.drawEllipse(QPointF(sx, sy), 5.0 if sel else 4.0, 5.0 if sel else 4.0)
            if sel:
                ring = QColor(LIGHTER)
                ring.setAlpha(int(70 + 150 * (1 - pulse)))
                p.setPen(QPen(ring, 1.5))
                p.setBrush(Qt.NoBrush)
                r = 9 + 5 * pulse
                p.drawEllipse(QPointF(sx, sy), r, r)

        # labels for hovered and selected points
        for ip in {self.hover, self.selected} - {None}:
            hit = next(((sx, sy) for i, sx, sy in self.hits if i == ip), None)
            if hit and ip in self.devices:
                self._label(p, ip, hit[0], hit[1], w)

        # caption
        p.setPen(QColor(TEXT_FAINT))
        f = QFont(self.font())
        f.setPointSizeF(9)
        p.setFont(f)
        p.drawText(QRectF(24, h - 44, w - 48, 30), Qt.AlignLeft | Qt.AlignVCenter,
                   "Point positions are arbitrary. Devices on a local network have no real-world location.")
        if self.caption:
            p.setPen(QColor(TEXT_MUTED))
            p.drawText(QRectF(24, 18, w - 48, 24), Qt.AlignLeft | Qt.AlignVCenter, self.caption)

    def _label(self, p, ip, x, y, w):
        d = self.devices[ip]
        name = {"self": "this PC", "gateway": "router"}.get(d["kind"], "") or (d.get("name") or "")
        text = f"{ip}   {name}" if name else ip
        f = QFont(self.font())
        f.setPointSizeF(10)
        f.setBold(True)
        p.setFont(f)
        tw = QFontMetrics(f).horizontalAdvance(text) + 22
        rx = min(x + 14, w - tw - 8)
        rect = QRectF(rx, y - 34, tw, 26)
        p.setPen(QPen(QColor(HOVER_BORDER), 1))
        p.setBrush(QColor("#130e21"))
        p.drawRoundedRect(rect, 7, 7)
        p.setPen(QColor(TEXT))
        p.drawText(rect, Qt.AlignCenter, text)


# ============================================================
# MAIN WINDOW
# ============================================================

DETAIL_ROWS = ["Hostname", "MAC address", "Address type", "Vendor", "Operating system", "Basis",
               "Latency", "Open ports", "Web page"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Network Globe")
        self.resize(1280, 800)
        self.devices = {}
        self.used = set()
        self.selected = None
        self.scan = None
        self.name_thread = None
        self.probes = []
        self.interfaces = local_networks()
        self.self_ips = {ip for ip, _ in self.interfaces}
        self.self_ip = self.interfaces[0][0] if self.interfaces else "127.0.0.1"
        self.nets = [n for _, n in self.interfaces]
        self.gateways = set()
        self.history = []
        self.previous_scan = set()
        self.last_new = []
        self.last_gone = []
        self.events = []
        self.saved_profiles = {}
        self.device_labels = {}
        self._load_state()

        root = QWidget()
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.globe = GlobeWidget(self.devices)
        self.globe.pointClicked.connect(self._on_point_clicked)
        row.addWidget(self.globe, 1)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(380)
        row.addWidget(panel)
        col = QVBoxLayout(panel)
        col.setContentsMargins(22, 22, 22, 22)
        col.setSpacing(12)

        title = QLabel("Network Globe")
        title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Subtitle")
        col.addWidget(title)
        col.addWidget(self.subtitle)

        self.scan_btn = QPushButton("Scan network")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.clicked.connect(self.start_scan)
        col.addWidget(self.scan_btn)
        tools_row = QHBoxLayout()
        for caption, handler in (("Network Center", self.open_center), ("Export", self.export_devices),
                                 ("Label device", self.label_selected)):
            b = QPushButton(caption)
            b.clicked.connect(handler)
            tools_row.addWidget(b)
        col.addLayout(tools_row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        col.addWidget(self.bar)

        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.currentItemChanged.connect(self._on_list_changed)
        col.addWidget(self.list, 1)

        card = QFrame()
        card.setObjectName("Card")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(16, 14, 16, 14)
        card_l.setSpacing(8)
        self.empty = QLabel("Select a point on the globe, or a device in the list, to see its details.")
        self.empty.setObjectName("Subtitle")
        self.empty.setWordWrap(True)
        card_l.addWidget(self.empty)

        self.detail = QWidget()
        self.detail.setStyleSheet("background: transparent;")
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        self.detail_ip = QLabel("")
        self.detail_ip.setObjectName("DetailIp")
        self.detail_ip.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.detail_ip)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.values = {}
        for i, name in enumerate(DETAIL_ROWS):
            k = QLabel(name)
            k.setObjectName("Key")
            k.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            v = QLabel("")
            v.setObjectName("Value")
            v.setWordWrap(True)
            v.setTextFormat(Qt.PlainText)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)
            self.values[name] = v
        grid.setColumnMinimumWidth(0, 108)
        grid.setColumnStretch(1, 1)
        dl.addLayout(grid)
        note = QLabel("The OS is a best guess from ping TTL and open ports.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        dl.addWidget(note)
        card_l.addWidget(self.detail)
        self.detail.setVisible(False)
        col.addWidget(card)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh_list)

        # Rescan automatically so devices that join or wake up later appear on their own.
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(SCAN_INTERVAL_MS)
        self.auto_timer.timeout.connect(self.start_scan)
        self.auto_timer.start()

        QShortcut(QKeySequence("Esc"), self, activated=self.clear_selection)
        self.setStyleSheet(STYLE)
        self._update_subtitle()
        QTimer.singleShot(300, self.start_scan)

    # ----- Network Center persistence and tools -----
    def _load_state(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.history = data.get("history", [])[-500:]
            self.device_labels = data.get("labels", {})
            self.saved_profiles = data.get("profiles", {})
            self.previous_scan = set(data.get("previous_scan", []))
            self.last_new = data.get("last_new", [])
            self.last_gone = data.get("last_gone", [])
        except Exception:
            self.history, self.device_labels, self.saved_profiles, self.previous_scan = [], {}, {}, set()
            self.last_new, self.last_gone = [], []

    def _save_state(self):
        data = {"history": self.history[-500:], "labels": self.device_labels,
                "profiles": self.saved_profiles, "previous_scan": sorted(self.previous_scan),
                "last_new": self.last_new, "last_gone": self.last_gone}
        try:
            STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self.subtitle.setText("Could not save network history: " + str(exc)[:100])

    def _device_title(self, ip, d):
        return self.device_labels.get(ip) or d.get("name") or identify_device(d)

    def label_selected(self):
        if not self.selected or self.selected not in self.devices:
            QMessageBox.information(self, "Label device", "Select a device first.")
            return
        ip = self.selected
        current = self.device_labels.get(ip, "")
        label, ok = QInputDialog.getText(self, "Device label", f"Friendly name for {ip}:", text=current)
        if ok and label.strip():
            self.device_labels[ip] = label.strip()[:64]
            self._save_state(); self._refresh_list(); self._render_details()

    def export_devices(self):
        if not self.devices:
            QMessageBox.information(self, "Export", "There are no discovered devices to export yet.")
            return
        path, chosen = QFileDialog.getSaveFileName(self, "Export network inventory", "PocketTerm_Network.csv",
                                                   "CSV (*.csv);;JSON (*.json)")
        if not path: return
        rows = []
        for ip, d in sorted(self.devices.items(), key=lambda x: ipaddress.ip_address(x[0])):
            rows.append({"ip": ip, "label": self._device_title(ip, d), "hostname": d.get("name"),
                         "mac": d.get("mac"), "vendor": OUI.get((d.get("mac") or "")[:8]),
                         "type_guess": identify_device(d), "online": bool(d.get("seen")),
                         "latency_ms": d.get("rtt"), "last_seen": d.get("last_seen")})
        try:
            if path.lower().endswith(".json"):
                Path(path).write_text(json.dumps(rows, indent=2), encoding="utf-8")
            else:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            QMessageBox.information(self, "Export", f"Exported {len(rows)} devices.")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _network_doctor(self):
        lines = ["INTERFACES"]
        if not self.interfaces:
            lines.append("No active private IPv4 interface detected.")
        for ip, network in self.interfaces:
            gw = default_gateway(ip)
            lines.append(f"• {ip} / {network} — gateway: {gw or 'not detected'}")
            if gw:
                try:
                    sock = socket.create_connection((gw, 80), timeout=1.2); sock.close()
                    lines.append("  Gateway TCP/80: reachable")
                except OSError:
                    lines.append("  Gateway TCP/80: no response (not necessarily a fault)")
        lines.append("\nDNS")
        try:
            lines.append("example.com resolves to " + ", ".join(socket.gethostbyname_ex("example.com")[2][:3]))
        except OSError as exc:
            lines.append("DNS lookup failed: " + str(exc))
        lines.append("\nINTERNET")
        try:
            sock = socket.create_connection(("1.1.1.1", 443), timeout=2); sock.close()
            lines.append("TCP connection to 1.1.1.1:443 succeeded.")
        except OSError as exc:
            lines.append("Internet reachability check failed: " + str(exc))
        lines.append("\nDISCOVERY")
        lines.append(f"Currently tracked devices: {len(self.devices)}")
        lines.append("Some devices may hide from discovery, sleep, or be isolated by the Wi-Fi access point.")
        return "\n".join(lines)

    def open_center(self):
        dlg = QDialog(self); dlg.setWindowTitle("PocketTerm Network Center"); dlg.resize(850, 620)
        layout = QVBoxLayout(dlg); tabs = QTabWidget(); layout.addWidget(tabs)
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Label / name", "IP", "Type guess", "MAC", "Latency", "Status"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for ip, d in sorted(self.devices.items()):
            r = table.rowCount(); table.insertRow(r)
            vals = [self._device_title(ip, d), ip, identify_device(d), d.get("mac") or "—",
                    (f"{d['rtt']:.1f} ms" if d.get("rtt") is not None else "—"),
                    ("Online" if d.get("seen") else "Offline / missed")]
            for c, value in enumerate(vals): table.setItem(r, c, QTableWidgetItem(str(value)))
        tabs.addTab(table, "Device inventory")

        topo = QTextEdit(); topo.setReadOnly(True)
        lines = ["NETWORK TOPOLOGY (logical view)", "",
                 f"Local network: {', '.join(str(n) for n in self.nets) or 'unknown'}"]
        for gw in sorted(self.gateways): lines.append(f"  ROUTER / GATEWAY  {gw}")
        for ip, d in sorted(self.devices.items()):
            if ip not in self.gateways:
                lines.append(f"    ├── {self._device_title(ip, d)}  [{ip}]  ({identify_device(d)})")
        lines += ["", "Links show subnet membership, not verified physical Wi-Fi connections."]
        topo.setPlainText("\n".join(lines)); tabs.addTab(topo, "Topology")

        hist = QTextEdit(); hist.setReadOnly(True)
        hist.setPlainText("\n".join(f"{e.get('time','')}  {e.get('event','')}  {e.get('ip','')}"
                                     for e in reversed(self.history[-200:])) or "No scan history yet.")
        tabs.addTab(hist, "History & timeline")

        doctor = QWidget(); dv = QVBoxLayout(doctor)
        report = QTextEdit(); report.setReadOnly(True); report.setPlainText(self._network_doctor()); dv.addWidget(report)
        refresh = QPushButton("Run diagnostics again")
        refresh.clicked.connect(lambda: report.setPlainText(self._network_doctor())); dv.addWidget(refresh)
        tabs.addTab(doctor, "Network Doctor")

        prof = QWidget(); pv = QVBoxLayout(prof)
        profile_list = QComboBox(); profile_list.addItems(sorted(self.saved_profiles)); pv.addWidget(profile_list)
        name = QLineEdit(); name.setPlaceholderText("Profile name (e.g. Home Wi-Fi)"); pv.addWidget(name)
        def save_profile():
            key = name.text().strip()
            if not key: return
            self.saved_profiles[key] = [str(n) for n in self.nets]; self._save_state()
            profile_list.clear(); profile_list.addItems(sorted(self.saved_profiles)); profile_list.setCurrentText(key)
            QMessageBox.information(dlg, "Profiles", f"Saved {key}. Profiles record network ranges; scanning remains limited to the active private LAN.")
        save = QPushButton("Save current network as profile"); save.clicked.connect(save_profile); pv.addWidget(save)
        tabs.addTab(prof, "Profiles")

        comp = QTextEdit(); comp.setReadOnly(True)
        current = {ip for ip, d in self.devices.items() if d.get("seen")}
        new = self.last_new; gone = self.last_gone
        comp.setPlainText("SINCE THE PREVIOUS RECORDED SCAN\n\nNewly observed:\n" + ("\n".join(new) or "None") +
                          "\n\nNot seen in this scan:\n" + ("\n".join(gone) or "None") +
                          "\n\nThis comparison uses the last completed scan snapshot.")
        tabs.addTab(comp, "Scan comparison")
        layout.addWidget(QLabel("Automatic discovery is limited to the active private LAN. Device-type guesses are best-effort."))
        dlg.exec()

    # ----- devices -----
    def _ensure_device(self, ip):
        d = self.devices.get(ip)
        if d is None:
            d = {"ip": ip, "kind": "device", "ttl": None, "rtt": None, "mac": None, "probe": None,
                 "name": socket.gethostname() if ip in self.self_ips else None,
                 "seen": True, "missed": 0}
            d.update(place_device(ip, self.used))
            d.setdefault("last_seen", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.devices[ip] = d
        return d

    def _classify(self, d):
        d["kind"] = "self" if d["ip"] in self.self_ips else "gateway" if d["ip"] in self.gateways else "device"

    def start_scan(self):
        if self.scan and self.scan.isRunning():
            return
        if self.self_ip.startswith("127."):
            self.subtitle.setText("No network connection found.")
        # Re-read the networks every scan: WiFi, Ethernet, VPN and virtual adapters can come and go.
        self.interfaces = local_networks()
        self.self_ips = {ip for ip, _ in self.interfaces}
        self.nets = [n for _, n in self.interfaces]
        self.gateways = {g for g in (default_gateway(ip) for ip in self.self_ips) if g}
        if not self.interfaces:
            self.subtitle.setText("No active private IPv4 network found.")
            self.scan_btn.setText("Scan network")
            self.scan_btn.setEnabled(True)
            self.bar.setVisible(False)
            return
        for d in self.devices.values():
            d["seen"] = False
        for my_ip in self.self_ips:
            me = self._ensure_device(my_ip)
            me["seen"] = True
        for d in self.devices.values():
            self._classify(d)
        self._refresh_list()
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.scan = ScanThread(self.interfaces, self)
        self.scan.hostFound.connect(self._on_host)
        self.scan.hostName.connect(self._on_name)
        self.scan.progress.connect(self._on_progress)
        self.scan.error.connect(self._on_scan_error)
        self.scan.finished.connect(self._on_scan_done)
        self.scan.start()

    def _on_host(self, info):
        d = self._ensure_device(info["ip"])
        was_seen = d.get("seen", False)
        d["seen"] = True
        d["missed"] = 0
        d["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if not was_seen and ipaddress.ip_address(info["ip"]).is_private and info["ip"] not in self.self_ips:
            self.history.append({"time": d["last_seen"], "event": "Device discovered / online", "ip": info["ip"]})
        for key in ("ttl", "rtt", "mac"):
            if info.get(key) is not None:
                d[key] = info[key]
        self._classify(d)
        self.refresh_timer.start(200)

    def _on_name(self, ip, name):
        d = self.devices.get(ip)
        if d is not None and name and not d.get("name"):
            d["name"] = name
            self.refresh_timer.start(200)
            if ip == self.selected:
                self._render_details()

    def _on_progress(self, done, total):
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)

    def _on_scan_error(self, message):
        self.subtitle.setText("Scan error: " + message[:180])

    def _on_scan_done(self):
        # A device is only dropped after it was missed by several scans in a row (phones sleep).
        current_snapshot = {ip for ip, d in self.devices.items() if d.get("seen")}
        for ip in sorted(current_snapshot - self.previous_scan):
            if ip not in self.self_ips:
                self.history.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "New device", "ip": ip})
        for ip in sorted(self.previous_scan - current_snapshot):
            if ip not in self.self_ips:
                self.history.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "Device not seen", "ip": ip})
        self.last_new = sorted(current_snapshot - self.previous_scan)
        self.last_gone = sorted(self.previous_scan - current_snapshot)
        self.previous_scan = current_snapshot
        if self.last_new:
            self.subtitle.setText(f"{len(self.last_new)} new device(s) detected")
        self._save_state()
        for ip in [ip for ip, d in self.devices.items() if not d["seen"]]:
            d = self.devices[ip]
            d["missed"] = d.get("missed", 0) + 1
            if d["missed"] >= MISSES_BEFORE_REMOVAL:
                del self.devices[ip]
        if self.selected not in self.devices:
            self.clear_selection()
        self.bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan again")
        self._refresh_list()
        if self.selected:
            self._render_details()
        # Look up names for every device that doesn't have one yet.
        if not (self.name_thread and self.name_thread.isRunning()):
            todo = [ip for ip, d in self.devices.items() if not d.get("name") and d["kind"] != "self"]
            if todo:
                self.name_thread = NameThread(todo, self)
                self.name_thread.resolved.connect(self._on_name)
                self.name_thread.start()

    def _update_subtitle(self):
        n = len(self.devices)
        label = "device" if n == 1 else "devices"
        nets = ", ".join(str(x) for x in self.nets[:3]) + (f" +{len(self.nets) - 3}" if len(self.nets) > 3 else "")
        self.subtitle.setText(f"{nets}  -  {n} {label}")

    def _refresh_list(self):
        order = sorted(self.devices, key=lambda ip: ipaddress.ip_address(ip))
        self.list.blockSignals(True)
        self.list.clear()
        for ip in order:
            d = self.devices[ip]
            item = QListWidgetItem()
            item.setData(Qt.UserRole, ip)
            item.setSizeHint(QRectF(0, 0, 10, 52).size().toSize())
            self.list.addItem(item)
            self.list.setItemWidget(item, self._row_widget(ip, d))
            if ip == self.selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self._update_subtitle()

    def _row_widget(self, ip, d):
        w = QWidget()
        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(0)
        a = QLabel(ip)
        a.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
        name = (d["probe"].get("hostname") if d.get("probe") else None) or d.get("name")
        kind = {"self": "This PC", "gateway": "Router"}.get(d["kind"], "")
        sub = " / ".join(x for x in (kind, self.device_labels.get(ip), name, identify_device(d)) if x) or ("Device" if not d.get("mac") else d["mac"])
        b = QLabel(sub)
        b.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        lay.addWidget(a)
        lay.addWidget(b)
        return w

    # ----- selection -----
    def _on_point_clicked(self, ip):
        if ip:
            self.select(ip)
        else:
            self.clear_selection()

    def _on_list_changed(self, current, _prev):
        if current is not None:
            self.select(current.data(Qt.UserRole), from_list=True)

    def select(self, ip, from_list=False):
        if ip not in self.devices:
            return
        self.selected = ip
        self.globe.select(ip)
        if not from_list:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == ip:
                    self.list.blockSignals(True)
                    self.list.setCurrentRow(i)
                    self.list.blockSignals(False)
                    break
        d = self.devices[ip]
        if d["probe"] is None and not getattr(d.get("thread"), "isRunning", lambda: False)():
            t = ProbeThread(ip, d["kind"] == "self", self)
            t.done.connect(self._on_probe)
            d["thread"] = t
            self.probes.append(t)
            t.start()
        self._render_details()

    def clear_selection(self):
        self.selected = None
        self.globe.select("")
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self.detail.setVisible(False)
        self.empty.setVisible(True)

    def _on_probe(self, ip, result):
        d = self.devices.get(ip)
        if d is None:
            return
        d["probe"] = result
        d.pop("thread", None)
        self.refresh_timer.start(100)
        if ip == self.selected:
            self._render_details()

    def _render_details(self):
        d = self.devices.get(self.selected)
        if not d:
            return
        self.empty.setVisible(False)
        self.detail.setVisible(True)
        kind = {"self": "  (this PC)", "gateway": "  (router)"}.get(d["kind"], "")
        self.detail_ip.setText(d["ip"] + kind)
        probe = d.get("probe")
        wait = "Checking..."
        v = self.values
        mac = d.get("mac")
        known_name = (probe["hostname"] if probe else None) or d.get("name")
        v["Hostname"].setText(known_name or ("Not announced" if probe else wait))
        if d["kind"] == "self":
            v["MAC address"].setText("Not shown for this PC")
            v["Address type"].setText("-")
            v["Vendor"].setText("-")
        else:
            v["MAC address"].setText(mac or "Not in the ARP table")
            if mac:
                v["Address type"].setText("Randomized (typical of phones and tablets)" if mac_is_random(mac)
                                          else "Hardware address")
                v["Vendor"].setText(OUI.get(mac[:8]) or ("-" if mac_is_random(mac) else "Not in the built-in list"))
            else:
                v["Address type"].setText("-")
                v["Vendor"].setText("-")
        label, basis = guess_os(d)
        v["Operating system"].setText(label if (probe or d["kind"] == "self" or d.get("ttl")) else wait)
        v["Basis"].setText(basis + " | Type: " + identify_device(d))
        v["Latency"].setText(f"{d['rtt']:.0f} ms" if d.get("rtt") is not None else "-")
        if probe:
            ports = probe["ports"]
            v["Open ports"].setText(", ".join(f"{p} {n}" for p, n in sorted(ports.items())) or "None of the common ports")
            web = probe.get("web") or {}
            bits = [b for b in (web.get("title"), f"Server: {web['server']}" if web.get("server") else None) if b]
            v["Web page"].setText(" / ".join(bits) or "-")
        else:
            v["Open ports"].setText(wait)
            v["Web page"].setText(wait)


def main():
    # Without this, Windows groups the app under python.exe and the taskbar shows the Python icon.
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.NetScanner")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    icon_path = find_icon()
    icon = QIcon(str(icon_path)) if icon_path else None
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    if icon is not None and not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()