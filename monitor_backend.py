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

def send_alert_email(subject, message):
    try:
        data = load_config()
        cfg = data.get("emailConfig", {})
        smtp_server = cfg.get("smtpServer", "")
        smtp_port = int(cfg.get("smtpPort", 587))
        username = cfg.get("username", "")
        password = decrypt_password(cfg.get("password", ""))
        from_email = cfg.get("fromEmail", username)
        recipients = cfg.get("recipients", [])

        if not smtp_server or not username or not recipients:
            print(f"[EMAIL SKIP] Not configured. Subject: {subject}")
            return False

        msg = (
            f"From: {from_email}\r\n"
            f"To: {', '.join(recipients)}\r\n"
            f"Subject: {subject}\r\n\r\n"
            f"{message}"
        )
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(username, password)
            server.sendmail(from_email, recipients, msg.encode("utf-8"))
        print(f"[EMAIL SENT] {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

def check_services(manual_trigger=False):
    try:
        data = load_config()
    except Exception as e:
        print(f"Error loading config: {e}")
        return

    stats = {"online": 0, "offline": 0, "slow": 0, "total": len(data.get("services", []))}
    alerts_to_send = []

    for service in data.get("services", []):
        url = service.get("url")
        if not url:
            continue

        username = service.get("username", "")
        password = decrypt_password(service.get("password", ""))
        auth_type = service.get("authType", "server")

        # Build check URL — append token if credentials provided
        sep = "&" if "?" in url else "?"
        check_url = url + sep + "f=json"
        token_error = None

        if username and password:
            token, token_error = get_arcgis_token(url, username, password, auth_type)
            if token:
                check_url += f"&token={token}"

        start_time = time.time()
        status = "Unknown"
        ping_ms = None
        error_detail = ""

        if token_error:
            status = "Offline"
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
                            status = "Offline"
                            err = json_resp["error"]
                            error_detail = err.get("message", f"Error code {err.get('code', '?')}")
                        elif ping_ms > 3000:
                            status = "Slow"
                        else:
                            status = "Online"
                    except json.JSONDecodeError:
                        status = "Offline"
                        error_detail = "Invalid response (not JSON)"
            except urllib.error.HTTPError as e:
                status = "Offline"
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
                status = "Offline"
                error_detail = str(e)

        old_status = service.get("status", "Unknown")
        service["status"] = status
        service["pingMs"] = ping_ms
        service["lastError"] = error_detail
        service["lastChecked"] = datetime.now(timezone.utc).isoformat()

        history = service.get("pingHistory", [])
        history.append(ping_ms if ping_ms is not None else 0)
        service["pingHistory"] = history[-15:]

        if status == "Online":
            stats["online"] += 1
        elif status == "Offline":
            stats["offline"] += 1
        elif status == "Slow":
            stats["slow"] += 1

        if status != old_status and old_status != "Unknown":
            level = None
            if status == "Offline":
                level = "Critical"
                msg = f"Service {service.get('serviceName')} went Offline."
            elif status == "Online" and old_status in ["Offline", "Slow"]:
                level = "Recovery"
                msg = f"Service {service.get('serviceName')} recovered and is now Online."
            elif status == "Slow":
                level = "Warning"
                msg = f"Service {service.get('serviceName')} is Slow ({ping_ms}ms)."
            if level:
                alerts_to_send.append({"level": level, "serviceName": service.get("serviceName"), "message": msg})

    data["stats"] = stats
    data["serverTime"] = datetime.now(timezone.utc).isoformat()

    recent_alerts = data.get("recentAlerts", [])
    for alert in alerts_to_send:
        email_sent = send_alert_email(f"ArcGIS Monitor: {alert['level']}", alert["message"])
        recent_alerts.append({
            "level": alert["level"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "serviceName": alert["serviceName"],
            "message": alert["message"],
            "emailSent": email_sent
        })
    data["recentAlerts"] = recent_alerts[-20:]

    try:
        save_config(data)
    except Exception as e:
        print(f"Error saving config: {e}")

def monitor_loop():
    while True:
        check_services()
        try:
            data = load_config()
            interval_sec = max(60, int(data.get("checkIntervalMinutes", 15)) * 60)
        except Exception:
            interval_sec = 900
        print(f"[MONITOR] Next check in {interval_sec // 60} min {interval_sec % 60} sec")
        time.sleep(interval_sec)

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
                self._send_json(safe)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/config/interval":
            try:
                data = load_config()
                self._send_json({"checkIntervalMinutes": data.get("checkIntervalMinutes", 15)})
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
                new_service = {
                    "id": new_id,
                    "serviceName": payload.get("name", "Unknown"),
                    "servicePath": derive_path(url),
                    "serviceType": payload.get("type", "MapServer"),
                    "url": url,
                    "username": payload.get("username", ""),
                    "password": encrypt_password(payload.get("password", "")),
                    "authType": payload.get("authType", "server"),
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
                    "smtpPort": int(payload.get("smtpPort", existing.get("smtpPort", 587))),
                    "username": payload.get("emailUsername", existing.get("username", "")),
                    "fromEmail": payload.get("fromEmail", existing.get("fromEmail", "")),
                    "recipients": payload.get("recipients", existing.get("recipients", [])),
                    "password": encrypt_password(payload["emailPassword"]) if payload.get("emailPassword") else existing.get("password", "")
                }
                data["emailConfig"] = cfg
                save_config(data)
                self._send_json({"success": True})

            elif self.path == "/api/config/interval":
                payload = self._read_json_body()
                minutes = max(1, int(payload.get("checkIntervalMinutes", 15)))
                data = load_config()
                data["checkIntervalMinutes"] = minutes
                save_config(data)
                self._send_json({"success": True, "checkIntervalMinutes": minutes})

            elif self.path == "/api/config/email/test":
                payload = self._read_json_body()
                data = load_config()
                saved = data.get("emailConfig", {})

                smtp_server  = payload.get("smtpServer")  or saved.get("smtpServer", "")
                smtp_port    = int(payload.get("smtpPort") or saved.get("smtpPort", 587))
                username     = payload.get("emailUsername") or saved.get("username", "")
                password     = payload.get("emailPassword") or decrypt_password(saved.get("password", ""))
                from_email   = payload.get("fromEmail")   or saved.get("fromEmail", username)
                recipients   = payload.get("recipients")  or saved.get("recipients", [])

                if not smtp_server:
                    self._send_json({"success": False, "message": "กรุณาระบุ SMTP Server"}); return
                if not username:
                    self._send_json({"success": False, "message": "กรุณาระบุ Email Username"}); return
                if not password:
                    self._send_json({"success": False, "message": "กรุณาระบุ Password (หรือบันทึก config ก่อน)"}); return
                if not recipients:
                    recipients = [from_email or username]

                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                subject = "ArcGIS Monitor — Test Email"
                body = (
                    f"This is a test email from ArcGIS Service Monitor.\n\n"
                    f"SMTP Server : {smtp_server}:{smtp_port}\n"
                    f"From        : {from_email}\n"
                    f"To          : {', '.join(recipients)}\n"
                    f"Sent at     : {now_str}\n\n"
                    f"If you received this, email alerts are configured correctly."
                )
                msg = (
                    f"From: {from_email}\r\n"
                    f"To: {', '.join(recipients)}\r\n"
                    f"Subject: {subject}\r\n\r\n"
                    f"{body}"
                )

                try:
                    with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                        server.ehlo()
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
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
                "fromEmail": "", "recipients": []
            }
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(default_config, f, indent=2)

    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    run_server(host=args.host, port=args.port)
