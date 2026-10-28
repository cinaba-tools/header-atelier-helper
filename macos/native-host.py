#!/usr/bin/env python3
"""Header Atelier - Akamai staging switch (native messaging host, macOS).

Spawned by Chrome via native messaging. Lets the Header Atelier extension
point a production hostname at the Akamai *staging* network **without any
privileges** - no root, no sudo, no /etc/hosts edit (which a locked-down
corporate Mac forbids to non-admin users).

Instead it runs a tiny forward proxy on 127.0.0.1 as the current user. The
extension routes only the managed hostnames to that proxy via a chrome.proxy
PAC script; for each connection the proxy opens the TCP socket to the Akamai
*staging edge IP* while the browser keeps doing its own TLS handshake through
the tunnel. So Host header, SNI, certificate and cookies all stay on the
production hostname - the standard Akamai staging-test method, moved from the
DNS/hosts layer to the browser-proxy layer.

Native messaging actions (one JSON message per request):
  {"action": "status"}                  -> {"ok", "version", "port", "staged": [{"hostname","ip","mode","target?"}]}
  {"action": "set",   "hostname": "h"}  -> {"ok", "hostname", "ip", "edgeHost", "port"}   (mode "staging")
  {"action": "pin",   "hostname": "h",
                      "target": "t"}    -> {"ok", "hostname", "ip", "target", "port"}     (mode "ip")
                                           target = IPv4 used as-is, or a hostname resolved once to its IPv4
  {"action": "clear", "hostname": "h"}  -> {"ok", "hostname", "port"}
  {"action": "detect","hostname": "h"}  -> {"ok", "hostname", "verdict", "evidence", "edgeHost", "ip", "ptr", "chain"}
                                           verdict = "akamai" | "none"; evidence = "cname" | "ptr" | "unresolved" | ""
Errors: {"ok": false, "error": "..."}

Also runnable from the shell:
  native-host.py --proxy-daemon <port>   internal: the detached proxy loop
  native-host.py clear-all               stop the proxy and forget all hostnames (uninstall)
  native-host.py restart-daemon          replace a running proxy with this build, keeping hostnames
"""

import errno
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

VERSION = "1.5.1"
DIG = "/usr/bin/dig"

# Per-user state (no privileges needed).
BASE_DIR = os.path.expanduser("~/Library/Application Support/header-atelier-helper")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
PID_FILE = os.path.join(BASE_DIR, "proxy.pid")
LOG_FILE = os.path.join(BASE_DIR, "proxy.log")
SELF_PATH = os.path.realpath(__file__)

PROXY_HOST = "127.0.0.1"
PREFERRED_PORT = 8998
PORT_RANGE = 40

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def valid_ipv4(s):
    return bool(IPV4_RE.match(s)) and all(0 <= int(p) <= 255 for p in s.split("."))

# Edge-hostname suffixes that mean the request is served by the Akamai
# platform. A CNAME onto one of these is conclusive.
AKAMAI_EDGE_SUFFIXES = [
    "edgekey.net", "edgesuite.net", "akamaiedge.net", "akamaized.net",
    "akamai.net", "akamaihd.net", "akamaistream.net",
    "edgekey-staging.net", "edgesuite-staging.net", "akamaiedge-staging.net",
    "akamaized-staging.net", "akamai-staging.net",
]

# Reverse DNS of the Akamai edge IPs. Catches apex/A-record setups (ALIAS,
# Zone Apex Mapping) where no CNAME is visible at all.
AKAMAI_PTR_SUFFIX = "akamaitechnologies.com"

# Production edge-hostname suffix -> staging equivalent.
STAGING_SUFFIXES = [
    ("edgekey.net", "edgekey-staging.net"),
    ("edgesuite.net", "edgesuite-staging.net"),
    ("akamaized.net", "akamaized-staging.net"),
    ("akamaiedge.net", "akamaiedge-staging.net"),
    ("akamai.net", "akamai-staging.net"),
]


# ---------- native messaging framing ----------

