# ArcGIS Service Monitor

Dashboard ตรวจสอบสถานะ ArcGIS REST Services แบบ Real-time  
พร้อมแจ้งเตือนอีเมล/Telegram เมื่อ service offline หรือตอบสนองช้า

> คู่มือผู้ใช้งานฉบับภาษาไทย (สำหรับผู้ใช้ dashboard ทั่วไป): [`ArcGIS_Service_Monitor_คู่มือผู้ใช้งาน.pdf`](./ArcGIS_Service_Monitor_คู่มือผู้ใช้งาน.pdf) — หรือดาวน์โหลดจาก [GitHub Releases](https://github.com/tongkhemmathat-dot/arcgisservicemonitor/releases)

---

## สารบัญ

- [โครงสร้างโปรเจกต์](#โครงสร้างโปรเจกต์)
- [วิธีติดตั้ง](#วิธีติดตั้ง)
  - [1. Windows — EXE Installer](#1-windows--exe-installer)
  - [2. Windows — IIS + NSSM](#2-windows--iis--nssm)
  - [3. Docker](#3-docker)
    - [Production](#production)
    - [ใช้กับ nginx ที่มีอยู่แล้ว](#ใช้กับ-nginx-ที่มีอยู่แล้ว-existing-reverse-proxy)
    - [Demo](#demo-มี-sample-services-พร้อมใช้)
- [การตั้งค่า](#การตั้งค่า)
- [API Reference](#api-reference)
- [แก้ปัญหา](#แก้ปัญหา)

---

## โครงสร้างโปรเจกต์

```
arcgisservicemonitor/
├── monitor_backend.py        Python backend — HTTP server + monitor loop
├── index.html                Dashboard (frontend — single page)
├── requirements.txt          Python dependencies (cryptography)
├── config.example.json       ตัวอย่าง config
│
├── windows/
│   ├── start.bat             รัน backend ตรงๆ (dev/test)
│   ├── web.config            IIS config — สำหรับ IIS+NSSM (serve index.html + proxy /api/)
│   ├── web.config.proxy      IIS config — สำหรับ EXE+IIS (proxy ทุก request ไป port 8000)
│   └── installer/
│       ├── build.bat         สคริปต์ build installer (รันบนเครื่อง build)
│       ├── build.spec        PyInstaller spec
│       └── installer.iss     Inno Setup script
│
└── docker/
    ├── Dockerfile            Production image (Python backend)
    ├── Dockerfile.demo       Demo image (มี sample services ในตัว)
    ├── docker-compose.yml    Production stack (nginx + backend)
    └── nginx/
        └── default.conf      nginx — serve frontend + proxy /api/
```

> `config.json`, `encryption.key` และ `auth.json` สร้างอัตโนมัติเมื่อรันครั้งแรก — **อย่า commit**

---

## วิธีติดตั้ง

### 1. Windows — EXE Installer

ติดตั้งแบบ wizard **ไม่ต้องมี Python, IIS หรือ ARR** บน target server  
Backend ถูก bundle เป็น `.exe` และลงทะเบียนเป็น Windows Service อัตโนมัติ

**สิ่งที่ต้องมีบนเครื่อง build:**
- Python 3.8+
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)

#### Build

```cmd
windows\installer\build.bat
```

สคริปต์จะ:
1. ติดตั้ง PyInstaller + cryptography
2. Bundle `monitor_backend.py` + `index.html` → `windows\installer\dist\ArcGISMonitor\`
3. เรียก Inno Setup สร้าง installer อัตโนมัติ

ไฟล์ผลลัพธ์: `windows\installer\output\ArcGISMonitor-Setup-1.0.3.exe`

#### ติดตั้งบน Windows Server

1. คัดลอก `ArcGISMonitor-Setup-1.0.3.exe` ไปยัง server
2. Double-click → เลือก path ที่ต้องการ
3. **เลือก Port** — wizard จะถามให้เลือก:

| ตัวเลือก | Dashboard URL | ต้องการ IIS/ARR? |
|----------|---------------|-----------------|
| **Port 80** | `http://<server>/` | ไม่ต้อง |
| **Port 8000** | `http://<server>:8000/` | ไม่ต้อง |

4. กด Install — Installer จัดการให้อัตโนมัติ:
   - ติดตั้งไฟล์ไปที่ path ที่เลือก (default: `C:\ArcGISMonitor`)
   - ลงทะเบียน Windows Service `ArcGISMonitor` (auto-start) บน port ที่เลือก
   - เปิด port ที่เลือกใน Windows Firewall
   - Start service และเปิด Dashboard ใน browser

> **Port 80** เหมาะสำหรับ production — ไม่ต้องพิมพ์ port ใน URL และไม่ต้องติดตั้ง IIS, ARR, หรือ reverse proxy ใดๆ  
> หาก server นั้นมี IIS ใช้งาน port 80 อยู่แล้ว ให้เลือก port 8000 แทน

#### จัดการ Service

```cmd
sc start ArcGISMonitor    # เริ่ม
sc stop  ArcGISMonitor    # หยุด
sc query ArcGISMonitor    # ดูสถานะ
```

**Log:** `<install_path>\logs\app.log`

#### Upgrade (เมื่อมีเวอร์ชันใหม่)

รัน installer ตัวใหม่ทับของเดิมได้เลย ไม่ต้อง uninstall ก่อน — `AppId` เป็น GUID คงที่ และ `config.json` / `encryption.key` / `auth.json` / `logs\` ไม่ได้ถูก bundle เข้า installer จึงไม่ถูกเขียนทับ ข้อมูล service/config/login เดิมยังอยู่ครบ

1. แก้เลขเวอร์ชันใน `windows\installer\installer.iss`:
   ```
   #define AppVersion   "1.0.1"
   ```
2. Build: `windows\installer\build.bat` → ได้ `output\ArcGISMonitor-Setup-1.0.1.exe`
3. คัดลอกไปที่ server แล้ว double-click รันทับ — installer จะ `sc stop` ก่อนเขียนไฟล์ เขียนทับเฉพาะ `.exe` + `index.html` แล้ว `sc start` ให้อัตโนมัติ
4. ตรวจสอบ: `sc query ArcGISMonitor` และเปิด dashboard ดูว่า config/service เดิมยังอยู่

ไม่ต้องตั้งค่าใหม่ใน Settings หลังอัปเกรด

#### Uninstall

Control Panel → Programs → ArcGIS Service Monitor → Uninstall  
Uninstaller หยุดและลบ Windows Service + Firewall rule อัตโนมัติ

---

#### ใช้งานร่วมกับ IIS (HTTPS / Custom Domain)

ใช้เฉพาะกรณีที่ต้องการ HTTPS หรือ custom domain — ไม่บังคับ  
Backend ทำงานบน port 8000, IIS ทำหน้าที่เป็น reverse proxy รับบน port 443

```
ผู้ใช้ → IIS :443 (HTTPS) → ArcGISMonitor.exe :8000
```

**สิ่งที่ต้องมี:**
- [IIS URL Rewrite Module](https://www.iis.net/downloads/microsoft/url-rewrite)
- [IIS ARR (Application Request Routing)](https://www.iis.net/downloads/microsoft/application-request-routing)

**ขั้นตอน:**

**1. ติดตั้ง URL Rewrite + ARR แล้วเปิด Proxy** (PowerShell as Administrator):

```powershell
& "$env:windir\system32\inetsrv\appcmd.exe" set config -section:system.webServer/proxy /enabled:"True"
```

**2. สร้าง IIS Website**

เปิด IIS Manager → Add Website:

| ฟิลด์ | ค่า |
|-------|-----|
| Site name | `ArcGISMonitor` |
| Physical path | folder เปล่าใดก็ได้ เช่น `C:\inetpub\arcgismonitor` |
| Port | `443` (HTTPS) |
| Host name | domain ของ server |

**3. วาง web.config**

คัดลอก `windows\web.config.proxy` ไปวางที่ physical path แล้วเปลี่ยนชื่อเป็น `web.config`:

```cmd
copy windows\web.config.proxy C:\inetpub\arcgismonitor\web.config
```

**4. ทดสอบ**

```cmd
curl https://monitor.example.com/api/monitor/dashboard
```

ควรได้ JSON กลับมา — เข้า dashboard ที่ `https://monitor.example.com/`

**ตั้งค่า HTTPS:**

IIS Manager → Sites → ArcGISMonitor → Bindings → Add → Type: `https` → เลือก SSL Certificate  
ไม่ต้องแก้ไขไฟล์ใดๆ เพิ่มเติม

> **หมายเหตุ:** `web.config` นี้ proxy **ทุก request** ไปที่ port 8000 ต่างจาก `windows\web.config` ที่ serve `index.html` เองและ proxy เฉพาะ `/api/`

---

### 2. Windows — IIS + NSSM

สำหรับ server ที่ใช้ IIS อยู่แล้วและต้องการ serve บน port 80/443

**ความต้องการ:**

| รายการ | เวอร์ชัน |
|--------|---------|
| Windows Server | 2016 / 2019 / 2022 |
| Python | 3.8+ |
| IIS | 10 พร้อม URL Rewrite + ARR |
| NSSM | 2.24+ |

> Python ใช้เฉพาะ standard library + `cryptography` — รัน `pip install -r requirements.txt`

#### 2.1 ติดตั้ง dependencies

```cmd
pip install -r requirements.txt
```

วางไฟล์ `monitor_backend.py`, `index.html`, `windows\web.config` ไปที่ install path เช่น `C:\ArcGISMonitor\`

#### 2.2 ลงทะเบียน Windows Service ด้วย NSSM

```cmd
nssm install ArcGISMonitor "C:\Python312\python.exe" "C:\ArcGISMonitor\monitor_backend.py"
nssm set ArcGISMonitor AppDirectory  "C:\ArcGISMonitor"
nssm set ArcGISMonitor AppStdout     "C:\ArcGISMonitor\logs\app.log"
nssm set ArcGISMonitor AppStderr     "C:\ArcGISMonitor\logs\app.log"
nssm set ArcGISMonitor AppRotateFiles 1
nssm set ArcGISMonitor AppRotateBytes 10485760
nssm set ArcGISMonitor Start SERVICE_AUTO_START
nssm start ArcGISMonitor
```

> หา path Python ด้วย `where python`

ตรวจสอบ: `curl http://127.0.0.1:8000/api/monitor/dashboard`

#### 2.3 ตั้งค่า IIS

1. ติดตั้ง [URL Rewrite](https://www.iis.net/downloads/microsoft/url-rewrite) และ [ARR](https://www.iis.net/downloads/microsoft/application-request-routing)
2. เปิด ARR Proxy (PowerShell as Administrator):
   ```powershell
   & "$env:windir\system32\inetsrv\appcmd.exe" set config -section:system.webServer/proxy /enabled:"True"
   ```
3. สร้าง IIS Website ชี้ไปที่ install path, port 80
4. IIS จะอ่าน `windows\web.config` และ proxy `/api/*` → `http://127.0.0.1:8000` อัตโนมัติ

**Dashboard:** `http://localhost/`

---

### 3. Docker

สถาปัตยกรรม: **nginx** (port 80) → proxy `/api/` → **Python backend** (port 8000 ภายใน)

**ความต้องการ:** Docker Engine 20.10+ และ Docker Compose v2

#### Production

```bash
cd docker
docker compose up -d --build
```

**Dashboard:** `http://<server-ip>`

```bash
docker compose logs -f               # ดู logs
docker compose pull && docker compose up -d --force-recreate  # อัปเดต
docker compose down                  # หยุด (volumes ยังอยู่)
docker compose down -v               # หยุด + ลบ volumes
```

#### ใช้กับ nginx ที่มีอยู่แล้ว (Existing Reverse Proxy)

สำหรับกรณีที่มี nginx container ทำหน้าที่เป็น reverse proxy อยู่แล้วในระบบ  
จะรันเฉพาะ backend container และให้ nginx เดิมจัดการ routing แทน

```
ผู้ใช้ → nginx (port 80/443) → arcgismonitor-backend:8000
```

**ขั้นตอน:**

**1. หาชื่อ Docker network ของ nginx ที่มีอยู่**

```bash
docker inspect <nginx-container-name> --format '{{json .NetworkSettings.Networks}}' | jq 'keys'
```

**2. สร้าง `docker-compose.custom.yml`** ที่ root ของ project:

```yaml
services:
  backend:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: arcgismonitor-backend
    restart: unless-stopped
    environment:
      HOST: "0.0.0.0"
      PORT: "8000"
      CONFIG_PATH: "/data/config.json"
      ENCRYPTION_KEY: "${ENCRYPTION_KEY:-}"
    volumes:
      - arcgis-data:/data
    networks:
      - proxy        # ← เปลี่ยนเป็นชื่อ network ของ nginx ที่มีอยู่

volumes:
  arcgis-data:

networks:
  proxy:
    external: true   # ← ใช้ network ที่มีอยู่แล้ว ไม่สร้างใหม่
```

> เปลี่ยน `proxy` ให้ตรงกับชื่อ network จริงของระบบ

**3. เพิ่ม location block ใน nginx config ที่มีอยู่**

กรณี ArcGISMonitor อยู่ที่ **subdomain** เช่น `monitor.yourdomain.com`:

```nginx
server {
    listen 80;
    server_name monitor.yourdomain.com;

    # Serve frontend
    location / {
        root  /usr/share/nginx/html/arcgismonitor;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # Proxy API → backend container
    location /api/ {
        proxy_pass            http://arcgismonitor-backend:8000;
        proxy_http_version    1.1;
        proxy_set_header      Host              $host;
        proxy_set_header      X-Real-IP         $remote_addr;
        proxy_set_header      X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto $scheme;
        proxy_read_timeout    30s;
        proxy_connect_timeout 5s;
    }
}
```

กรณี ArcGISMonitor อยู่ที่ **subpath** เช่น `yourdomain.com/monitor/`:

```nginx
location /monitor/ {
    alias /usr/share/nginx/html/arcgismonitor/;
    index index.html;
    try_files $uri $uri/ /monitor/index.html;
}

location /monitor/api/ {
    rewrite ^/monitor/api/(.*)$ /api/$1 break;
    proxy_pass            http://arcgismonitor-backend:8000;
    proxy_http_version    1.1;
    proxy_set_header      Host              $host;
    proxy_set_header      X-Real-IP         $remote_addr;
    proxy_set_header      X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header      X-Forwarded-Proto $scheme;
    proxy_read_timeout    30s;
    proxy_connect_timeout 5s;
}
```

**4. Mount `index.html` เข้า nginx container ที่มีอยู่**

ใน compose ของ nginx เดิม เพิ่ม volume:

```yaml
volumes:
  - /path/to/ArcGISMonitor/index.html:/usr/share/nginx/html/arcgismonitor/index.html:ro
```

**5. รัน backend และ reload nginx**

```bash
# รัน backend
docker compose -f docker-compose.custom.yml up -d --build

# Reload nginx (ไม่ต้อง restart)
docker exec <nginx-container-name> nginx -s reload
```

**ทดสอบ:**

```bash
curl http://<server-ip>/api/monitor/dashboard
```

ควรได้ JSON กลับมา — เข้า dashboard ที่ `http://<server-ip>/`

> **หมายเหตุ:** ไม่มีการ expose port 8000 ออกนอก Docker network —  
> nginx ติดต่อ backend ผ่าน Docker internal network เท่านั้น ปลอดภัยกว่าการ bind port

---

#### Demo (มี sample services พร้อมใช้)

```bash
docker build -f docker/Dockerfile.demo -t arcgis-demo .
docker run -d -p 8000:8000 --name arcgis-demo arcgis-demo
```

**Dashboard:** `http://localhost:8000`

มี 5 public ESRI services ให้ดูได้เลย, ตรวจสอบทุก 2 นาที

#### Encryption Key (Docker)

Backend สร้าง encryption key อัตโนมัติใน volume ครั้งแรก ไม่ต้องตั้งค่า  
สำหรับ production ที่ต้องการควบคุม key เอง:

```bash
# สร้าง key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ใส่ใน .env
cp .env.example .env
# แก้ ENCRYPTION_KEY=<key>
```

---

## การตั้งค่า

เปิด Dashboard → คลิก **⚙️ Settings**

### Map Services

| ฟิลด์ | คำอธิบาย |
|-------|---------|
| Service Name | ชื่อแสดงผล |
| Service URL | URL ของ ArcGIS REST endpoint |
| Service Type | MapServer / FeatureServer / ImageServer / GeocodeServer |
| Username / Password | กรณี service ต้องการ login — ระบบ generate ArcGIS Token อัตโนมัติและเข้ารหัสก่อนเก็บ |

### Email Alert

| ฟิลด์ | ตัวอย่าง |
|-------|---------|
| SMTP Server | `smtp.gmail.com` |
| SMTP Port | `587` (TLS) / `465` (SSL) |
| Username | `monitor@company.com` |
| Password | App Password |
| Recipients | เพิ่มได้หลายคน |

กด **🧪 ทดสอบส่งอีเมล** เพื่อตรวจสอบก่อนบันทึก

### Telegram Alert

| ฟิลด์ | คำอธิบาย |
|-------|---------|
| Bot Token | สร้างผ่าน [@BotFather](https://t.me/BotFather) ใน Telegram |
| Chat ID | แชทส่วนตัวหาได้จาก [@userinfobot](https://t.me/userinfobot) · กลุ่ม/ช่องขึ้นต้นด้วย `-100` |

กด **🧪 ทดสอบส่งข้อความ** เพื่อตรวจสอบก่อนบันทึก

### Check Interval

ความถี่การตรวจสอบอัตโนมัติ (ค่าเริ่มต้น 15 นาที) และ stagger delay (หน่วงเวลาระหว่าง service แต่ละตัวเพื่อกระจาย network load)  
เปลี่ยนแล้วมีผลรอบถัดไปทันที ไม่ต้อง restart

### Login

Dashboard ทุกหน้า/ทุก API endpoint ถูกล็อกด้วย HTTP Basic Auth

- **รันครั้งแรก:** ระบบสุ่ม username `admin` + password อัตโนมัติ แล้วพิมพ์ออกทาง console/log (`<install_path>\logs\app.log` บน Windows) — เก็บรหัสผ่านนี้ไว้แล้วเข้าไปเปลี่ยนที่ **Settings → Login** ทันที
- **เปลี่ยนรหัสผ่าน:** Settings → Login → กรอก username/password ใหม่ (อย่างน้อย 8 ตัวอักษร) → มีผลทันที ครั้งถัดไปที่โหลดหน้าเว็บ browser จะถาม login ใหม่
- **บังคับ credential ระดับ deployment:** ตั้ง environment variable `AUTH_USERNAME` และ `AUTH_PASSWORD` ก่อนสตาร์ท backend — เมื่อตั้งแล้วจะ override ค่าที่บันทึกไว้ใน `auth.json` เสมอ และปิดการเปลี่ยนรหัสผ่านผ่านหน้าเว็บ (ต้องแก้ env var + restart service แทน)
- `auth.json` เก็บ username + password hash (PBKDF2) ไว้ที่ install path เดียวกับ `config.json` — **อย่า commit**

---

## API Reference

| Method | Path | คำอธิบาย |
|--------|------|---------|
| `GET` | `/api/monitor/dashboard` | ข้อมูลทั้งหมด (services, stats, alerts) |
| `POST` | `/api/monitor/check` | trigger ตรวจสอบทันที |
| `GET` | `/api/monitor/ping` | live ping ทุก service (ไม่บันทึก) |
| `POST` | `/api/monitor/add` | เพิ่ม service |
| `POST` | `/api/monitor/update` | แก้ไข service |
| `POST` | `/api/monitor/delete` | ลบ service |
| `GET` | `/api/config/email` | ดู email config |
| `POST` | `/api/config/email` | บันทึก email config |
| `POST` | `/api/config/email/test` | ทดสอบส่งอีเมล |
| `GET` | `/api/config/telegram` | ดู Telegram config |
| `POST` | `/api/config/telegram` | บันทึก Telegram config |
| `POST` | `/api/config/telegram/test` | ทดสอบส่งข้อความ Telegram |
| `GET` | `/api/config/interval` | ดู check interval / stagger delay |
| `POST` | `/api/config/interval` | ตั้ง check interval / stagger delay |
| `GET` | `/api/config/auth` | ดู login username (ไม่คืน password) + สถานะ env override |
| `POST` | `/api/config/auth` | เปลี่ยน login username/password |

> ทุก endpoint ด้านบนต้องแนบ HTTP Basic Auth header — ไม่มี auth จะได้ `401 Unauthorized`

> Passwords ไม่ถูกส่งกลับจาก API — dashboard response แสดงเป็น `"password": "********"` เสมอ

---

## แก้ปัญหา

### Service ไม่ start (Windows)

```cmd
sc query ArcGISMonitor
type <install_path>\logs\app.log
```

### IIS 502 Bad Gateway (EXE + IIS)

```cmd
REM 1. ตรวจสอบ service ทำงานอยู่
sc query ArcGISMonitor

REM 2. ทดสอบ backend ตรงๆ (ข้าม IIS)
curl http://127.0.0.1:8000/api/monitor/dashboard

REM 3. ตรวจสอบ ARR Proxy เปิดอยู่
%windir%\system32\inetsrv\appcmd list config -section:system.webServer/proxy
```

ถ้าขั้นที่ 2 ผ่านแต่ IIS ยังเป็น 502 → ปัญหาอยู่ที่ ARR config ให้ตรวจสอบขั้นที่ 3

### Docker — nginx 502

```bash
docker compose ps
docker compose logs backend
```

### Docker — nginx 502 (Existing Reverse Proxy)

```bash
# 1. ตรวจสอบ backend container ทำงานอยู่
docker ps | grep arcgismonitor-backend

# 2. ตรวจสอบว่า backend อยู่ใน network เดียวกับ nginx
docker network inspect <network-name> | grep arcgismonitor

# 3. ทดสอบ backend จากภายใน nginx container
docker exec <nginx-container-name> wget -qO- http://arcgismonitor-backend:8000/api/monitor/dashboard
```

ถ้าขั้นที่ 3 ล้มเหลว → backend ยังไม่ได้อยู่ใน network เดียวกัน  
ตรวจสอบ `networks:` ใน `docker-compose.custom.yml` ว่าชื่อตรงกับ network จริง

### service ขึ้น Offline ทั้งที่ URL ถูก

```
1. ดู lastError ใต้ชื่อ service บน Dashboard
2. Username/Password ไม่ถูกต้อง
3. Firewall ปิดกั้น outbound HTTPS จาก server
4. SSL certificate ของ GIS server ไม่ valid
```

### encryption key หาย (Docker)

```
สาเหตุ: volume ถูกลบด้วย down -v
ผลกระทบ: password ที่เข้ารหัสไว้อ่านไม่ได้ — ต้องใส่ password ใหม่ใน Settings
ป้องกัน: backup /data/encryption.key หรือใช้ ENCRYPTION_KEY env var
```

### ลืม login เข้า Dashboard

หน้าเว็บไม่มีทาง reset รหัสผ่านด้วยตัวเอง ต้องแก้จากฝั่งเซิร์ฟเวอร์:

```
1. ถ้าใช้ AUTH_USERNAME/AUTH_PASSWORD env var → แก้ค่าใน env var แล้ว restart service
2. ถ้าไม่ได้ตั้ง env var → ลบไฟล์ auth.json ที่ install path แล้ว restart service
   ระบบจะสุ่ม username "admin" + password ใหม่ให้อัตโนมัติ (ดูได้จาก logs\app.log)
```
