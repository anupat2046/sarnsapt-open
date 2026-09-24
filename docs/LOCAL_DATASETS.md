# นำเข้าชุดข้อมูลที่เก็บในเครื่อง

1. ตรวจสิทธิ์ก่อนใช้งาน: ผู้ให้ข้อมูล, ฉบับ, license, เงื่อนไขการแสดงผล และสิทธิ์นำข้อความเข้าระบบ/ส่งไปยัง LLM
2. วางไฟล์จริงใน `data/local/` (Git ignore) ตั้งชื่อไฟล์และฉบับให้ชัด อย่าแก้ไฟล์ต้นฉบับ
3. สร้าง mapping JSON ตาม `schemas/organizer-mapping.schema.json` โดยเริ่มจาก `examples/sample-dictionary.mapping.json` ค่า `source.id` และ `edition_id` ต้องเป็นรหัสถาวรของแต่ละแหล่ง/ฉบับ
4. Audit ก่อน: `./scripts/audit-organizer.ps1 -InputPath <file> -MappingPath <mapping>` อ่านรายงานใน `reports/`
5. ทดลองแปลงโดยไม่ import: `./scripts/ingest-organizer.ps1 -InputPath <file> -MappingPath <mapping> -SkipImport` ตรวจ normalized JSONL, RDF และ validation report
6. เมื่อผ่านการตรวจจึงนำเข้าโดยรันคำสั่งเดิมโดยไม่ใส่ `-SkipImport` GraphDB ต้องเปิดและมี repository `thailex` แล้ว หลังนำเข้าสคริปต์จะสร้างลิงก์ระหว่าง Sense ที่มี lemma ตรงกัน, POS เข้ากันได้ และหลักฐานข้อความคล้ายกันพอ ลิงก์เป็นข้อเสนอ `possiblySameSense` เท่านั้น ไม่ใช่การรับรอง `exactMatch` หากเป็นชุดใหญ่อาจใช้เวลานาน; ใส่ `-SkipAutoAlignment` เพื่อเลื่อนขั้นนี้ไปก่อน
7. ตรวจ `reports/auto-alignment-organizer-<source>-<edition>-validation.json` จำนวน `proposed_count` และสถานะ `no_matching_evidence` (หมายถึงยังไม่มีหลักฐานที่พอจะเชื่อม ไม่ใช่ import ล้มเหลว) หากต้องการรันใหม่เฉพาะขั้นนี้ใช้ `./scripts/build-auto-alignments.ps1 -NormalizedInput <normalized.jsonl> -SourceGraph <graph-iri>`
8. ทดสอบคำจริงผ่าน `/api/search`, `/api/senses` และ `/api/ask` รวมถึงตรวจชื่อแหล่ง/ฉบับบนหน้าเว็บและหน้าเปรียบเทียบ

สคริปต์จะไม่เขียนทับ graph ที่มีข้อมูลอยู่แล้ว หากต้องแทนที่ฉบับเดิมจริง ให้ใช้ `-ReplaceExisting`; ระบบจะสำรอง graph ก่อนลบและ import ใหม่ แต่ควรเก็บ backup ภายนอกเครื่องด้วยสำหรับข้อมูลสำคัญ

ข้อเสนออัตโนมัติแยกกราฟตามแหล่ง/ฉบับจากกราฟผลตรวจของมนุษย์ การรันใหม่แทนที่เฉพาะข้อเสนอของแหล่งนั้น และเก็บ backup ก่อนแทนที่ ผลตรวจเดิมไม่ถูกลบ แต่ถ้า reimport เปลี่ยน Source Record ID/URI ควรตรวจผลตรวจเก่าว่ายังชี้ไปยัง Sense ที่มีอยู่ ข้อเสนอจากระบบอาจผิดได้ โดยเฉพาะคำพ้องรูปและนิยามสั้น; อย่าใช้เป็นข้อเท็จจริงทางการโดยไม่ตรวจ

## ไฟล์ XLSX/DOCX ของผู้จัด

ไฟล์พจนานุกรมที่ได้รับเป็น XLSX/DOCX และในช่องเดียวมักรวมหลายข้อมูลไว้ด้วยกัน เช่น ชนิดคำ `น.` ป้าย `(ปาก)` คำอ่าน `[...]` และความหมายที่มีเลขลำดับ ให้แปลงเป็น CSV แบนก่อนด้วย `prepare-organizer` ขั้นนี้แค่แยกส่วนของข้อความ ไม่แก้ ไม่แปล และไม่เติมเนื้อหา แถวต้นฉบับทั้งแถวเก็บไว้ในคอลัมน์ `raw`

```powershell
$env:PYTHONPATH = "src"
python -m thailex_ingestion.cli prepare-organizer --profile royal-2542 `
  --input "data/local/organizer/original/<ไฟล์>.xlsx" `
  --output data/local/organizer/prepared/royal-dict-2542.csv
```

profile ที่มี: `royal-2542` (number/K_W/M_W/D_T), `royal-2554` (headword/pos/definition/…), `royal-2569` (head_word/snumber_sense/…), `technical-terms` (ศัพท์ตั้ง/ศัพท์บัญญัติ/คำอธิบาย), `transliterations` และ `dialect-docx` (ย่อหน้าที่คั่นด้วย tab: คำ อักษรถิ่น `[คำอ่าน]` `[IPA]` ตามด้วยนิยาม) ไฟล์ `.doc` รุ่นเก่ายังอ่านตรงไม่ได้ ต้องแปลงเป็น `.docx` ก่อน ส่วน DICT_2569 ฉบับ `.docx` และไฟล์ PDF ยังไม่มี profile

Mapping รองรับฟิลด์ `pronunciations`, `etymology` และ `notes` (มี `separator` ได้) ซึ่งหน้า “สำรวจคำ” จะแสดงเป็นคำอ่าน รากศัพท์ และหมายเหตุจากแหล่ง

เมื่อนำเข้าหลายชุดพร้อมกัน ให้ใส่ `-SkipAutoAlignment` ทุกชุด แล้วค่อยรัน `./scripts/build-auto-alignments.ps1` ต่อแหล่งเมื่อข้อมูลเข้าครบแล้ว การจับคู่จะดูเฉพาะคำที่มีในแหล่งอื่นด้วย ถ้ารันตอนที่ยังมีแหล่งเดียว จะไม่ได้ข้อเสนอเลย

ปัจจุบัน adapter ทั่วไปรองรับ CSV/JSON/JSONL/XML เท่านั้น โครงสร้างหลายชีต, ตาราง Word, ฟิลด์พิเศษ และความสัมพันธ์ที่ไม่ตรง schema ต้องสร้าง parser/mapping เฉพาะแหล่ง อย่าเติมนิยามหรือความสัมพันธ์ที่ต้นฉบับไม่มี

ไฟล์ใน `data/local/`, `data/raw/`, `data/normalized/`, `data/rdf/generated/`, `data/rdf/backup/`, `reports/`, `.env` และ `secrets/` ถูก ignore; `git status` ต้องตรวจอีกครั้งก่อน commit เสมอ การ ignore ป้องกัน commit ใหม่ แต่ไม่ได้ลบข้อมูลที่เคยอยู่ใน Git history — repo นี้จึงเริ่ม Git history ใหม่