def log(message):
    """Daemon-side logging. stdout/stderr of the detached proxy are redirected
    to LOG_FILE, so this is what makes a proxy failure diagnosable at all -
    before, every error was swallowed and looked like a broken website."""
    print(f"[{time.ctime()}] {message}", flush=True)


def read_message():
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        sys.exit(0)
    (length,) = struct.unpack("<I", raw_len)
    return json.loads(sys.stdin.buffer.read(length))


def send_message(obj):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ---------- DNS resolution ----------

def dig_short(record_type, name):
    result = subprocess.run(
        [DIG, "+short", "+time=3", "+tries=2", record_type, name],
        capture_output=True, text=True, timeout=20,
    )
    return [line.strip().rstrip(".").lower() for line in result.stdout.splitlines() if line.strip()]


def staging_equivalent(name):
    for prod_suffix, staging_suffix in STAGING_SUFFIXES:
        # DNS may already point at a staging edge hostname (test setups):
        # treat it as its own staging equivalent instead of rejecting it.
        if name == staging_suffix or name.endswith("." + staging_suffix):
            return name
        if name == prod_suffix or name.endswith("." + prod_suffix):
            return name[: -len(prod_suffix)] + staging_suffix
    return None


def matches_suffix(name, suffixes):
    return any(name == suffix or name.endswith("." + suffix) for suffix in suffixes)


def reverse_arpa(ip):
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def detect_akamai(hostname):
    """Tell whether `hostname` is served by Akamai, using DNS only.

    Two independent signals, because neither covers every setup:
      1. the CNAME chain ending on an Akamai edge suffix - conclusive;
      2. the reverse DNS of the resolved IP - the fallback for apex records
         (ALIAS / Zone Apex Mapping), where the chain shows no CNAME at all.
    The answer is deliberately binary: an edge CNAME or an Akamai IP means
    Akamai, anything else does not.
    """
    # Start the chain with the hostname itself: visiting an edge hostname
    # directly (foo.akamaized.net) is Akamai even when it has no CNAME.
    chain = [hostname] + dig_short("A", hostname)
    edge_host = None
    ips = []
    for name in chain:
        if IPV4_RE.match(name):
            ips.append(name)
        elif edge_host is None and matches_suffix(name, AKAMAI_EDGE_SUFFIXES):
            edge_host = name

    ip = ips[0] if ips else ""
    if edge_host:
        return {"verdict": "akamai", "evidence": "cname", "edgeHost": edge_host, "ip": ip, "ptr": "", "chain": chain}
    if not ips:
        return {"verdict": "none", "evidence": "unresolved", "edgeHost": "", "ip": "", "ptr": "", "chain": chain}

    for candidate in dig_short("PTR", reverse_arpa(ip)):
        if matches_suffix(candidate, [AKAMAI_PTR_SUFFIX]):
            return {"verdict": "akamai", "evidence": "ptr", "edgeHost": "", "ip": ip, "ptr": candidate, "chain": chain}
    return {"verdict": "none", "evidence": "", "edgeHost": "", "ip": ip, "ptr": "", "chain": chain}


def resolve_staging(hostname):
    """Follow the CNAME chain of `hostname` to its Akamai edge hostname,
    derive the -staging equivalent and resolve its IPv4 address."""
    chain = [hostname] + dig_short("A", hostname)
    edge_staging = None
    edge_prod = None
    for name in chain:
        if IPV4_RE.match(name):
            continue
        candidate = staging_equivalent(name)
        if candidate:
            edge_prod = name
            edge_staging = candidate
            break
    if not edge_staging:
        raise RuntimeError(
            f"{hostname} does not resolve through an Akamai edge hostname "
            "(edgekey/edgesuite/akamaized/akamaiedge)"
        )
    ips = [line for line in dig_short("A", edge_staging) if IPV4_RE.match(line)]
    if not ips:
        raise RuntimeError(f"staging edge hostname {edge_staging} did not resolve to an IPv4 address")
    return edge_prod, edge_staging, ips[0]


