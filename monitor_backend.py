import json
import time
import threading
import smtplib
import ssl
import re
import sys
import base64
import hmac
import hashlib
import secrets
import urllib.request
import urllib.error
import urllib.parse
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import os

# When bundled by PyInstaller, sys.executable = the EXE path.
# _BASE_DIR is used for writable data (config.json, encryption.key, logs).
# _BUNDLE_DIR is where read-only bundled files live (index.html).
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
    _BUNDLE_DIR = sys._MEIPASS
    # Redirect output to log file — Windows Service has no console.
    # Rotates daily at midnight; keeps 30 days of history (app.log.YYYY-MM-DD), older ones deleted.
    _log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(_log_dir, exist_ok=True)

    class _RotatingStdout:
        """File-like wrapper around TimedRotatingFileHandler so print()/tracebacks
        can redirect through it while getting daily rotation + retention."""

        def __init__(self, handler):
            self._handler = handler

        def write(self, message):
            self._handler.acquire()
            try:
                if self._handler.shouldRollover(None):
                    self._handler.doRollover()
                self._handler.stream.write(message)
                self._handler.stream.flush()
            finally:
                self._handler.release()

        def flush(self):
            self._handler.acquire()
            try:
                self._handler.stream.flush()
            finally:
                self._handler.release()

    _log_handler = TimedRotatingFileHandler(
        os.path.join(_log_dir, "app.log"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    _log_file = _RotatingStdout(_log_handler)
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
# Login credentials (HTTP Basic Auth) — gates every API/dashboard request.
# Priority: AUTH_USERNAME/AUTH_PASSWORD env vars → stored hash → auto-generate
# ---------------------------------------------------------------------------
_AUTH_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "auth.json")
_AUTH_USER = None
_AUTH_SALT = None
_AUTH_HASH = None

def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)

def _env_auth():
    user = os.environ.get("AUTH_USERNAME", "").strip()
    pwd  = os.environ.get("AUTH_PASSWORD", "").strip()
    return (user, pwd) if user and pwd else None

def set_auth(username: str, password: str):
    global _AUTH_USER, _AUTH_SALT, _AUTH_HASH
    salt = secrets.token_bytes(16)
    _AUTH_USER = username
    _AUTH_SALT = salt
    _AUTH_HASH = _hash_password(password, salt)
    with open(_AUTH_PATH, "w") as f:
        json.dump({"username": username, "salt": salt.hex(), "hash": _AUTH_HASH.hex()}, f)

def _load_or_create_auth():
    if os.path.exists(_AUTH_PATH):
        global _AUTH_USER, _AUTH_SALT, _AUTH_HASH
        with open(_AUTH_PATH, "r") as f:
            data = json.load(f)
        _AUTH_USER = data["username"]
        _AUTH_SALT = bytes.fromhex(data["salt"])
        _AUTH_HASH = bytes.fromhex(data["hash"])
        return
    password = secrets.token_urlsafe(12)
    set_auth("admin", password)
    print("=" * 62)
    print("[AUTH] First run — generated a login for the dashboard:")
    print("[AUTH]   Username: admin")
    print(f"[AUTH]   Password: {password}")
    print("[AUTH] Save this now — change it under Settings > Login after logging in.")
    print("=" * 62)

_load_or_create_auth()

def verify_auth(username: str, password: str) -> bool:
    env = _env_auth()
    if env:
        return hmac.compare_digest(username, env[0]) and hmac.compare_digest(password, env[1])
    if not _AUTH_USER or not hmac.compare_digest(username, _AUTH_USER):
        return False
    return hmac.compare_digest(_hash_password(password, _AUTH_SALT), _AUTH_HASH)

# ---------------------------------------------------------------------------
# Token cache: {(token_url, username): (token_str, expires_epoch)}
# ---------------------------------------------------------------------------
_token_cache = {}
_token_lock = threading.Lock()

