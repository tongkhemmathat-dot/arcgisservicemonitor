import json
import time
import threading
import smtplib
import ssl
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import os

# When bundled by PyInstaller, sys.executable = the EXE path.
# _BASE_DIR is used for writable data (config.json, encryption.key, logs).
# _BUNDLE_DIR is where read-only bundled files live (index.html).
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
    _BUNDLE_DIR = sys._MEIPASS
    # Redirect output to log file — Windows Service has no console
    _log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = open(os.path.join(_log_dir, "app.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = _BASE_DIR

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(_BASE_DIR, "config.json"))

# ---------------------------------------------------------------------------
# Password encryption (Fernet / AES-128-CBC)
# Key priority: ENCRYPTION_KEY env var → key file next to config → auto-generate
# ---------------------------------------------------------------------------
_ENC_PREFIX = "enc:"
_KEY_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "encryption.key")

def _load_or_create_key() -> bytes | None:
    # 1. env var takes top priority
    raw = os.environ.get("ENCRYPTION_KEY", "").strip()
    if raw:
        print("[CRYPTO] Using ENCRYPTION_KEY from environment variable.")
        return raw.encode()

    # 2. existing key file
    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            key = f.read().strip()
        print(f"[CRYPTO] Loaded encryption key from {_KEY_PATH}")
        return key

    # 3. auto-generate and persist
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(_KEY_PATH), exist_ok=True)
        with open(_KEY_PATH, "wb") as f:
            f.write(key)
        print(f"[CRYPTO] Generated new encryption key → {_KEY_PATH}")
        print(f"[CRYPTO] Back up this file to avoid losing access to stored passwords.")
        return key
    except Exception as e:
        print(f"[CRYPTO] Could not generate key: {e}")
        return None

try:
    from cryptography.fernet import Fernet, InvalidToken
    _key_bytes = _load_or_create_key()
    if _key_bytes:
        _fernet = Fernet(_key_bytes)
        print("[CRYPTO] Password encryption enabled.")
    else:
        _fernet = None
        print("[CRYPTO] WARNING: encryption disabled — passwords stored as plain text.")
except Exception as _e:
    _fernet = None
    print(f"[CRYPTO] cryptography library unavailable ({_e}) — passwords stored as plain text.")

def encrypt_password(plain: str) -> str:
    if not plain or not _fernet:
        return plain
    return _ENC_PREFIX + _fernet.encrypt(plain.encode()).decode()

def decrypt_password(stored: str) -> str:
    if not stored:
        return stored
    if stored.startswith(_ENC_PREFIX):
        if not _fernet:
            print("[CRYPTO] ERROR: encrypted password found but ENCRYPTION_KEY is not set.")
            return ""
        try:
            return _fernet.decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
        except Exception:
            print("[CRYPTO] ERROR: failed to decrypt password — wrong key?")
            return ""
    return stored  # plain text (stored before encryption was enabled)

def redact_password(stored: str) -> str:
    """Return masked value for API responses — never expose raw or encrypted bytes."""
    return "********" if stored else ""

# ---------------------------------------------------------------------------
# Token cache: {(token_url, username): (token_str, expires_epoch)}
# ---------------------------------------------------------------------------
_token_cache = {}
_token_lock = threading.Lock()

