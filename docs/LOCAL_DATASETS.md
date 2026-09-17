# นำเข้าชุดข้อมูลที่เก็บในเครื่อง

1. ตรวจสิทธิ์ก่อนใช้งาน: ผู้ให้ข้อมูล, ฉบับ, license, เงื่อนไขการแสดงผล และสิทธิ์นำข้อความเข้าระบบ/ส่งไปยัง LLM
2. วางไฟล์จริงใน `data/local/` (Git ignore) ตั้งชื่อไฟล์และฉบับให้ชัด อย่าแก้ไฟล์ต้นฉบับ
3. สร้าง mapping JSON ตาม `schemas/organizer-mapping.schema.json` โดยเริ่มจาก `examples/sample-dictionary.mapping.json` ค่า `source.id` และ `edition_id` ต้องเป็นรหัสถาวรของแต่ละแหล่ง/ฉบับ
4. Audit ก่อน: `./scripts/audit-organizer.ps1 -InputPath <file> -MappingPath <mapping>` อ่านรายงานใน `reports/`
5. ทดลองแปลงโดยไม่ import: `./scripts/ingest-organizer.ps1 -InputPath <file> -MappingPath <mapping> -SkipImport` ตรวจ normalized JSONL, RDF และ validation report
6. เมื่อผ่านการตรวจจึงนำเข้าโดยรันคำสั่งเดิมโดยไม่ใส่ `-SkipImport` GraphDB ต้องเปิดและมี repository `thailex` แล้ว
7. ทดสอบคำจริงผ่าน `/api/search`, `/api/senses` และ `/api/ask` รวมถึงตรวจชื่อแหล่ง/ฉบับบนหน้าเว็บ

สคริปต์จะไม่เขียนทับ graph ที่มีข้อมูลอยู่แล้ว หากต้องแทนที่ฉบับเดิมจริง ให้ใช้ `-ReplaceExisting`; ระบบจะสำรอง graph ก่อนลบและ import ใหม่ แต่ควรเก็บ backup ภายนอกเครื่องด้วยสำหรับข้อมูลสำคัญ

ปัจจุบัน adapter ทั่วไปรองรับ CSV/JSON/JSONL/XML เท่านั้น โครงสร้างหลายชีต, ตาราง Word, ฟิลด์พิเศษ และความสัมพันธ์ที่ไม่ตรง schema ต้องสร้าง parser/mapping เฉพาะแหล่ง อย่าเติมนิยามหรือความสัมพันธ์ที่ต้นฉบับไม่มี

ไฟล์ใน `data/local/`, `data/raw/`, `data/normalized/`, `data/rdf/generated/`, `data/rdf/backup/`, `reports/`, `.env` และ `secrets/` ถูก ignore; `git status` ต้องตรวจอีกครั้งก่อน commit เสมอ การ ignore ป้องกัน commit ใหม่ แต่ไม่ได้ลบข้อมูลที่เคยอยู่ใน Git history — repo นี้จึงเริ่ม Git history ใหม่
