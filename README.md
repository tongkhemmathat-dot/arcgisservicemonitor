# ArcGIS Service Monitor

Dashboard สำหรับตรวจสอบสถานะ ArcGIS REST Services แบบ Real-time  
พร้อมระบบแจ้งเตือนอีเมลเมื่อ service offline หรือ response ช้า

---

## สารบัญ

- [โครงสร้างไฟล์](#โครงสร้างไฟล์)
- [วิธี Deploy](#วิธี-deploy)
  - [1. Windows + IIS (On-Premise)](#1-windows--iis-on-premise)
  - [2. Docker — Single Container](#2-docker--single-container)
  - [3. Docker Compose — Multi-Environment](#3-docker-compose--multi-environment)
    - [Development](#development)
    - [Staging](#staging)
    - [Production](#production)
- [การตั้งค่าระบบ](#การตั้งค่าระบบ)
- [API Endpoints](#api-endpoints)
- [แก้ปัญหาเบื้องต้น](#แก้ปัญหาเบื้องต้น)

---

## โครงสร้างไฟล์

```
arcgisservicemonitor/
├── monitor_backend.py        API Server + Monitor Loop (backend)
├── index.html                Dashboard (frontend)
├── config.example.json       ตัวอย่าง config
│
├── Dockerfile                สำหรับ Docker build (backend)
├── docker-compose.yml        Base config (ใช้คู่กับ override ด้านล่าง)
├── docker-compose.dev.yml    Dev overrides
├── docker-compose.staging.yml  Staging overrides
├── docker-compose.prod.yml   Production overrides
├── nginx/
│   └── default.conf          nginx config (serve frontend + proxy /api/)
├── .dockerignore
│
├── web.config                IIS configuration (สำหรับ deploy แบบ IIS เท่านั้น)
└── start.bat                 รันแบบ Dev บน Windows
```

> `config.json` จะถูกสร้างอัตโนมัติเมื่อรัน backend ครั้งแรก  
> **อย่า commit `config.json`** เพราะมีข้อมูล credentials

---

## วิธี Deploy

### 1. Windows + IIS (On-Premise)

สำหรับ server Windows ที่ใช้ IIS อยู่แล้ว

**ความต้องการของระบบ**

| รายการ | เวอร์ชัน |
|--------|---------|
| Windows Server | 2016 / 2019 / 2022 |
| Python | 3.8 ขึ้นไป |
| IIS | 10 |
| IIS URL Rewrite Module | 2.1 ขึ้นไป |
| IIS ARR (Application Request Routing) | 3.0 ขึ้นไป |
| NSSM | 2.24 ขึ้นไป |

> Python ใช้ Standard Library เท่านั้น ไม่ต้อง pip install เพิ่มเติม

#### 1.1 ติดตั้ง Python

ดาวน์โหลดจาก https://python.org/downloads  
ติดตั้งโดยเลือก **"Add Python to PATH"** จากนั้นตรวจสอบ:

```cmd
python --version
```

#### 1.2 วางไฟล์บน Server

```cmd
mkdir C:\ArcGISMonitor
mkdir C:\ArcGISMonitor\logs
```

คัดลอก `monitor_backend.py`, `index.html`, `web.config` ไปไว้ที่ `C:\ArcGISMonitor\`

#### 1.3 รัน Backend เป็น Windows Service (NSSM)

ดาวน์โหลด NSSM จาก https://nssm.cc/download แล้ววาง `nssm.exe` ที่ `C:\tools\nssm.exe`

เปิด **Command Prompt as Administrator**:

```cmd
C:\tools\nssm.exe install ArcGISMonitor "C:\Python312\python.exe" "C:\ArcGISMonitor\monitor_backend.py"

C:\tools\nssm.exe set ArcGISMonitor AppDirectory "C:\ArcGISMonitor"
C:\tools\nssm.exe set ArcGISMonitor AppStdout "C:\ArcGISMonitor\logs\app.log"
C:\tools\nssm.exe set ArcGISMonitor AppStderr "C:\ArcGISMonitor\logs\app.log"
C:\tools\nssm.exe set ArcGISMonitor AppRotateFiles 1
C:\tools\nssm.exe set ArcGISMonitor AppRotateBytes 10485760
C:\tools\nssm.exe set ArcGISMonitor Start SERVICE_AUTO_START
C:\tools\nssm.exe set ArcGISMonitor DisplayName "ArcGIS Service Monitor"

C:\tools\nssm.exe start ArcGISMonitor
```

> หา path Python ด้วย `where python`

ตรวจสอบ backend ทำงาน:

```cmd
curl http://127.0.0.1:8000/api/monitor/dashboard
```

**คำสั่งจัดการ Service:**

```cmd
sc query ArcGISMonitor                        # ดูสถานะ
C:\tools\nssm.exe stop ArcGISMonitor          # หยุด
C:\tools\nssm.exe restart ArcGISMonitor       # รีสตาร์ท
C:\tools\nssm.exe remove ArcGISMonitor confirm  # ลบ
```

#### 1.4 ตั้งค่า IIS

1. ติดตั้ง **URL Rewrite Module**: https://www.iis.net/downloads/microsoft/url-rewrite  
2. ติดตั้ง **ARR**: https://www.iis.net/downloads/microsoft/application-request-routing  
3. เปิด Proxy ใน ARR (PowerShell as Administrator):

```powershell
& "$env:windir\system32\inetsrv\appcmd.exe" set config `
  -section:system.webServer/proxy /enabled:"True"
```

4. เปิด **IIS Manager** → Add Website:

| ฟิลด์ | ค่า |
|-------|-----|
| Site name | `ArcGISMonitor` |
| Physical path | `C:\ArcGISMonitor` |
| Port | `80` |

5. เปิด browser ที่ `http://localhost/` ควรเห็นหน้า Dashboard

> สำหรับ HTTPS: ติดตั้ง SSL Certificate ใน IIS แล้วเพิ่ม Binding แบบ `https` port 443  
> ไม่ต้องแก้ไข `web.config` หรือ backend เพิ่มเติม

#### 1.5 เปลี่ยน Port (กรณีจำเป็น)

ตั้ง environment variable `PORT` ก่อนรัน หรือตั้งใน NSSM:

```cmd
C:\tools\nssm.exe set ArcGISMonitor AppEnvironmentExtra "PORT=9000"
```

แล้วแก้ `web.config` บรรทัด proxy ให้ตรงกัน:

```xml
<action type="Rewrite" url="http://127.0.0.1:9000/api/{R:1}" />
```

จากนั้น restart:

```cmd
C:\tools\nssm.exe restart ArcGISMonitor
iisreset
```

---

### 2. Docker — Single Container

สำหรับ server ที่มี Docker และต้องการรันด้วยคำสั่งเดียว โดยไม่ใช้ nginx แยก  
Backend จะ serve `index.html` เองที่ `/` และ `/api/*` ผ่าน port เดียว

**ความต้องการ:** Docker Engine 20.10+

#### Build และรัน

```bash
docker build -t arcgis-monitor .

docker run -d \
  --name arcgis-monitor \
  -p 8000:8000 \
  -v arcgis-data:/data \
  --restart unless-stopped \
  arcgis-monitor
```

เปิด browser ที่ `http://<server-ip>:8000`

#### คำสั่งจัดการ

```bash
docker logs -f arcgis-monitor          # ดู logs
docker restart arcgis-monitor          # รีสตาร์ท
docker stop arcgis-monitor             # หยุด
docker rm arcgis-monitor               # ลบ container
```

#### เปลี่ยน Port

```bash
docker run -d -p 80:8000 -v arcgis-data:/data --restart unless-stopped arcgis-monitor
```

---

### 3. Docker Compose — Multi-Environment

สถาปัตยกรรมแบบ 2 container: **nginx** (frontend) + **Python** (backend)

```
Browser → nginx:80
             ├── GET /       → serve index.html
             └── GET /api/*  → proxy → backend:8000
                                          └── /data/config.json (volume)
```

**ความต้องการ:** Docker Engine 20.10+ และ Docker Compose v2

#### Config Volume แต่ละ Environment

| Environment | Named Volume | Port |
|-------------|-------------|------|
| dev | `arcgis-data` | 8080 (frontend), 8000 (backend) |
| staging | `arcgis-staging-data` | 8080 |
| prod | `arcgis-prod-data` | 80 |

แต่ละ environment ใช้ volume แยกกัน ทำให้ config และ alert history ไม่ปนกัน

---

#### Development

เหมาะสำหรับทดสอบบน local หรือ dev server  
- Frontend: `http://localhost:8080`  
- Backend เปิด port ตรงสำหรับ debug / Postman: `http://localhost:8000`

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

รันแบบ background:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

ดู logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f
```

หยุด:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

---

#### Staging

เหมาะสำหรับ UAT หรือ pre-production  
- Frontend: `http://<staging-server>:8080`  
- Backend ไม่เปิด port ออกนอก (เข้าถึงผ่าน nginx เท่านั้น)  
- `restart: unless-stopped`

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build
```

ดู logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml logs -f
```

อัปเดต (pull image ใหม่ + recreate):

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build --force-recreate
```

หยุด:

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml down
```

---

#### Production

เหมาะสำหรับ production server  
- Frontend: `http://<server>:80`  
- `restart: always` — รีสตาร์ทอัตโนมัติเมื่อ container หรือ host รีบูต  
- Volume `arcgis-prod-data` แยกจาก dev/staging

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

ดู logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
```

อัปเดต:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate
```

หยุด (volumes ยังคงอยู่):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

ลบ volume ด้วย (ข้อมูลหาย):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

> **HTTPS บน Production:** วาง reverse proxy เช่น Caddy หรือ Nginx Proxy Manager ด้านหน้า แล้วให้ชี้ไปที่ port 80 ของ container  
> หรือแก้ `docker-compose.prod.yml` เพิ่ม `443:443` และ mount SSL certificate เข้า nginx

---

## การตั้งค่าระบบ

เข้าไปที่หน้า Dashboard แล้วคลิก **⚙️ Settings** (มุมบนขวา)

### Map Services

| ฟิลด์ | คำอธิบาย |
|-------|---------|
| Service Name | ชื่อแสดงผล เช่น `NOSTRA Basemap` |
| Service URL | URL ของ ArcGIS service เช่น `https://gis.example.com/arcgis/rest/services/MyMap/MapServer` |
| Service Type | MapServer / FeatureServer / ImageServer / GeocodeServer |
| Username | (ถ้า service ต้องการ login) |
| Password | ระบบ generate ArcGIS Token อัตโนมัติ และ cache ไว้จนหมดอายุ |

### Email

| ฟิลด์ | ตัวอย่าง |
|-------|---------|
| SMTP Server | `smtp.gmail.com` หรือ `mail.company.com` |
| SMTP Port | `587` (TLS) หรือ `465` (SSL) |
| Email Username | `monitor@company.com` |
| Email Password | App Password (แนะนำสำหรับ Gmail) |
| From Email | `monitor@company.com` |
| Recipients | เพิ่มอีเมลผู้รับแจ้งเตือนได้หลายคน |

กดปุ่ม **🧪 ทดสอบส่งอีเมล** เพื่อตรวจสอบก่อนบันทึก

### Monitor Interval

ตั้งความถี่การตรวจสอบอัตโนมัติ (ค่าเริ่มต้น: 15 นาที)

| ค่า | เหมาะกับ |
|-----|---------|
| 1 นาที | ทดสอบ / critical systems |
| 5 นาที | production ทั่วไป |
| 15 นาที | production ที่ไม่ต้องการ load สูง |
| 30–60 นาที | monitoring เบาๆ |

---

## API Endpoints

| Method | Path | คำอธิบาย |
|--------|------|---------|
| GET | `/api/monitor/dashboard` | ข้อมูลทั้งหมด (services, stats, alerts) |
| POST | `/api/monitor/check` | trigger ตรวจสอบทันที |
| GET | `/api/monitor/ping` | live ping ทุก service (ไม่บันทึก) |
| POST | `/api/monitor/add` | เพิ่ม service |
| POST | `/api/monitor/update` | แก้ไข service |
| POST | `/api/monitor/delete` | ลบ service |
| GET | `/api/config/email` | ดู email config |
| POST | `/api/config/email` | บันทึก email config |
| POST | `/api/config/email/test` | ทดสอบส่งอีเมล |
| GET | `/api/config/interval` | ดู check interval |
| POST | `/api/config/interval` | ตั้ง check interval |

---

## แก้ปัญหาเบื้องต้น

### Backend ไม่ start (Windows)

```cmd
type C:\ArcGISMonitor\logs\app.log
python C:\ArcGISMonitor\monitor_backend.py
```

### IIS แสดง 502 Bad Gateway

```
สาเหตุ: ARR Proxy ติดต่อ backend ไม่ได้
แก้ไข:
  1. ตรวจสอบ service กำลังทำงาน:  sc query ArcGISMonitor
  2. ทดสอบ backend:               curl http://127.0.0.1:8000/api/monitor/dashboard
  3. ตรวจสอบ ARR Proxy เปิดอยู่:  %windir%\system32\inetsrv\appcmd list config -section:system.webServer/proxy
```

### IIS แสดง 500.52

```
สาเหตุ: ยังไม่ได้ติดตั้ง URL Rewrite module
แก้ไข: ติดตั้งจาก iis.net/downloads/microsoft/url-rewrite
```

### Docker — nginx แสดง 502 Bad Gateway

```bash
# ตรวจสอบ backend container ทำงานอยู่
docker compose -f docker-compose.yml -f docker-compose.<env>.yml ps

# ดู logs backend
docker compose -f docker-compose.yml -f docker-compose.<env>.yml logs backend
```

### Docker — ข้อมูลหายหลัง docker compose down

```bash
# ตรวจสอบว่า volume ยังอยู่
docker volume ls | grep arcgis

# ต้องใช้ down -v ถึงจะลบ volume — ถ้าไม่ได้ใส่ -v ข้อมูลยังอยู่
```

### Service ขึ้น Offline ทั้งที่ URL ถูกต้อง

```
สาเหตุที่เป็นไปได้:
  1. Username/Password ไม่ถูกต้อง — ดู lastError ใต้ชื่อ service บน Dashboard
  2. Token endpoint ไม่ใช่ /arcgis/tokens/generateToken
  3. Firewall ปิดกั้น outbound HTTPS จาก container หรือ server
  4. SSL Certificate ของ GIS server ไม่ valid
```

### ทดสอบการติดตั้ง

```
✅ เปิดหน้า Dashboard ได้
✅ GET /api/monitor/dashboard ได้ JSON กลับมา
✅ Service แสดงสถานะ Online/Offline
✅ กด "ตรวจสอบเดี๋ยวนี้" แล้ว sparkline อัปเดต
✅ Settings → Email → ทดสอบส่งอีเมล → สำเร็จ
```