# ---------- state file ----------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, ValueError):
        state = {}
    state.setdefault("port", PREFERRED_PORT)
    state.setdefault("hostnames", {})
    # Legacy state files (helper <= 1.2.0) stored plain "hostname": "ip".
    for h, v in list(state["hostnames"].items()):
        if isinstance(v, str):
            state["hostnames"][h] = {"ip": v, "mode": "staging"}
    return state


def save_state(state):
    os.makedirs(BASE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def staged_list(state):
    out = []
    for h, entry in sorted(state.get("hostnames", {}).items()):
        item = {"hostname": h, "ip": entry["ip"], "mode": entry.get("mode", "staging")}
        if entry.get("target"):
            item["target"] = entry["target"]
        out.append(item)
    return out


# ---------- proxy daemon lifecycle ----------

def read_pid():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM
    return True


def proxy_ping(port, timeout=1.5):
    """Ask whoever is listening on `port` to identify itself. Returns the
    parsed /__ping payload, or None when nobody answers or the answer is not
    ours (another program can perfectly well hold that port)."""
    try:
        with socket.create_connection((PROXY_HOST, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"GET /__ping HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            chunks = []
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 8192:
                    break
    except OSError:
        return None
    _, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and payload.get("proxy") == "header-atelier" else None


def daemon_running(port=None):
    """A live PID is not proof: after a reboot the number can belong to an
    unrelated process, and an upgrade can leave an older daemon serving. When
    a port is known, ask the listener who it is and require our own version."""
    if port is not None:
        payload = proxy_ping(port)
        if payload is None:
            return False
        if payload.get("version") != VERSION:
            log(f"stale proxy on port {port} (version {payload.get('version')}, expected {VERSION})")
            return False
        return True
    return pid_alive(read_pid())


def port_is_free(port):
    """Free means "the daemon could bind it". run_proxy() binds with
    SO_REUSEADDR, so test with it too: without it a port merely left in
    TIME_WAIT by a previous connection looked taken, and every restart drifted
    the proxy one port further along."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((PROXY_HOST, port))
            return True
        except OSError:
            return False


def wait_until_listening(port, timeout=2.5):
    """Wait for OUR proxy, not merely for something accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proxy_ping(port, timeout=0.4):
            return True
        time.sleep(0.05)
    return False


def spawn_daemon(port):
    """Double-fork a detached proxy process so it outlives this native-host
    invocation and Chrome's connection."""
    os.makedirs(BASE_DIR, exist_ok=True)
    pid = os.fork()
    if pid > 0:
        return  # parent (native host) returns
    # first child
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    # grandchild: fully detached daemon
    with open(os.devnull, "rb") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    logfd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(logfd, sys.stdout.fileno())
    os.dup2(logfd, sys.stderr.fileno())
    try:
        run_proxy(port)
    finally:
        os._exit(0)


def ensure_daemon(state):
    """Start the proxy if hostnames are staged and it is not running; stop it
    if nothing is staged. Returns the port in effect."""
    port = state.get("port", PREFERRED_PORT)
    if state.get("hostnames"):
        if not daemon_running(port):
            # Either nothing is listening, or what is listening is not us (or
            # is an older build): make sure the old one is gone before binding.
            stop_daemon(port)
            if not port_is_free(port):
                port = next((p for p in range(PREFERRED_PORT, PREFERRED_PORT + PORT_RANGE)
                             if port_is_free(p)), None)
                if port is None:
                    raise RuntimeError("no free localhost port for the staging proxy")
                state["port"] = port
                save_state(state)
            spawn_daemon(port)
            if not wait_until_listening(port):
                raise RuntimeError("staging proxy failed to start on 127.0.0.1:%d" % port)
    else:
        stop_daemon()
    return port


def stop_daemon(port=None):
    """Stop the running proxy and wait for it to actually be gone. Without the
    wait, ensure_daemon() found the port still held a moment later and moved
    the new daemon to another one - drifting a port further on every upgrade."""
    pid = read_pid()
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.05)
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass
    # The listening socket can outlive the process by a moment.
    if port is not None:
        deadline = time.time() + 2.0
        while time.time() < deadline and not port_is_free(port):
            time.sleep(0.05)


# ---------- proxy server ----------

def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def dest_ip_for(host):
    """Staging/pinned IP for a managed hostname, else the real resolved IP."""
    entry = load_state().get("hostnames", {}).get(host)
    if entry:
        return entry["ip"]
    return socket.gethostbyname(host)


def handle_connect(client, target):
    host, _, port = target.partition(":")
    port = int(port or "443")
    try:
        ip = dest_ip_for(host)
        upstream = socket.create_connection((ip, port), timeout=15)
    except Exception as e:  # noqa: BLE001 - answer, never just drop the client
        log(f"CONNECT {host}:{port} failed: {e!r}")
        client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        return
    # Clear both timeouts: create_connection leaves 15s on the upstream and
    # serve_client set 30s on the client. Inside pipe() those made recv() time
    # out on an idle-but-healthy tunnel, and the handler tore it down - which
    # the browser reports as ERR_EMPTY_RESPONSE on the next reused connection.
    upstream.settimeout(None)
    client.settimeout(None)
    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    t = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
    t.start()
    pipe(upstream, client)
    t.join()


def serve_client(client):
    try:
        client.settimeout(30)
        first = b""
        while b"\n" not in first:
            chunk = client.recv(4096)
            if not chunk:
                return
            first += chunk
            if len(first) > 65536:
                return
        line, _, remainder = first.partition(b"\n")
        line = line.rstrip(b"\r").decode("latin-1")
        parts = line.split(" ")
        if len(parts) < 3:
            return
        method, target, version = parts[0], parts[1], parts[2]
        if target.startswith("/"):
            # Origin-form request: this is addressed to the proxy itself, not
            # proxied through it. Used by ensure_daemon() to identify us.
            handle_local_request(client, target)
        elif method.upper() == "CONNECT":
            # The client waits for our 200 before sending any TLS bytes, so
            # there is nothing buffered past the CONNECT line to preserve.
            handle_connect(client, target)
        else:
            # Bytes already read past the first line belong to the header block.
            handle_http_with_prefix(client, method, target, version, remainder)
    except OSError:
        pass
    except Exception as e:  # noqa: BLE001
        # Anything else used to escape here and close the socket without a
        # reply, which is indistinguishable from a broken site in the browser.
        log(f"unhandled error serving client: {e!r}")
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except OSError:
            pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def handle_local_request(client, path):
    """Answer requests aimed at the proxy itself. /__ping identifies which
    build is actually listening - the way to catch a daemon left over from an
    older helper, which used to keep serving after an upgrade."""
    if path.split("?")[0] == "/__ping":
        body = json.dumps({"proxy": "header-atelier", "version": VERSION, "pid": os.getpid()}).encode()
        client.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        return
    client.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")


def handle_http_with_prefix(client, method, target, version, prefix):
    m = re.match(r"^https?://([^/]+)(/[^ ]*)?$", target, re.IGNORECASE)
    if not m:
        client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        return
    authority = m.group(1).split("@")[-1]
    path = m.group(2) or "/"
    host = authority.split(":")[0]
    port = int(authority.split(":")[1]) if ":" in authority else 80
    # Header block = whatever followed the first line, plus more from the socket.
    headers = bytearray(prefix)
    while b"\r\n\r\n" not in headers and b"\n\n" not in headers:
        chunk = client.recv(4096)
        if not chunk:
            break
        headers += chunk
        if len(headers) > 65536:
            break
    new_head = f"{method} {path} {version}\r\n".encode("latin-1") + bytes(headers)
    try:
        ip = dest_ip_for(host)
        upstream = socket.create_connection((ip, port), timeout=15)
    except Exception as e:  # noqa: BLE001 - answer, never just drop the client
        log(f"{method} http://{host}:{port}{path} failed: {e!r}")
        client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        return
    upstream.settimeout(None)
    client.settimeout(None)
    upstream.sendall(new_head)
    t = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
    t.start()
    pipe(upstream, client)
    t.join()


def run_proxy(port):
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    stopping = {"flag": False}

    def _term(_signo, _frame):
        stopping["flag"] = True
    try:
        signal.signal(signal.SIGTERM, _term)
    except ValueError:
        pass  # not the main thread (e.g. under test); SIGTERM default still exits

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PROXY_HOST, port))
    server.listen(64)
    server.settimeout(1.0)
    print(f"[{time.ctime()}] staging proxy listening on {PROXY_HOST}:{port}", flush=True)
    try:
        while not stopping["flag"]:
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=serve_client, args=(client,), daemon=True).start()
    finally:
        server.close()
        try:
            os.remove(PID_FILE)
        except FileNotFoundError:
            pass


