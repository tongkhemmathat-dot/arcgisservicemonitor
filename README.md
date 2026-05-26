# ArcGIS Service Monitor

Dashboard สำหรับตรวจสอบสถานะ ArcGIS REST Services แบบ Real-time
พร้อมระบบแจ้งเตือนอีเมลเมื่อ service offline หรือ response ช้า

---

## สารบัญ

- [โครงสร้างไฟล์](#โครงสร้างไฟล์)
- [ความต้องการของระบบ](#ความต้องการของระบบ)
- [การติดตั้ง](#การติดตั้ง)
  - [1. ติดตั้ง Python](#1-ติดตั้ง-python)
  - [2. วางไฟล์บน Server](#2-วางไฟล์บน-server)
  - [3. รัน Backend เป็น Windows Service (NSSM)](#3-รัน-backend-เป็น-windows-service-nssm)
  - [4. ตั้งค่า IIS](#4-ตั้งค่า-iis)
- [การตั้งค่าระบบ](#การตั้งค่าระบบ)
- [API Endpoints](#api-endpoints)
- [แก้ปัญหาเบื้องต้น](#แก้ปัญหาเบื้องต้น)

---

## โครงสร้างไฟล์

```
ArcGISMonitor\
├── index.html           Dashboard (frontend)
├── monitor_backend.py   API Server + Monitor Loop (backend)
├── config.json          ข้อมูล services, settings, alert history
├── web.config           IIS configuration (Reverse Proxy)
├── start.bat            รันแบบ Dev / ทดสอบ
└── logs\
    └── app.log          Log จาก backend service
```

---

## ความต้องการของระบบ

| รายการ | เวอร์ชัน |
|--------|---------|
| Windows Server | 2016 / 2019 / 2022 |
| Python | 3.8 ขึ้นไป |
| IIS | 10 (เปิดใช้งาน Web Server role) |
| IIS URL Rewrite Module | 2.1 ขึ้นไป |
| IIS ARR (Application Request Routing) | 3.0 ขึ้นไป |
| NSSM | 2.24 ขึ้นไป |

> **หมายเหตุ:** Python ใช้ Standard Library เท่านั้น ไม่ต้องติดตั้ง package เพิ่มเติม

---

## การติดตั้ง

### 1. ติดตั้ง Python

1. ดาวน์โหลด Python จาก https://python.org/downloads
2. ติดตั้งโดยเลือก **"Add Python to PATH"**
3. ตรวจสอบการติดตั้ง:

```cmd
python --version
```

---

### 2. วางไฟล์บน Server

สร้างโฟลเดอร์และคัดลอกไฟล์:

```cmd
mkdir C:\ArcGISMonitor
mkdir C:\ArcGISMonitor\logs
```

คัดลอกไฟล์ทั้งหมดไปไว้ที่ `C:\ArcGISMonitor\`

> สามารถเปลี่ยน path ได้ตามต้องการ — backend จะสร้าง `config.json` ที่ตำแหน่งเดิมกับ script อัตโนมัติ

---

### 3. รัน Backend เป็น Windows Service (NSSM)

#### 3.1 ดาวน์โหลด NSSM

ดาวน์โหลดจาก https://nssm.cc/download แล้ววาง `nssm.exe` ไว้ที่ `C:\tools\nssm.exe`
(หรือ path อื่น แต่ต้องรู้ path ไว้ใช้ในขั้นตอนถัดไป)

#### 3.2 ติดตั้ง Service

เปิด **Command Prompt as Administrator** แล้วรัน:

```cmd
C:\tools\nssm.exe install ArcGISMonitor "C:\Python312\python.exe" "C:\ArcGISMonitor\monitor_backend.py"

C:\tools\nssm.exe set ArcGISMonitor AppDirectory "C:\ArcGISMonitor"

C:\tools\nssm.exe set ArcGISMonitor AppStdout "C:\ArcGISMonitor\logs\app.log"
C:\tools\nssm.exe set ArcGISMonitor AppStderr "C:\ArcGISMonitor\logs\app.log"
C:\tools\nssm.exe set ArcGISMonitor AppRotateFiles 1
C:\tools\nssm.exe set ArcGISMonitor AppRotateBytes 10485760

C:\tools\nssm.exe set ArcGISMonitor Start SERVICE_AUTO_START
C:\tools\nssm.exe set ArcGISMonitor DisplayName "ArcGIS Service Monitor"
C:\tools\nssm.exe set ArcGISMonitor Description "ArcGIS REST Service Health Monitor Backend"
```

> **หมายเหตุ:** เปลี่ยน `C:\Python312\python.exe` ให้ตรงกับ path Python ที่ติดตั้ง
> หาได้โดยรัน `where python` ใน Command Prompt

#### 3.3 เริ่ม Service

```cmd
C:\tools\nssm.exe start ArcGISMonitor
```

#### 3.4 คำสั่งจัดการ Service

```cmd
# ดูสถานะ
sc query ArcGISMonitor

# หยุด / รีสตาร์ท / ลบ
C:\tools\nssm.exe stop ArcGISMonitor
C:\tools\nssm.exe restart ArcGISMonitor
C:\tools\nssm.exe remove ArcGISMonitor confirm
```

ตรวจสอบว่า backend ทำงานแล้ว:

```cmd
curl http://127.0.0.1:8000/api/monitor/dashboard
```

ต้องได้รับ JSON response กลับมา

---

### 4. ตั้งค่า IIS

#### 4.1 เปิดใช้งาน IIS (ถ้ายังไม่ได้เปิด)

เปิด **Server Manager** → Add Roles and Features → Web Server (IIS)
เลือก features ขั้นต่ำ:
- Web Server → Common HTTP Features → Static Content, Default Document
- Web Server → Application Development (ไม่จำเป็น)

#### 4.2 ติดตั้ง URL Rewrite Module

ดาวน์โหลดและติดตั้งจาก:
https://www.iis.net/downloads/microsoft/url-rewrite

#### 4.3 ติดตั้ง ARR (Application Request Routing)

ดาวน์โหลดและติดตั้งจาก:
https://www.iis.net/downloads/microsoft/application-request-routing

#### 4.4 เปิดใช้งาน Proxy ใน ARR

เปิด **PowerShell as Administrator** แล้วรัน:

```powershell
& "$env:windir\system32\inetsrv\appcmd.exe" set config `
  -section:system.webServer/proxy /enabled:"True"
```

#### 4.5 สร้าง IIS Website

เปิด **IIS Manager**:

1. คลิกขวาที่ **Sites** → **Add Website**
2. ตั้งค่าดังนี้:

| ฟิลด์ | ค่า |
|-------|-----|
| Site name | `ArcGISMonitor` |
| Physical path | `C:\ArcGISMonitor` |
| Binding Type | `http` |
| Port | `80` |
| Host name | (ชื่อ domain หรือปล่อยว่าง) |

3. คลิก **OK**

#### 4.6 ตรวจสอบ IIS

เปิด browser แล้วเข้า `http://localhost/` หรือ `http://<server-ip>/`
ควรเห็นหน้า Dashboard

> **ถ้าต้องการ HTTPS:** ติดตั้ง SSL Certificate ใน IIS แล้วเพิ่ม Binding แบบ `https` port 443
> ไม่ต้องแก้ไข `web.config` หรือ backend เพิ่มเติม

---

## การตั้งค่าระบบ

เข้าไปที่หน้า Dashboard แล้วคลิก **Settings** (มุมบนขวา)

### Map Services

| ฟิลด์ | คำอธิบาย |
|-------|---------|
| Service Name | ชื่อแสดงผล เช่น `NOSTRA Basemap` |
| Service URL | URL ของ ArcGIS MapServer เช่น `https://gis.example.com/arcgis/rest/services/MyMap/MapServer` |
| Service Type | MapServer / FeatureServer / ImageServer / GeocodeServer |
| Username | (ถ้า service ต้องการ login) |
| Password | (ถ้า service ต้องการ login) — ระบบจะ generate ArcGIS Token อัตโนมัติ |

### Email (แจ้งเตือนเมื่อ service offline)

| ฟิลด์ | ตัวอย่าง |
|-------|---------|
| SMTP Server | `smtp.gmail.com` หรือ `mail.company.com` |
| SMTP Port | `587` (TLS) หรือ `465` (SSL) |
| Email Username | `monitor@company.com` |
| Email Password | password หรือ App Password |
| From Email | `monitor@company.com` |
| Recipients | เพิ่มอีเมลผู้รับแจ้งเตือน |

กดปุ่ม **🧪 ทดสอบส่งอีเมล** เพื่อตรวจสอบการตั้งค่าก่อนบันทึก

### Monitor

ตั้งความถี่การตรวจสอบ (ค่าเริ่มต้น: 15 นาที)

| ค่า | เหมาะกับ |
|-----|---------|
| 1 นาที | ทดสอบ / critical systems |
| 5 นาที | production ทั่วไป |
| 15 นาที | production ที่ไม่ต้องการ load สูง |
| 30–60 นาที | monitoring เบาๆ |

---

## API Endpoints

Backend ทำงานบน `http://127.0.0.1:8000` (เข้าถึงจากภายในเท่านั้น ผ่าน IIS Proxy)

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

### Backend ไม่ start

```cmd
# ดู log
type C:\ArcGISMonitor\logs\app.log

# ทดสอบรันตรง
python C:\ArcGISMonitor\monitor_backend.py
```

### IIS แสดง 502 Bad Gateway

```
สาเหตุ: ARR Proxy ติดต่อ backend ไม่ได้
แก้ไข:
  1. ตรวจสอบว่า service ArcGISMonitor กำลังทำงาน
     sc query ArcGISMonitor
  2. ทดสอบ backend โดยตรง
     curl http://127.0.0.1:8000/api/monitor/dashboard
  3. ตรวจสอบว่าเปิด ARR Proxy แล้ว
     %windir%\system32\inetsrv\appcmd list config -section:system.webServer/proxy
```

### IIS แสดง 500.52 (URL Rewrite error)

```
สาเหตุ: ยังไม่ได้ติดตั้ง URL Rewrite module
แก้ไข: ติดตั้ง URL Rewrite จาก iis.net/downloads/microsoft/url-rewrite
```

### Service ขึ้น Offline ทั้งที่ URL ถูกต้อง

```
สาเหตุที่เป็นไปได้:
  1. Username/Password ไม่ถูกต้อง — ดู lastError ใต้ชื่อ service
  2. Token endpoint ไม่ใช่ /arcgis/tokens/generateToken
     (บางระบบใช้ /arcgis/sharing/rest/generateToken)
  3. Firewall บน server ปิดกั้น outbound HTTPS
  4. SSL Certificate ของ GIS server ไม่ valid
```

### เปลี่ยน port จาก 8000

แก้ไขในไฟล์ `monitor_backend.py` บรรทัดสุดท้าย:

```python
run_server(host="127.0.0.1", port=9000)  # เปลี่ยนเป็น port ที่ต้องการ
```

และแก้ `web.config`:

```xml
<action type="Rewrite" url="http://127.0.0.1:9000/api/{R:1}" />
```

จากนั้น restart service:

```cmd
C:\tools\nssm.exe restart ArcGISMonitor
iisreset
```

---

## ทดสอบการติดตั้ง

```
✅ http://localhost/                          → เห็นหน้า Dashboard
✅ http://localhost/api/monitor/dashboard     → ได้ JSON
✅ Service แสดงสถานะ Online/Offline
✅ กด "ตรวจสอบเดี๋ยวนี้" แล้ว sparkline อัปเดต
✅ Settings → Email → ทดสอบส่งอีเมล → สำเร็จ
```
