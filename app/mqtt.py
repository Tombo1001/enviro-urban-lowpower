"""A small MQTT 3.1.1 client on the standard library.

Two jobs: log every message under a topic filter to a JSONL file, and publish
one retained message. Each uses its own connection and client id.

Log line format, one JSON object per line:
    {"ts": "2026-11-21T21:30:14Z", "topic": "...", "retained": false, "payload": {...}}
"""
import json
import os
import socket
import struct
import threading
import time
from datetime import datetime, timezone

KEEPALIVE = 60


def _env():
    return {
        "host": os.environ.get("MQTT_HOST", ""),
        "port": int(os.environ.get("MQTT_PORT", "1883")),
        "user": os.environ.get("MQTT_USER", ""),
        "password": os.environ.get("MQTT_PASSWORD", ""),
    }


def configured():
    return bool(_env()["host"])


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(ptype, body):
    n, out = len(body), b""
    while True:
        d, n = n & 0x7F, n >> 7
        out += bytes([d | (0x80 if n else 0)])
        if not n:
            break
    return bytes([ptype]) + out + body


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("broker closed the connection")
        buf += chunk
    return buf


def _read(sock):
    header = _recv_exact(sock, 1)[0]
    mult, length = 1, 0
    while True:
        d = _recv_exact(sock, 1)[0]
        length += (d & 0x7F) * mult
        mult *= 128
        if not d & 0x80:
            break
    return header, _recv_exact(sock, length) if length else b""


def _connect(client_id, keepalive):
    cfg = _env()
    if not cfg["host"]:
        raise ConnectionError("MQTT_HOST is not set")
    flags = 0x02  # clean session
    body = _str(client_id)
    if cfg["user"]:
        flags |= 0x80
        body += _str(cfg["user"])
    if cfg["password"]:
        flags |= 0x40
        body += _str(cfg["password"])
    sock = socket.create_connection((cfg["host"], cfg["port"]), timeout=10)
    try:
        sock.sendall(_packet(0x10, _str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive) + body))
        h, ack = _read(sock)
        if h >> 4 != 2 or len(ack) < 2 or ack[1] != 0:
            raise ConnectionError(f"broker refused the connection (code {ack[1] if len(ack) > 1 else '?'})")
    except Exception:
        sock.close()
        raise
    return sock


def publish_retained(topic, payload):
    """Publish one retained message at QoS 1 and wait for the broker's ack."""
    with _connect(f"enviro-dash-pub-{socket.gethostname()}", 30) as sock:
        sock.sendall(_packet(0x33, _str(topic) + struct.pack("!H", 1) + payload.encode()))
        h, _ = _read(sock)
        if h >> 4 != 4:
            raise ConnectionError("broker did not acknowledge the publish")
        sock.sendall(b"\xe0\x00")


class Subscriber(threading.Thread):
    """Appends every message under `topic` to `path`, reconnecting for ever."""

    def __init__(self, path, topic):
        super().__init__(daemon=True)
        self.path, self.topic = path, topic
        self.status = {"connected": False, "since": None, "error": None, "messages": 0}

    def _session(self, out):
        sock = _connect(f"enviro-dash-log-{socket.gethostname()}", KEEPALIVE)
        with sock:
            sock.sendall(_packet(0x82, struct.pack("!H", 1) + _str(self.topic) + b"\x00"))
            self.status.update(connected=True, since=time.time(), error=None)
            print(f"mqtt: connected, subscribed to {self.topic}", flush=True)
            sock.settimeout(KEEPALIVE / 2)
            while True:
                try:
                    h, body = _read(sock)
                except socket.timeout:
                    sock.sendall(b"\xc0\x00")  # PINGREQ
                    continue
                if h >> 4 != 3:
                    continue
                tlen = struct.unpack("!H", body[:2])[0]
                topic = body[2:2 + tlen].decode(errors="replace")
                off = 2 + tlen + (2 if (h >> 1) & 3 else 0)
                raw = body[off:].decode(errors="replace")
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = raw
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                rec = {"ts": ts, "topic": topic, "retained": bool(h & 1), "payload": data}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                self.status["messages"] += 1

    def run(self):
        backoff = 1
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as out:
            while True:
                started = time.time()
                try:
                    self._session(out)
                except Exception as e:
                    self.status.update(connected=False, since=time.time(), error=str(e) or repr(e))
                    print(f"mqtt: disconnected: {e!r}; retrying in {backoff}s", flush=True)
                if time.time() - started > 60:
                    backoff = 1
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
