# 🌾 Rice Online News Automation Pipeline (V2)

ระบบอัตโนมัติ End-to-End สำหรับดึงหัวข้อข่าวข้าวล่าสุดจาก [riceonline.com](https://riceonline.com/), ค้นหาลิงก์ข่าวต้นฉบับด้วย **Gemini Search Grounding**, ดึงเนื้อหาข่าวและคลีนด้วย AI พร้อมสร้างรายงาน Word (`.docx`) โดยอัตโนมัติ

---

## 📁 โครงสร้างโปรเจกต์ (Clean Architecture)

```text
Rice Online News (V2)/
│
├── .github/workflows/
│   └── daily_scrape.yml      # CI/CD Workflow รันอัตโนมัติรายวันบน GitHub Actions
│
├── data/                     # เก็บข้อมูลชั่วคราวและ Checkpoint (ไม่ถูก Commit ขึ้น Git)
│   ├── raw_headlines.json    # หัวข้อข่าวที่ดึงจากหน้าเว็บ riceonline.com
│   └── resolved_urls.json    # URLs ข่าวต้นฉบับที่ค้นพบจาก Gemini Search Grounding
│
├── logs/                     # บันทึก Execution Logs
│   └── pipeline.log
│
├── output/                   # โฟลเดอร์เก็บรายงานผลลัพธ์
│   └── RiceNews_Report_*.docx
│
├── .env                      # เก็บ API Keys และค่าคอนฟิกส่วนตัว (ห้าม Commit ขึ้น Git)
├── .gitignore                # ป้องกันการส่งไฟล์สำคัญหรือไฟล์ชั่วคราวขึ้น GitHub
├── config.py                 # ค่าคอนฟิกกลาง (โมเดล, กฎ Domain Filter, Timeouts)
├── utils.py                  # ฟังก์ชันช่วย (RateLimiter, ตัวกรองโดเมน, จัดการไฟล์ Atomic)
├── raw_headline.py           # สเต็ปที่ 1: ดึงหัวข้อข่าวจาก riceonline.com
├── url_resolver.py           # สเต็ปที่ 2: ค้นหา URL ด้วย Gemini Search Grounding
├── article_scraper.py        # สเต็ปที่ 3: ดึงเนื้อหาข่าว & ให้ AI สรุปตัดโฆษณา
├── docx_generator.py         # สเต็ปที่ 4: สร้างไฟล์เอกสาร Word จัดฟอนต์สวยงาม
├── pipeline.py               # จุดสั่งการหลัก (Master Entry Point)
├── requirements.txt          # รายการ Library ที่โปรเจกต์ต้องใช้
└── README.md                 # คู่มือการใช้งาน
```

---

## 🚀 วิธีการรันบนเครื่อง Local Laptop

### 1. ทดสอบแบบรวดเร็ว (3 ข่าวแรก)
```powershell
python pipeline.py --test 3
```

### 2. รันเต็มรูปแบบทั้งกระบวนการ
```powershell
python pipeline.py
```

### 3. รันแยกเฉพาะสเต็ปที่ต้องการ
* ดึงเฉพาะหัวข้อข่าว:
  ```powershell
  python pipeline.py --step headlines
  ```
* ค้นหาเฉพาะ URLs จากหัวข้อที่มีอยู่แล้ว:
  ```powershell
  python pipeline.py --step resolve --test 5
  ```
* Scrape เนื้อหาข่าว:
  ```powershell
  python pipeline.py --step scrape
  ```
* สร้างเฉพาะรายงาน Word จากเนื้อหาที่ scrape แล้ว:
  ```powershell
  python pipeline.py --step report
  ```

---

## ☁️ การย้ายขึ้น GitHub Actions (เมื่อพร้อม)

1. สร้าง GitHub Repository (แนะนำให้เลือกเป็น **Private**)
2. ไปที่ **Settings** -> **Secrets and variables** -> **Actions** ใน GitHub Repo
3. เพิ่ม Repository Secrets ดังนี้:
   - `GEMINI_API_KEY`: API Key จาก Google AI Studio (จำเป็น)
   - `TAVILY_API_KEY`: API Key จาก Tavily Search (ทางเลือก แนะนำสำหรับ Fallback เพื่อความแม่นยำ 100%)
4. Push โค้ดทั้งหมดขึ้น GitHub
5. ระบบจะทำงานเองโดยอัตโนมัติทุกวันเวลา **07:00 น. (เวลาไทย)** หรือสามารถกดปุ่ม **Run workflow** ด้วยตนเองได้ตลอดเวลา