# ---------- actions ----------

def validated_hostname(message):
    hostname = str(message.get("hostname", "")).strip().lower()
    if not HOSTNAME_RE.match(hostname):
        raise RuntimeError(f"invalid hostname: {hostname!r}")
    return hostname


def handle(message):
    action = message.get("action")
    if action == "status":
        state = load_state()
        port = ensure_daemon(state)
        return {"ok": True, "version": VERSION, "port": port, "staged": staged_list(state)}
    if action == "set":
        hostname = validated_hostname(message)
        edge_prod, edge_staging, ip = resolve_staging(hostname)
        state = load_state()
        state["hostnames"][hostname] = {"ip": ip, "mode": "staging"}
        save_state(state)
        port = ensure_daemon(state)
        return {"ok": True, "hostname": hostname, "ip": ip, "mode": "staging",
                "edgeHost": edge_staging, "prodEdgeHost": edge_prod, "port": port}
    if action == "pin":
        # Pin the hostname to a caller-chosen destination instead of the
        # auto-derived staging edge: an IPv4 used as-is, or any hostname
        # (e.g. a specific edge hostname) resolved once to its IPv4. Covers
        # sites whose CNAME does not exist in DNS yet.
        hostname = validated_hostname(message)
        target = str(message.get("target", "")).strip().lower().rstrip(".")
        if valid_ipv4(target):
            ip = target
        elif HOSTNAME_RE.match(target):
            ips = [line for line in dig_short("A", target) if IPV4_RE.match(line)]
            if not ips:
                raise RuntimeError(f"{target} did not resolve to an IPv4 address")
            ip = ips[0]
        else:
            raise RuntimeError(f"invalid target {target!r}: enter an IPv4 address or a hostname")
        state = load_state()
        state["hostnames"][hostname] = {"ip": ip, "mode": "ip", "target": target}
        save_state(state)
        port = ensure_daemon(state)
        return {"ok": True, "hostname": hostname, "ip": ip, "target": target,
                "mode": "ip", "port": port}
    if action == "detect":
        # Read-only: never touches the proxy state, so it is safe to call on
        # every first visit to a hostname.
        hostname = validated_hostname(message)
        result = detect_akamai(hostname)
        return {"ok": True, "hostname": hostname, **result}
    if action == "clear":
        hostname = validated_hostname(message)
        state = load_state()
        state["hostnames"].pop(hostname, None)
        save_state(state)
        port = ensure_daemon(state)
        return {"ok": True, "hostname": hostname, "port": port}
    raise RuntimeError(f"unknown action: {action!r}")


def cli_clear_all():
    stop_daemon()
    save_state({"port": PREFERRED_PORT, "hostnames": {}})
    print("staging proxy stopped; all hostnames cleared")


def cli_restart_daemon():
    """Used by install.sh: an upgrade must not leave the previous daemon
    serving (it is detached, so nothing else would ever stop it). The staged
    hostnames are kept - only the process is replaced."""
    stop_daemon()
    state = load_state()
    if not state.get("hostnames"):
        print("staging proxy stopped; no hostnames staged, nothing to restart")
        return
    port = ensure_daemon(state)
    print(f"staging proxy restarted on 127.0.0.1:{port} (version {VERSION})")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--proxy-daemon":
        run_proxy(int(sys.argv[2]))
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "clear-all":
        cli_clear_all()
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "restart-daemon":
        cli_restart_daemon()
        return
    while True:
        message = read_message()
        try:
            send_message(handle(message))
        except Exception as e:  # noqa: BLE001 - every failure must reach the popup
            send_message({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