def get_arcgis_token(service_url, username, password, auth_type="server"):
    """Generate and cache an ArcGIS token.

    auth_type:
        "server"  → /arcgis/tokens/generateToken          (ArcGIS Server)
        "portal"  → /portal/sharing/rest/generateToken    (ArcGIS Enterprise Portal)
        "online"  → https://www.arcgis.com/sharing/rest/generateToken  (ArcGIS Online)
    """
    if not username or not password:
        return None, None

    parsed = urllib.parse.urlparse(service_url)
    server_base = f"{parsed.scheme}://{parsed.netloc}"

    if auth_type == "online":
        token_url = "https://www.arcgis.com/sharing/rest/generateToken"
    elif auth_type == "portal":
        token_url = server_base + "/portal/sharing/rest/generateToken"
    else:  # "server" (default)
        token_url = server_base + "/arcgis/tokens/generateToken"

    cache_key = (token_url, username)

    with _token_lock:
        cached = _token_cache.get(cache_key)
        if cached:
            token, expires = cached
            if time.time() < expires - 60:  # still valid (60s buffer)
                return token, None

    try:
        params = urllib.parse.urlencode({
            "username": username,
            "password": password,
            "client": "requestip",
            "expiration": 60,
            "f": "json"
        }).encode("utf-8")
        req = urllib.request.Request(
            token_url, data=params,
            headers={
                "User-Agent": "ArcGISMonitor/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        if "token" in result:
            token = result["token"]
            expires = result.get("expires", (time.time() + 3600) * 1000) / 1000
            with _token_lock:
                _token_cache[cache_key] = (token, expires)
            print(f"[TOKEN OK] {server_base} ({username})")
            return token, None
        else:
            err = result.get("error", {})
            msg = err.get("message", "Token generation failed")
            print(f"[TOKEN FAIL] {token_url}: {msg}")
            return None, msg
    except Exception as e:
        print(f"[TOKEN ERROR] {token_url}: {e}")
        return None, str(e)

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(data):
    temp_path = CONFIG_PATH + ".tmp"
    with open(temp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, CONFIG_PATH)

def _make_ssl_context(ignore_tls: bool):
    ctx = ssl.create_default_context()
    if ignore_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _smtp_connect(smtp_server, smtp_port, ignore_tls, disable_starttls):
    """สร้าง SMTP connection ตาม option ที่เลือก และคืน server object (ยังไม่ปิด)"""
    ctx = _make_ssl_context(ignore_tls)
    server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
    server.ehlo()
    if not disable_starttls:
        try:
            server.starttls(context=ctx)
            server.ehlo()
        except smtplib.SMTPException:
            pass  # server ไม่รองรับ STARTTLS (relay plain mode) — ข้ามได้
    return server

def send_alert_email(subject, message):
    try:
        data = load_config()
        cfg = data.get("emailConfig", {})
        smtp_server      = cfg.get("smtpServer", "")
        smtp_port        = int(cfg.get("smtpPort", 587))
        username         = cfg.get("username", "")
        password         = decrypt_password(cfg.get("password", ""))
        from_email       = cfg.get("fromEmail", "") or username
        recipients       = cfg.get("recipients", [])
        ignore_tls       = cfg.get("ignoreTls", False)
        disable_starttls = cfg.get("disableStarttls", False)

        if not smtp_server or not recipients:
            print(f"[EMAIL SKIP] Not configured. Subject: {subject}")
            return False

        msg = (
            f"From: {from_email}\r\n"
            f"To: {', '.join(recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n"
            f"{message}"
        )
        with _smtp_connect(smtp_server, smtp_port, ignore_tls, disable_starttls) as server:
            if username and password:
                server.login(username, password)
            server.sendmail(from_email, recipients, msg.encode("utf-8"))
        print(f"[EMAIL SENT] {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

def _check_one(service):
    """ตรวจสอบ service เดียว — คืนค่า (updated_service, alert_or_None)"""
    url = service.get("url")
    if not url:
        return service, None

    username  = service.get("username", "")
    password  = decrypt_password(service.get("password", ""))
    auth_type = service.get("authType", "server")

    sep       = "&" if "?" in url else "?"
    check_url = url + sep + "f=json"
    token_error = None

    if username and password:
        token, token_error = get_arcgis_token(url, username, password, auth_type)
        if token:
            check_url += f"&token={token}"

    start_time   = time.time()
    status       = "Unknown"
    ping_ms      = None
    error_detail = ""

    if token_error:
        status       = "Offline"
        error_detail = f"Token error: {token_error}"
    else:
        try:
            req = urllib.request.Request(check_url, headers={"User-Agent": "ArcGISMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                content = response.read().decode("utf-8")
                ping_ms = int((time.time() - start_time) * 1000)
                try:
                    json_resp = json.loads(content)
                    if "error" in json_resp:
                        status       = "Offline"
                        err          = json_resp["error"]
                        error_detail = err.get("message", f"Error code {err.get('code', '?')}")
                    elif ping_ms > 3000:
                        status = "Slow"
                    else:
                        status = "Online"
                except json.JSONDecodeError:
                    status       = "Offline"
                    error_detail = "Invalid response (not JSON)"
        except urllib.error.HTTPError as e:
            status       = "Offline"
            error_detail = f"HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            status = "Offline"
            reason = str(e.reason).lower()
            if "timed out" in reason or "timeout" in reason:
                error_detail = "Connection timed out (5s)"
            elif "getaddrinfo" in reason or "nodename" in reason or "name or service" in reason:
                error_detail = "DNS lookup failed"
            elif "connection refused" in reason:
                error_detail = "Connection refused"
            elif "ssl" in reason or "certificate" in reason:
                error_detail = "SSL/certificate error"
            else:
                error_detail = str(e.reason)
        except Exception as e:
            status       = "Offline"
            error_detail = str(e)

    old_status = service.get("status", "Unknown")
    updated    = dict(service)
    updated["status"]      = status
    updated["pingMs"]      = ping_ms
    updated["lastError"]   = error_detail
    updated["lastChecked"] = datetime.now(timezone.utc).isoformat()

    # Migrate old format [int, ...] → [{ms, t}, ...]
    raw_history = service.get("pingHistory", [])
    history = []
    for h in raw_history:
        if isinstance(h, dict):
            history.append(h)
        else:
            history.append({"ms": int(h) if h else 0, "t": ""})

    # Append new entry with timestamp
    history.append({
        "ms": ping_ms if ping_ms is not None else 0,
        "t":  datetime.now(timezone.utc).isoformat()
    })

    # Keep only last 24 hours (drop entries older than 24h)
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    history = [h for h in history if not h.get("t") or h["t"] >= cutoff]

    updated["pingHistory"] = history

    alert = None
    if status != old_status and old_status != "Unknown":
        if status == "Offline":
            alert = {"level": "Critical",  "serviceName": service.get("serviceName"),
                     "message": f"Service {service.get('serviceName')} went Offline."}
        elif status == "Online" and old_status in ["Offline", "Slow"]:
            alert = {"level": "Recovery",  "serviceName": service.get("serviceName"),
                     "message": f"Service {service.get('serviceName')} recovered and is now Online."}
        elif status == "Slow":
            alert = {"level": "Warning",   "serviceName": service.get("serviceName"),
                     "message": f"Service {service.get('serviceName')} is Slow ({ping_ms}ms)."}

    return updated, alert


def _flush_results(updated_map, alerts_to_send):
    """บันทึกผลลัพธ์ลง config และส่งอีเมลแจ้งเตือน"""
    try:
        data     = load_config()
        services = data.get("services", [])

        for i, svc in enumerate(services):
            if svc.get("id") in updated_map:
                result = updated_map[svc["id"]]
                # Merge only monitoring fields — preserves tags, name, URL, credentials, etc.
                # that may have been updated concurrently since _check_one captured the dict.
                for field in ("status", "pingMs", "lastError", "lastChecked", "pingHistory"):
                    svc[field] = result[field]

        stats = {"online": 0, "offline": 0, "slow": 0, "total": len(services)}
        for s in services:
            st = s.get("status", "Unknown")
            if st == "Online":  stats["online"]  += 1
            elif st == "Offline": stats["offline"] += 1
            elif st == "Slow":  stats["slow"]    += 1

        data["services"]   = services
        data["stats"]      = stats
        data["serverTime"] = datetime.now(timezone.utc).isoformat()

        recent = data.get("recentAlerts", [])
        for alert in alerts_to_send:
            email_sent = send_alert_email(f"ArcGIS Monitor: {alert['level']}", alert["message"])
            recent.append({
                "level":       alert["level"],
                "timestamp":   datetime.now(timezone.utc).isoformat(),
                "serviceName": alert["serviceName"],
                "message":     alert["message"],
                "emailSent":   email_sent,
            })
        data["recentAlerts"] = recent[-20:]
        save_config(data)
    except Exception as e:
        print(f"[FLUSH] Error saving results: {e}")


def check_services(manual_trigger=False):
    """ตรวจสอบทุก service พร้อมกัน (ใช้โดย /api/monitor/check)"""
    try:
        data = load_config()
    except Exception as e:
        print(f"Error loading config: {e}")
        return

    services    = data.get("services", [])
    stagger_sec = max(0, int(data.get("staggerSeconds", 5)))
    updated_map = {}
    alerts      = []

    for i, svc in enumerate(services):
        if i > 0 and stagger_sec > 0:
            time.sleep(stagger_sec)
        updated, alert = _check_one(svc)
        updated_map[svc.get("id")] = updated
        if alert:
            alerts.append(alert)

    _flush_results(updated_map, alerts)


def monitor_loop():
    """
    วนทุก 60 วินาที — ตรวจสอบว่า service ใดถึงรอบเช็คแล้ว
    แต่ละ service มี checkIntervalMinutes ของตัวเอง (ถ้าไม่ระบุ ใช้ global)
    หน่วงเวลา staggerSeconds ระหว่าง service เพื่อกระจาย network load
    """
    while True:
        try:
            data            = load_config()
            global_interval = max(1, int(data.get("checkIntervalMinutes", 15)))
            stagger_sec     = max(0, int(data.get("staggerSeconds", 5)))
            now             = time.time()

            due = []
            for svc in data.get("services", []):
                raw = svc.get("checkIntervalMinutes")
                svc_interval = max(1, int(raw)) if raw else global_interval

                last = svc.get("lastChecked")
                if not last:
                    due.append(svc)
                else:
                    try:
                        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
                        if now - last_ts >= svc_interval * 60:
                            due.append(svc)
                    except Exception:
                        due.append(svc)

            if due:
                names = ", ".join(s.get("serviceName", "?") for s in due)
                print(f"[MONITOR] Due: {names} | stagger={stagger_sec}s")
                updated_map = {}
                alerts      = []
                for i, svc in enumerate(due):
                    if i > 0 and stagger_sec > 0:
                        time.sleep(stagger_sec)
                    updated, alert = _check_one(svc)
                    updated_map[svc.get("id")] = updated
                    if alert:
                        alerts.append(alert)
                _flush_results(updated_map, alerts)

        except Exception as e:
            print(f"[MONITOR ERROR] {e}")

        time.sleep(60)  # re-evaluate ทุก 1 นาที

def derive_path(url):
    m = re.search(r"/services/(.+)", url, re.IGNORECASE)
    return m.group(1).strip("/") if m else ""

def quick_ping(service):
    """Ping a single service — returns result dict, does NOT save or alert."""
    url = service.get("url", "")
    if not url:
        return {"id": service.get("id"), "pingMs": None, "status": "Offline"}

    username = service.get("username", "")
    password = decrypt_password(service.get("password", ""))
    auth_type = service.get("authType", "server")
    sep = "&" if "?" in url else "?"
    check_url = url + sep + "f=json"

    if username and password:
        token, _ = get_arcgis_token(url, username, password, auth_type)
        if token:
            check_url += f"&token={token}"

    try:
        start = time.time()
        req = urllib.request.Request(check_url, headers={"User-Agent": "ArcGISMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        ping_ms = int((time.time() - start) * 1000)
        try:
            j = json.loads(body)
            if "error" in j:
                return {"id": service.get("id"), "pingMs": None, "status": "Offline"}
        except Exception:
            pass
        status = "Slow" if ping_ms > 3000 else "Online"
        return {"id": service.get("id"), "pingMs": ping_ms, "status": status}
    except Exception:
        return {"id": service.get("id"), "pingMs": None, "status": "Offline"}


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress per-request logging

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/monitor/dashboard":
            try:
                data = load_config()
                # Redact passwords — never expose plain text or encrypted bytes to the UI
                for svc in data.get("services", []):
                    svc["password"] = redact_password(svc.get("password", ""))
                self._send_json(data)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/config/email":
            try:
                data = load_config()
                cfg = data.get("emailConfig", {})
                safe = {k: v for k, v in cfg.items() if k != "password"}
                safe["hasPassword"] = bool(cfg.get("password"))
                safe.setdefault("ignoreTls", False)
                safe.setdefault("disableStarttls", False)
                self._send_json(safe)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/config/interval":
            try:
                data = load_config()
                self._send_json({
                    "checkIntervalMinutes": data.get("checkIntervalMinutes", 15),
                    "staggerSeconds": data.get("staggerSeconds", 5),
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/monitor/ping":
            try:
                data = load_config()
                services = data.get("services", [])
                results = [None] * len(services)
                threads = []

                def _ping(i, svc):
                    results[i] = quick_ping(svc)

                for i, svc in enumerate(services):
                    t = threading.Thread(target=_ping, args=(i, svc), daemon=True)
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join(timeout=6)

                self._send_json({
                    "results": [r for r in results if r],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path in ("/", "/index.html"):
            index_path = os.path.join(_BUNDLE_DIR, "index.html")
            try:
                with open(index_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._send_json({"error": "index.html not found"}, 404)

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/api/monitor/check":
                threading.Thread(target=check_services, args=(True,), daemon=True).start()
                self._send_json({"success": True})

            elif self.path == "/api/monitor/add":
                payload = self._read_json_body()
                data = load_config()
                services = data.get("services", [])
                new_id = max((s.get("id", 0) for s in services), default=0) + 1
                url = payload.get("url", "")
                raw_interval = payload.get("checkIntervalMinutes")
                svc_interval = max(1, int(raw_interval)) if raw_interval else None
                raw_tags = payload.get("tags", [])
                tags = [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()]
                new_service = {
                    "id": new_id,
                    "serviceName": payload.get("name", "Unknown"),
                    "servicePath": derive_path(url),
                    "serviceType": payload.get("type", "MapServer"),
                    "url": url,
                    "username": payload.get("username", ""),
                    "password": encrypt_password(payload.get("password", "")),
                    "authType": payload.get("authType", "server"),
                    "checkIntervalMinutes": svc_interval,
                    "tags": tags,
                    "status": "Unknown",
                    "pingMs": None,
                    "pingHistory": [],
                    "lastChecked": None
                }
                services.append(new_service)
                data["services"] = services
                data["stats"]["total"] = len(services)
                save_config(data)
                safe_service = {**new_service, "password": redact_password(new_service["password"])}
                self._send_json({"success": True, "service": safe_service})

            elif self.path == "/api/monitor/update":
                payload = self._read_json_body()
                target_id = payload.get("id")
                data = load_config()
                updated = False
                for svc in data.get("services", []):
                    if svc.get("id") == target_id:
                        svc["serviceName"] = payload.get("name", svc["serviceName"])
                        svc["serviceType"] = payload.get("type", svc["serviceType"])
                        new_url = payload.get("url", svc["url"])
                        svc["url"] = new_url
                        svc["servicePath"] = derive_path(new_url)
                        if "username" in payload:
                            svc["username"] = payload["username"]
                        if payload.get("password"):
                            svc["password"] = encrypt_password(payload["password"])
                        if "authType" in payload:
                            svc["authType"] = payload["authType"]
                        if "checkIntervalMinutes" in payload:
                            raw = payload["checkIntervalMinutes"]
                            svc["checkIntervalMinutes"] = max(1, int(raw)) if raw else None
                        if "tags" in payload:
                            raw_tags = payload["tags"]
                            svc["tags"] = [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()]
                        updated = True
                        break
                if not updated:
                    self._send_json({"success": False, "error": "Service not found"}, 404)
                    return
                save_config(data)
                self._send_json({"success": True})

            elif self.path == "/api/monitor/delete":
                payload = self._read_json_body()
                target_id = payload.get("id")
                data = load_config()
                before = len(data.get("services", []))
                data["services"] = [s for s in data.get("services", []) if s.get("id") != target_id]
                if len(data["services"]) == before:
                    self._send_json({"success": False, "error": "Service not found"}, 404)
                    return
                data["stats"]["total"] = len(data["services"])
                save_config(data)
                self._send_json({"success": True})

            elif self.path == "/api/config/email":
                payload = self._read_json_body()
                data = load_config()
                existing = data.get("emailConfig", {})
                cfg = {
                    "smtpServer": payload.get("smtpServer", existing.get("smtpServer", "")),
                    "smtpPort":   int(payload.get("smtpPort", existing.get("smtpPort", 587))),
                    "username":   payload.get("emailUsername", existing.get("username", "")),
                    "fromEmail":  payload.get("fromEmail", existing.get("fromEmail", "")),
                    "recipients": payload.get("recipients", existing.get("recipients", [])),
                    "ignoreTls":       bool(payload.get("ignoreTls",       existing.get("ignoreTls",       False))),
                    "disableStarttls": bool(payload.get("disableStarttls", existing.get("disableStarttls", False))),
                    "password":        encrypt_password(payload["emailPassword"]) if payload.get("emailPassword") else existing.get("password", "")
                }
                data["emailConfig"] = cfg
                save_config(data)
                self._send_json({"success": True})

            elif self.path == "/api/config/interval":
                payload = self._read_json_body()
                minutes = max(1, int(payload.get("checkIntervalMinutes", 15)))
                stagger = max(0, int(payload.get("staggerSeconds", 5)))
                data = load_config()
                data["checkIntervalMinutes"] = minutes
                data["staggerSeconds"] = stagger
                save_config(data)
                self._send_json({"success": True, "checkIntervalMinutes": minutes, "staggerSeconds": stagger})

            elif self.path == "/api/config/email/test":
                payload = self._read_json_body()
                data = load_config()
                saved = data.get("emailConfig", {})

                smtp_server = payload.get("smtpServer")    or saved.get("smtpServer", "")
                smtp_port   = int(payload.get("smtpPort")  or saved.get("smtpPort", 587))
                username    = payload.get("emailUsername") or saved.get("username", "")
                password    = payload.get("emailPassword") or decrypt_password(saved.get("password", ""))
                from_email  = payload.get("fromEmail")     or saved.get("fromEmail", "") or username
                recipients  = payload.get("recipients")    or saved.get("recipients", [])
                ignore_tls       = payload.get("ignoreTls",       saved.get("ignoreTls",       False))
                disable_starttls = payload.get("disableStarttls", saved.get("disableStarttls", False))

                if not smtp_server:
                    self._send_json({"success": False, "message": "กรุณาระบุ SMTP Server"}); return
                if not recipients:
                    if from_email:
                        recipients = [from_email]
                    else:
                        self._send_json({"success": False, "message": "กรุณาระบุ Recipients หรือ From Email"}); return

                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                subject = "ArcGIS Monitor — Test Email"
                auth_mode = username if username else "relay (no auth)"
                body = (
                    f"This is a test email from ArcGIS Service Monitor.\n\n"
                    f"SMTP Server    : {smtp_server}:{smtp_port}\n"
                    f"Auth           : {auth_mode}\n"
                    f"Ignore TLS     : {'yes' if ignore_tls else 'no'}\n"
                    f"Disable STARTTLS: {'yes' if disable_starttls else 'no'}\n"
                    f"From           : {from_email}\n"
                    f"To             : {', '.join(recipients)}\n"
                    f"Sent at        : {now_str}\n\n"
                    f"If you received this, email alerts are configured correctly."
                )
                msg = (
                    f"From: {from_email}\r\n"
                    f"To: {', '.join(recipients)}\r\n"
                    f"Subject: {subject}\r\n\r\n"
                    f"{body}"
                )

                try:
                    with _smtp_connect(smtp_server, smtp_port, ignore_tls, disable_starttls) as server:
                        if username and password:
                            server.login(username, password)
                        server.sendmail(from_email, recipients, msg.encode("utf-8"))
                    self._send_json({
                        "success": True,
                        "message": f"ส่งสำเร็จ → {', '.join(recipients)}"
                    })
                except smtplib.SMTPAuthenticationError:
                    self._send_json({"success": False, "message": "Authentication failed — username หรือ password ไม่ถูกต้อง"})
                except smtplib.SMTPConnectError:
                    self._send_json({"success": False, "message": f"ไม่สามารถเชื่อมต่อ {smtp_server}:{smtp_port}"})
                except smtplib.SMTPRecipientsRefused as e:
                    refused = ', '.join(e.recipients.keys())
                    self._send_json({"success": False, "message": f"Server ปฏิเสธผู้รับ: {refused}"})
                except smtplib.SMTPSenderRefused:
                    self._send_json({"success": False, "message": f"Server ปฏิเสธ sender: {from_email}"})
                except ssl.SSLError as e:
                    self._send_json({"success": False, "message": f"SSL error: {e}"})
                except OSError as e:
                    self._send_json({"success": False, "message": f"Network error: {e.strerror or e}"})

            else:
                self._send_json({"error": "Not Found"}, 404)

        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def do_DELETE(self):
        m = re.match(r"^/api/monitor/delete/(\d+)$", self.path)
        if m:
            try:
                target_id = int(m.group(1))
                data = load_config()
                data["services"] = [s for s in data.get("services", []) if s.get("id") != target_id]
                data["stats"]["total"] = len(data["services"])
                save_config(data)
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
        else:
            self._send_json({"error": "Not Found"}, 404)


def run_server(host=None, port=None):
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"ArcGIS Monitor running at http://{host}:{port}")
    print(f"Config file: {CONFIG_PATH}")
    httpd.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ArcGIS Service Monitor")
    parser.add_argument("--host", default=None, help="Bind host (default: HOST env or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (default: PORT env or 8000)")
    args, _ = parser.parse_known_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"Creating default config at {CONFIG_PATH}")
        default_config = {
            "arcGISBaseUrl": "https://your-server.domain.com/server/rest/services",
            "serverTime": datetime.now(timezone.utc).isoformat(),
            "stats": {"online": 0, "offline": 0, "slow": 0, "total": 0},
            "services": [],
            "recentAlerts": [],
            "emailConfig": {
                "smtpServer": "", "smtpPort": 587,
                "username": "", "password": "",
                "fromEmail": "", "recipients": [],
                "ignoreTls": False,
                "disableStarttls": False
            }
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(default_config, f, indent=2)

    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    run_server(host=args.host, port=args.port)
