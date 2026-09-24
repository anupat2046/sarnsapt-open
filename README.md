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

หากต้องการลองจับคู่ข้ามแหล่งแบบไม่ใช้ข้อมูลจริง ให้นำเข้าชุดสังเคราะห์อีกชุดด้วย:

```powershell
./scripts/ingest-organizer.ps1 -InputPath ./examples/sample-glossary.csv -MappingPath ./examples/sample-glossary.mapping.json
Invoke-RestMethod 'http://localhost:8000/api/alignments?lemma=ดาว'
```

ตัวอย่างนี้ควรได้ข้อเสนอ “ดาว” ความหมายวัตถุบนท้องฟ้า 1 คู่ในสถานะ `pending`; ความหมายเครื่องหมายตกแต่งไม่ควรถูกเชื่อม ทั้งสองไฟล์ใน `examples/` เป็นข้อมูลสังเคราะห์เท่านั้น

สคริปต์ทำ audit → normalize → validate → RDF → import เข้า named graph ตาม `source.id` และ `edition_id` แล้วสร้างข้อเสนอเชื่อมความหมายข้ามแหล่งโดยอัตโนมัติสำหรับคำไทยที่มีหลักฐานเทียบกันได้ ข้อเสนอนี้ใช้เส้น `possiblySameSense` และมีสถานะ “รอตรวจ” ไม่ใช่คำยืนยันว่าเป็นความหมายเดียวกัน ระบบไม่เปลี่ยนคะแนนสูงให้เป็น `exactMatch` เอง และไม่เขียนทับผลตรวจของมนุษย์ หากไม่ต้องการรันการจับคู่อัตโนมัติในรอบนั้นให้ใส่ `-SkipAutoAlignment`

หาก graph เดิมมีข้อมูลแล้ว สคริปต์จะหยุดโดยไม่ลบข้อมูล หากตั้งใจแทนที่ให้ใช้ `-ReplaceExisting` ซึ่งจะสำรอง graph เดิมใน `data/rdf/backup/` ก่อนนำเข้า ไฟล์นั้นก็ถูก Git ignore

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

ชุดทดสอบสดของ `/api/ask` และแบบทดสอบเทียบว่ากราฟช่วยให้โมเดลเดิมตอบดีขึ้นแค่ไหน (ต้องเปิดระบบและตั้ง `THAILLM_API_KEY` ก่อน)

```powershell
python scripts/eval-ask.py
python scripts/bench-grounded.py --per-stratum 12 --judge
```

วิธีอ่านผลและข้อจำกัดอยู่ใน [แบบทดสอบ](docs/BENCHMARK.md)

## สิทธิ์การใช้งาน

โค้ดในโปรเจกต์นี้เผยแพร่ภายใต้ [Apache License 2.0](LICENSE) ส่วน **ข้อมูลเป็นคนละสิทธิ์กับโค้ด** ไฟล์ชุดข้อมูลจากแหล่งภายนอกและจากผู้จัดงานไม่ได้อยู่ใน repo นี้ (ถูก Git ignore) การมีโค้ดที่นำเข้าข้อมูลได้ไม่ได้แปลว่ามีสิทธิ์แจกจ่ายหรือใช้ข้อมูลนั้นเชิงพาณิชย์ ต้องตรวจ license ของแต่ละแหล่งเอง ข้อมูลตัวอย่างใน `examples/` และ `data/sample/` เป็นข้อมูลสังเคราะห์ที่เขียนขึ้นเองเพื่อทดสอบเท่านั้น

สิทธิ์ในชื่อและตราสัญลักษณ์ “สานศัพท์” แยกต่างหากจาก license ของโค้ด ดูรายการตรวจใน [นโยบายข้อมูล](docs/DATA_POLICY.md)
