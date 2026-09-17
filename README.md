# สานศัพท์ (SarnSap)

โค้ดต้นแบบสำหรับค้นความหมายคำไทยจากหลายแหล่งข้อมูล โดยเก็บแต่ละระเบียนและแหล่งที่มาแยกกันใน Knowledge Graph แล้วให้ Backend ตรวจหลักฐานก่อนส่งคำตอบ หน้าเว็บไม่ติดต่อ GraphDB โดยตรง

โฟลเดอร์นี้เป็น **โปรเจกต์ใหม่ที่แยกจากงาน Hackathon** ไม่มีประวัติ Git ของงานแข่ง ไม่มีไฟล์ข้อมูลจากผู้จัด ไม่มีไฟล์ลับหรือรายงานทีม ข้อมูลตัวอย่างที่อยู่ใน repo เป็นข้อมูลสังเคราะห์เพื่อทดสอบระบบเท่านั้น ไม่ใช่นิยามจากหน่วยงานใด

## เริ่มใช้งานในเครื่อง (Windows / PowerShell)

ต้องมี Docker Desktop, Python 3.13, PowerShell และ GraphDB 11.5 Free license ของตนเอง วาง license ที่ `secrets/graphdb.license` (โฟลเดอร์นี้ถูก Git ignore)

```powershell
Copy-Item .env.example .env
docker compose up -d
./scripts/init-graphdb.ps1
docker compose --profile frontend up -d --build
```

เปิดหน้าเว็บที่ `http://localhost:3000` และ GraphDB Workbench ที่ `http://localhost:7200` ตัวอย่างเริ่มต้นใน GraphDB เป็นข้อมูลสังเคราะห์ `data/sample/thailex-sample.ttl` หากยังไม่ตั้งค่า API ของ LLM ระบบใช้ตัวเลือกแบบ heuristic ตามหลักฐานในกราฟ ไม่ได้อ้างว่าเรียก LLM

หากต้องการใช้ ThaiLLM ให้ใส่ `THAILLM_API_KEY` ใน `.env` และตั้ง `THAILEX_SELECTOR_MODE=thaillm` ด้วยตัวเอง ห้าม commit `.env` หรือ key การเปิดโหมดนี้จะส่งคำถาม ประวัติสนทนา และข้อความหลักฐานบางส่วนจากชุดข้อมูล local ไปยัง API ภายนอก จึงต้องตรวจสิทธิ์และความเหมาะสมของข้อมูลก่อน

เมื่อเปิด ThaiLLM โมเดลจะเลือกความหมายและเขียนคำตอบตามคำถาม/บริบทสนทนาในหนึ่ง API call โดย Backend ตรวจ Sense และรหัสหลักฐานก่อนแสดงผล หากโมเดลไม่ส่งคำตอบที่ใช้ได้ ระบบจะใช้ข้อความจากกราฟแทน สถานะ `answer_mode` ใน API บอกที่มาของรูปแบบคำตอบ การตรวจรหัสหลักฐานไม่ใช่การพิสูจน์ว่าข้อความอิสระของโมเดลถูกต้องทุกคำ

หยุดระบบด้วย `./scripts/stop.ps1` โดยไม่ลบ Docker volume

## เพิ่มชุดข้อมูลจากเครื่อง

รองรับไฟล์ `CSV`, `JSON`, `JSONL`, `XML` ผ่าน mapping ของแต่ละชุดข้อมูล โครงสร้างตัวอย่างอยู่ใน `examples/` และ schema อยู่ใน `schemas/organizer-mapping.schema.json` ไฟล์จริงกับ mapping ที่มีรายละเอียดแหล่งข้อมูลควรเก็บใน `data/local/` ซึ่ง Git ignore

```powershell
./scripts/ingest-organizer.ps1 -InputPath ./examples/sample-dictionary.csv -MappingPath ./examples/sample-dictionary.mapping.json
```

สคริปต์ทำ audit → normalize → validate → RDF → import เข้า named graph ตาม `source.id` และ `edition_id` หาก graph เดิมมีข้อมูลแล้ว สคริปต์จะหยุดโดยไม่ลบข้อมูล หากตั้งใจแทนที่ให้ใช้ `-ReplaceExisting` ซึ่งจะสำรอง graph เดิมใน `data/rdf/backup/` ก่อนนำเข้า ไฟล์นั้นก็ถูก Git ignore

`XLSX` และ `DOCX` ยังไม่มีตัวอ่านทั่วไปในโปรเจกต์นี้ ต้องแปลงเป็นรูปแบบข้างต้นหรือเพิ่ม parser เฉพาะแหล่งก่อน ห้ามอ้างว่ารองรับทุกชุดข้อมูลโดยไม่ทำ mapping และตรวจสิทธิ์

อ่านขั้นตอนและข้อจำกัดเพิ่มเติมใน [คู่มือนำเข้าข้อมูล](docs/LOCAL_DATASETS.md)

## โครงสร้าง

- `frontend/` — Next.js UI และกราฟ Cytoscape.js
- `backend/` — FastAPI, การค้น SPARQL, เลือกความหมาย, ตรวจหลักฐาน
- `src/thailex_ingestion/` — adapters และการสร้าง RDF
- `ontology/`, `schemas/` — แบบจำลองและ schema
- `examples/` — ข้อมูลสังเคราะห์สำหรับทดลอง
- `scripts/` — เริ่มระบบ ทดสอบ นำเข้า ตรวจผล

ดู [สถาปัตยกรรม](docs/ARCHITECTURE.md) และ [นโยบายข้อมูล](docs/DATA_POLICY.md)

## ทดสอบ

```powershell
$env:PYTHONPATH='src;backend'
python -m unittest discover -s tests -q
python -m unittest discover -s backend/tests -q
./scripts/test-frontend.ps1
```

## ก่อนเผยแพร่บน GitHub

โค้ดและข้อมูลเป็นคนละสิทธิ์กัน ตรวจสิทธิ์ในโค้ดที่พัฒนาระหว่างงานแข่งและสิทธิ์ใช้ชื่อ/ตราสัญลักษณ์ก่อนเลือก `LICENSE` ของ repo นี้ ขณะนี้ **ยังไม่ใส่ license และยังไม่สร้าง remote/public repository** จึงไม่ควรตีความว่าไฟล์ข้อมูลภายนอกเปิดให้แจกจ่ายหรือใช้เชิงพาณิชย์ได้ ดูรายการตรวจใน [นโยบายข้อมูล](docs/DATA_POLICY.md)