def _try_generate_token(token_url, username, password, client, referer=""):
    """ยิง generateToken request เดียว — คืน (token_str, expires_epoch) หรือ raise Exception"""
    fields = {
        "username": username,
        "password": password,
        "client": client,
        "expiration": 60,
        "f": "json",
    }
    if referer:
        fields["referer"] = referer
    params = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        token_url, data=params,
        headers={
            "User-Agent": "ArcGISMonitor/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": referer or "",
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    if "token" in result:
        expires = result.get("expires", (time.time() + 3600) * 1000) / 1000
        return result["token"], expires

    err = result.get("error", {})
    raise ValueError(err.get("message", f"code {err.get('code', '?')}"))


def get_arcgis_token(service_url, username, password, auth_type="server"):
    """Generate and cache an ArcGIS token.

    auth_type:
        "server"  → /arcgis/tokens/generateToken, client=requestip
        "portal"  → /portal/sharing/rest/generateToken (fallback: /arcgis/sharing/rest/)
                    client=referer (required by most Enterprise Portal configs)
        "online"  → https://www.arcgis.com/sharing/rest/generateToken, client=referer
    """
    if not username or not password:
        return None, None

    parsed = urllib.parse.urlparse(service_url)
    server_base = f"{parsed.scheme}://{parsed.netloc}"

    # Build ordered list of (url, client, referer) to try in sequence
    if auth_type == "online":
        candidates = [
            ("https://www.arcgis.com/sharing/rest/generateToken", "referer", "https://www.arcgis.com"),
        ]
    elif auth_type == "portal":
        # Enterprise Portal can be installed at /portal or /arcgis context.
        # Most require client=referer; fall back to requestip if referer fails.
        candidates = [
            (server_base + "/portal/sharing/rest/generateToken", "referer", server_base),
            (server_base + "/arcgis/sharing/rest/generateToken", "referer", server_base),
            (server_base + "/portal/sharing/rest/generateToken", "requestip", ""),
            (server_base + "/arcgis/sharing/rest/generateToken", "requestip", ""),
        ]
    else:  # "server"
        candidates = [
            (server_base + "/arcgis/tokens/generateToken", "requestip", ""),
            (server_base + "/arcgis/tokens/generateToken", "referer", server_base),
        ]

    # Check cache using first candidate URL as key
    cache_key = (candidates[0][0], username)
    with _token_lock:
        cached = _token_cache.get(cache_key)
        if cached:
            token, expires, cached_referer = cached
            if time.time() < expires - 60:
                return token, None, cached_referer

    last_error = "Token generation failed"
    for token_url, client, referer in candidates:
        try:
            token, expires = _try_generate_token(token_url, username, password, client, referer)
            with _token_lock:
                _token_cache[cache_key] = (token, expires, referer)
            print(f"[TOKEN OK] {token_url} client={client} ({username})")
            return token, None, referer
        except Exception as e:
            last_error = str(e)
            print(f"[TOKEN FAIL] {token_url} client={client}: {last_error}")

    return None, last_error, ""

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

_LEVEL_COLOR = {
    "Critical": "#c0392b",
    "Warning":  "#e67e22",
    "Recovery": "#27ae60",
}
_LEVEL_ICON = {
    "Critical": "&#x274C;",
    "Warning":  "&#x26A0;&#xFE0F;",
    "Recovery": "&#x2705;",
}

def _build_alert_html(level, service_name, service_url, error_detail, ping_ms, stats, timestamp_utc):
    color = _LEVEL_COLOR.get(level, "#555")
    icon  = _LEVEL_ICON.get(level, "")
    ts_local = timestamp_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    ping_row = ""
    if ping_ms is not None:
        ping_row = f"<tr><td style='padding:4px 8px;color:#888;'>Response time</td><td style='padding:4px 8px;'><b>{ping_ms} ms</b></td></tr>"

    error_row = ""
    if error_detail:
        error_row = f"<tr><td style='padding:4px 8px;color:#888;'>Error detail</td><td style='padding:4px 8px;color:#c0392b;'>{error_detail}</td></tr>"

    url_row = ""
    if service_url:
        safe_url = service_url.split("?")[0]
        url_row = f"<tr><td style='padding:4px 8px;color:#888;'>Service URL</td><td style='padding:4px 8px;word-break:break-all;'><a href='{safe_url}' style='color:#2980b9;'>{safe_url}</a></td></tr>"

    total   = stats.get("total", 0)
    online  = stats.get("online", 0)
    offline = stats.get("offline", 0)
    slow    = stats.get("slow", 0)

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.12);">

        <!-- Header -->
        <tr><td style="background:{color};padding:20px 28px;">
          <span style="font-size:22px;font-weight:bold;color:#fff;">{icon} ArcGIS Monitor: {level}</span>
        </td></tr>

        <!-- Service name -->
        <tr><td style="padding:20px 28px 8px;">
          <p style="margin:0;font-size:13px;color:#888;text-transform:uppercase;letter-spacing:.5px;">Service</p>
          <p style="margin:4px 0 0;font-size:20px;font-weight:bold;color:#222;">{service_name}</p>
        </td></tr>

        <!-- Detail table -->
        <tr><td style="padding:8px 28px 20px;">
          <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr><td style="padding:4px 8px;color:#888;">Status</td>
                <td style="padding:4px 8px;"><b style="color:{color};">{level}</b></td></tr>
            {url_row}
            {ping_row}
            {error_row}
            <tr><td style="padding:4px 8px;color:#888;">Detected at</td>
                <td style="padding:4px 8px;">{ts_local}</td></tr>
          </table>
        </td></tr>

        <!-- Divider -->
        <tr><td style="padding:0 28px;"><hr style="border:none;border-top:1px solid #eee;"></td></tr>

        <!-- Summary -->
        <tr><td style="padding:16px 28px 24px;">
          <p style="margin:0 0 10px;font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.5px;">Overall Status ({total} services)</p>
          <table cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:0 16px 0 0;text-align:center;">
                <span style="font-size:22px;font-weight:bold;color:#27ae60;">{online}</span><br>
                <span style="font-size:11px;color:#888;">Online</span>
              </td>
              <td style="padding:0 16px;text-align:center;">
                <span style="font-size:22px;font-weight:bold;color:#c0392b;">{offline}</span><br>
                <span style="font-size:11px;color:#888;">Offline</span>
              </td>
              <td style="padding:0 16px;text-align:center;">
                <span style="font-size:22px;font-weight:bold;color:#e67e22;">{slow}</span><br>
                <span style="font-size:11px;color:#888;">Slow</span>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#f9f9f9;padding:12px 28px;border-top:1px solid #eee;">
          <span style="font-size:11px;color:#aaa;">Sent by ArcGIS Service Monitor</span>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_alert_email(subject, message, service_url="", error_detail="", ping_ms=None, level="", service_name="", stats=None):
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

        now_utc = datetime.now(timezone.utc)
        html_body = _build_alert_html(
            level=level or subject,
            service_name=service_name or message,
            service_url=service_url,
            error_detail=error_detail,
            ping_ms=ping_ms,
            stats=stats or {},
            timestamp_utc=now_utc,
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(message, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with _smtp_connect(smtp_server, smtp_port, ignore_tls, disable_starttls) as server:
            if username and password:
                server.login(username, password)
            server.sendmail(from_email, recipients, msg.as_bytes())
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
    token_error  = None
    token_referer = ""

    if username and password:
        token, token_error, token_referer = get_arcgis_token(url, username, password, auth_type)
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
            req_headers = {"User-Agent": "ArcGISMonitor/1.0"}
            if token_referer:
                req_headers["Referer"] = token_referer
            req = urllib.request.Request(check_url, headers=req_headers)

            # อ่าน body เสมอ ไม่ว่า HTTP status จะเป็นอะไร
            # สถานะ Online/Offline ตัดสินจาก JSON content ไม่ใช่ HTTP code
            http_code = None
            content   = ""
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    http_code = resp.getcode()
                    content   = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as http_err:
                http_code = http_err.code
                try:
                    content = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    content = ""

            ping_ms = int((time.time() - start_time) * 1000)

            if not content.strip():
                status       = "Offline"
                error_detail = f"HTTP {http_code} — empty response"
            else:
                try:
                    json_resp    = json.loads(content)
                    has_creds    = bool(service.get("username"))

                    if "error" in json_resp:
                        err      = json_resp["error"]
                        err_code = err.get("code", 0)
                        err_msg  = err.get("message", f"code {err_code}")

                        if err_code in (499, 498) and not has_creds:
                            # Service reachable — เพียงแต่ต้องการ token
                            # ยังไม่ได้ตั้งค่า credentials → นับว่า Online
                            status       = "Slow" if ping_ms > 3000 else "Online"
                            error_detail = f"[{err_code}] {err_msg}"
                        else:
                            status       = "Offline"
                            error_detail = f"[{err_code}] {err_msg}"

                    elif "currentVersion" in json_resp or any(
                        k in json_resp for k in ("layers", "mapName", "serviceDescription",
                                                  "name", "pixelType", "tables")
                    ):
                        # Positive ArcGIS health indicator
                        status = "Slow" if ping_ms > 3000 else "Online"
                    else:
                        # JSON ตอบกลับแต่ไม่มี field ที่รู้จัก — ยังถือว่า Online
                        status = "Slow" if ping_ms > 3000 else "Online"

                except json.JSONDecodeError:
                    status       = "Offline"
                    error_detail = f"HTTP {http_code} — response is not JSON"

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
        svc_name = service.get("serviceName")
        if status == "Offline":
            alert = {"level": "Critical", "serviceName": svc_name, "serviceUrl": url,
                     "errorDetail": error_detail, "pingMs": ping_ms,
                     "message": f"Service {svc_name} went Offline."}
        elif status == "Online" and old_status in ["Offline", "Slow"]:
            alert = {"level": "Recovery", "serviceName": svc_name, "serviceUrl": url,
                     "errorDetail": "", "pingMs": ping_ms,
                     "message": f"Service {svc_name} recovered and is now Online."}
        elif status == "Slow":
            alert = {"level": "Warning",  "serviceName": svc_name, "serviceUrl": url,
                     "errorDetail": error_detail, "pingMs": ping_ms,
                     "message": f"Service {svc_name} is Slow ({ping_ms}ms)."}

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
            email_sent = send_alert_email(
                subject=f"ArcGIS Monitor: {alert['level']}",
                message=alert["message"],
                service_url=alert.get("serviceUrl", ""),
                error_detail=alert.get("errorDetail", ""),
                ping_ms=alert.get("pingMs"),
                level=alert["level"],
                service_name=alert["serviceName"],
                stats=stats,
            )
            telegram_sent = send_telegram_alert(
                level=alert["level"],
                service_name=alert["serviceName"],
                service_url=alert.get("serviceUrl", ""),
                error_detail=alert.get("errorDetail", ""),
                ping_ms=alert.get("pingMs"),
                stats=stats,
            )
            recent.append({
                "level":         alert["level"],
                "timestamp":     datetime.now(timezone.utc).isoformat(),
                "serviceName":   alert["serviceName"],
                "message":       alert["message"],
                "emailSent":     email_sent,
                "telegramSent":  telegram_sent,
            })
        data["recentAlerts"] = recent[-20:]
        save_config(data)
    except Exception as e:
        print(f"[FLUSH] Error saving results: {e}")


_LEVEL_EMOJI = {
    "Critical": "\U0001f534",
    "Warning":  "⚠️",
    "Recovery": "✅",
}

def send_telegram_alert(level, service_name, service_url="", error_detail="", ping_ms=None, stats=None):
    try:
        data = load_config()
        cfg = data.get("telegramConfig", {})
        bot_token = cfg.get("botToken", "")
        chat_id   = cfg.get("chatId", "")
        if not bot_token or not chat_id:
            return False

        emoji = _LEVEL_EMOJI.get(level, "")
        lines = [
            f"{emoji} <b>ArcGIS Monitor: {level}</b>",
            "",
            f"<b>Service:</b> {service_name}",
        ]
        if service_url:
            safe_url = service_url.split("?")[0]
            lines.append(f"<b>URL:</b> {safe_url}")
        if error_detail:
            lines.append(f"<b>Error:</b> {error_detail}")
        if ping_ms is not None:
            lines.append(f"<b>Response time:</b> {ping_ms} ms")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"<b>Detected at:</b> {ts}")

        if stats:
            total   = stats.get("total", 0)
            online  = stats.get("online", 0)
            offline = stats.get("offline", 0)
            slow    = stats.get("slow", 0)
            lines += ["", f"\U0001f4ca <b>Overall ({total} services):</b> {online} Online | {offline} Offline | {slow} Slow"]

        text = "\n".join(lines)
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"[TELEGRAM SENT] {level}: {service_name}")
        return True
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


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
    token_referer = ""

    if username and password:
        token, _, token_referer = get_arcgis_token(url, username, password, auth_type)
        if token:
            check_url += f"&token={token}"

    try:
        start    = time.time()
        req_headers = {"User-Agent": "ArcGISMonitor/1.0"}
        if token_referer:
            req_headers["Referer"] = token_referer
        req      = urllib.request.Request(check_url, headers=req_headers)
        http_code = None
        body      = ""
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                http_code = resp.getcode()
                body      = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as http_err:
            http_code = http_err.code
            try:
                body = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""

        ping_ms   = int((time.time() - start) * 1000)
        has_creds = bool(service.get("username"))

        if not body.strip():
            return {"id": service.get("id"), "pingMs": None, "status": "Offline",
                    "error": f"HTTP {http_code} — empty response"}

        try:
            j        = json.loads(body)
            err_obj  = j.get("error", {})
            err_code = err_obj.get("code", 0)

            if err_obj:
                if err_code in (499, 498) and not has_creds:
                    status = "Slow" if ping_ms > 3000 else "Online"
                else:
                    return {"id": service.get("id"), "pingMs": None, "status": "Offline",
                            "error": f"[{err_code}] {err_obj.get('message', '')}"}
            else:
                status = "Slow" if ping_ms > 3000 else "Online"
        except Exception:
            status = "Slow" if ping_ms > 3000 else "Online"

        return {"id": service.get("id"), "pingMs": ping_ms, "status": status}
    except urllib.error.URLError as e:
        return {"id": service.get("id"), "pingMs": None, "status": "Offline",
                "error": str(e.reason)}
    except Exception as e:
        return {"id": service.get("id"), "pingMs": None, "status": "Offline",
                "error": str(e)}


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

    def _require_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        user = pwd = ""
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                user, _, pwd = decoded.partition(":")
            except Exception:
                pass
        if verify_auth(user, pwd):
            return True
        body = json.dumps({"error": "Unauthorized"}).encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ArcGIS Service Monitor"')
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if not self._require_auth():
            return

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

        elif self.path == "/api/config/telegram":
            try:
                data = load_config()
                cfg = data.get("telegramConfig", {})
                self._send_json({
                    "hasBotToken": bool(cfg.get("botToken")),
                    "chatId": cfg.get("chatId", ""),
                })
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

        elif self.path == "/api/config/auth":
            self._send_json({
                "username": _AUTH_USER,
                "envControlled": _env_auth() is not None,
            })

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
        if not self._require_auth():
            return
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

            elif self.path == "/api/config/auth":
                if _env_auth() is not None:
                    self._send_json({"success": False,
                                      "error": "Login is controlled by AUTH_USERNAME/AUTH_PASSWORD env vars — change those and restart the service instead."}, 400)
                    return
                payload = self._read_json_body()
                username = payload.get("username", "").strip()
                password = payload.get("password", "")
                if not username or len(password) < 8:
                    self._send_json({"success": False, "error": "Username required, password must be at least 8 characters"}, 400)
                    return
                set_auth(username, password)
                self._send_json({"success": True})

            elif self.path == "/api/config/telegram":
                payload = self._read_json_body()
                data = load_config()
                existing = data.get("telegramConfig", {})
                cfg = {
                    "botToken": payload.get("botToken") or existing.get("botToken", ""),
                    "chatId":   payload.get("chatId",   existing.get("chatId", "")),
                }
                data["telegramConfig"] = cfg
                save_config(data)
                self._send_json({"success": True})

            elif self.path == "/api/config/telegram/test":
                payload = self._read_json_body()
                data = load_config()
                saved = data.get("telegramConfig", {})
                bot_token = payload.get("botToken") or saved.get("botToken", "")
                chat_id   = payload.get("chatId")   or saved.get("chatId", "")

                if not bot_token:
                    self._send_json({"success": False, "message": "กรุณาระบุ Bot Token"}); return
                if not chat_id:
                    self._send_json({"success": False, "message": "กรุณาระบุ Chat ID"}); return

                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                text = (
                    "✅ <b>ArcGIS Monitor — Test Message</b>\n\n"
                    f"Telegram alerts are configured correctly.\n"
                    f"<b>Sent at:</b> {now_str}"
                )
                tg_payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    data=tg_payload,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                    if result.get("ok"):
                        self._send_json({"success": True, "message": f"ส่งสำเร็จ → Chat ID: {chat_id}"})
                    else:
                        self._send_json({"success": False, "message": result.get("description", "Telegram API error")})
                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8")
                    try:
                        detail = json.loads(body).get("description", body)
                    except Exception:
                        detail = body
                    self._send_json({"success": False, "message": f"Telegram API error: {detail}"})
                except urllib.error.URLError as e:
                    self._send_json({"success": False, "message": f"Network error: {e.reason}"})

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
