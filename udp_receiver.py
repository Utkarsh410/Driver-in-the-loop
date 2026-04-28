"""
udp_receiver.py
---------------
Receives ACC telemetry via UDP broadcast (Shared Memory Broadcaster plugin
or ACC's built-in UDP output).  This complements the shared-memory reader
and is useful for:
  • Remote logging over a network (e.g. from a separate analysis PC)
  • Cross-platform testing (works on macOS / Linux)
  • Broadcasting to multiple consumers simultaneously

ACC UDP packet format (simplified, 2024):
  Header: magic(4) + packetId(4) + version(2)
  Payload: varies per packet type

Default port: 9996  (ACC → Tools → UDP Telemetry)

Usage:
    rx = UDPReceiver(port=9996)
    rx.start()
    frame = rx.latest_frame()   # dict or None
    rx.stop()
"""

import socket
import struct
import threading
import time
from typing import Optional, Dict, Any

# ── ACC UDP packet IDs ────────────────────────────────────────────────────────
# These correspond to the packet types defined in the ACC UDP plugin spec.

PKT_PHYSICS   = 1
PKT_GRAPHICS  = 2
PKT_STATIC    = 3
PKT_HANDSHAKE = 99

_PHYSICS_FMT = (
    "i"    # packetId
    "f"    # gas
    "f"    # brake
    "f"    # fuel
    "i"    # gear
    "i"    # rpms
    "f"    # steerAngle
    "f"    # speedKmh
    "3f"   # velocity xyz
    "3f"   # accG xyz
    "4f"   # wheelSlip FL FR RL RR
    "4f"   # wheelLoad
    "4f"   # wheelsPressure
    "4f"   # wheelAngularSpeed
    "4f"   # tyreWear
    "4f"   # tyreDirtyLevel
    "4f"   # tyreCoreTemperature
    "4f"   # camberRAD
    "4f"   # suspensionTravel
    "f"    # drs
    "f"    # tc
    "f"    # heading
    "f"    # pitch
    "f"    # roll
)

_PACKET_SIZES: Dict[int, int] = {}


def _calc_size(fmt: str) -> int:
    """Return byte size of a struct format string."""
    return struct.calcsize("<" + fmt)


class UDPReceiver:
    """
    Listens for ACC UDP telemetry on a given port.
    Thread-safe: latest_frame() returns the most recent decoded packet.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9996,
                 timeout: float = 2.0, buffer_size: int = 4096):
        self._host        = host
        self._port        = port
        self._timeout     = timeout
        self._buf_sz      = buffer_size
        self._sock: Optional[socket.socket] = None
        self._running     = False
        self._lock        = threading.Lock()
        self._latest: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None
        self._rx_count    = 0
        self._last_rx     = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self._host, self._port))
            print(f"[UDP] Listening on {self._host}:{self._port}")
        except OSError as exc:
            print(f"[UDP] Bind error: {exc}. Is another process using port {self._port}?")
            return
        self._sock.settimeout(self._timeout)
        self._running = True
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=3)
        print(f"[UDP] Stopped. Received {self._rx_count} packets.")

    # ── public API ────────────────────────────────────────────────────────────

    def latest_frame(self) -> Optional[Dict[str, Any]]:
        """Return the latest decoded frame or None if nothing received yet."""
        with self._lock:
            return self._latest.copy() if self._latest else None

    @property
    def packet_rate(self) -> float:
        """Estimated packets per second (rolling)."""
        return self._rx_count / max(1.0, time.time() - self._last_rx + 1.0)

    @property
    def is_receiving(self) -> bool:
        return self._running and (time.time() - self._last_rx) < 2.0

    # ── receive loop ──────────────────────────────────────────────────────────

    def _recv_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._sock.recvfrom(self._buf_sz)
                self._last_rx = time.time()
                self._rx_count += 1
                self._dispatch(data, addr)
            except socket.timeout:
                pass
            except OSError:
                if self._running:
                    print("[UDP] Socket error.")
                break

    def _dispatch(self, data: bytes, addr) -> None:
        if len(data) < 6:
            return
        # Minimal header: first 4 bytes magic, next 2 bytes type
        pkt_type = struct.unpack_from("<H", data, 4)[0]
        payload  = data[6:]

        if pkt_type == PKT_PHYSICS:
            frame = self._parse_physics(payload)
        elif pkt_type == PKT_GRAPHICS:
            frame = self._parse_graphics(payload)
        elif pkt_type == PKT_STATIC:
            frame = self._parse_static(payload)
        else:
            return

        if frame:
            frame["_pkt_type"]  = pkt_type
            frame["_timestamp"] = self._last_rx
            frame["_source"]    = addr[0]
            with self._lock:
                if self._latest is None:
                    self._latest = frame
                else:
                    self._latest.update(frame)

    @staticmethod
    def _parse_physics(data: bytes) -> Optional[Dict]:
        fmt = "<" + _PHYSICS_FMT
        sz  = struct.calcsize(fmt)
        if len(data) < sz:
            return None
        vals = struct.unpack_from(fmt, data)
        i = 0
        def _take(n):
            nonlocal i
            res = vals[i:i+n]
            i += n
            return res[0] if n == 1 else list(res)
        return {
            "packetId":         _take(1),
            "throttle":         _take(1),
            "brake":            _take(1),
            "fuel":             _take(1),
            "gear":             _take(1),
            "rpms":             _take(1),
            "steer_angle":      _take(1),
            "speed_kmh":        _take(1),
            "velocity":         _take(3),
            "accG":             _take(3),
            "wheel_slip":       _take(4),
            "wheel_load":       _take(4),
            "tyre_pressure":    _take(4),
            "wheel_angular_speed": _take(4),
            "tyre_wear":        _take(4),
            "tyre_dirty":       _take(4),
            "tyre_core_temp":   _take(4),
            "camber":           _take(4),
            "suspension":       _take(4),
            "drs":              _take(1),
            "tc":               _take(1),
            "heading":          _take(1),
            "pitch":            _take(1),
            "roll":             _take(1),
        }

    @staticmethod
    def _parse_graphics(data: bytes) -> Optional[Dict]:
        """Parse a minimal graphics packet."""
        fmt = "<iii"
        if len(data) < struct.calcsize(fmt):
            return None
        pid, status, session = struct.unpack_from(fmt, data)
        return {"packetId": pid, "status": status, "session": session}

    @staticmethod
    def _parse_static(data: bytes) -> Optional[Dict]:
        """Parse a minimal static packet (car + track string)."""
        try:
            offset = 0
            def _str(n):
                nonlocal offset
                raw = data[offset:offset + n * 2]
                offset += n * 2
                return raw.decode("utf-16-le").rstrip("\x00")
            sm_ver  = _str(15)
            ac_ver  = _str(15)
            return {"sm_version": sm_ver, "ac_version": ac_ver}
        except Exception:
            return None


# ── simple connectivity test ──────────────────────────────────────────────────

def test_udp_connection(port: int = 9996, timeout: float = 5.0) -> bool:
    """
    Block for up to `timeout` seconds waiting for any UDP packet.
    Returns True if data arrives (ACC is broadcasting).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(timeout)
    try:
        data, _ = sock.recvfrom(4096)
        print(f"[UDP] ACC detected – received {len(data)} bytes.")
        return True
    except socket.timeout:
        print(f"[UDP] No ACC data on port {port} within {timeout}s.")
        return False
    finally:
        sock.close()


if __name__ == "__main__":
    test_udp_connection()