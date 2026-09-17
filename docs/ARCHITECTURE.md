# สถาปัตยกรรม

```text
Browser / Next.js
       │ HTTP API
       ▼
FastAPI ─── SPARQL ─── GraphDB 11.5
   │                       └─ Named graph แยกตามแหล่งและฉบับ
   ├──── optional ThaiLLM API: เลือก Sense / เรียบเรียงคำตอบ
   └──── ตรวจ Sense URI และ Evidence ID กับ Candidate ที่ดึงมา
       │
       └── คำตอบ + หลักฐาน + กราฟ → Browser
```

Browser ไม่ Query GraphDB โดยตรง ข้อเท็จจริงและที่มามาจาก GraphDB ส่วน ThaiLLM ใช้เพื่อเข้าใจบริบท เลือกความหมาย และเรียบเรียงคำตอบจาก Candidate/Evidence/ความสัมพันธ์ที่ส่งให้ใน API call เดียว Backend ปฏิเสธ Sense หรือ Evidence ID ที่ไม่อยู่ใน Candidate Set

เมื่อ ThaiLLM ส่งคำตอบแบบสนทนาที่ผ่านการตรวจรูปแบบพื้นฐาน ระบบแสดงคำตอบนั้นแทนเทมเพลตเดิม หากไม่ส่งหรือส่งข้อความที่ใช้ไม่ได้ ระบบยังตอบจากข้อมูลในกราฟด้วยเทมเพลตที่ปลอดภัย โดย API ระบุ `diagnostics.answer_mode` ว่าคำตอบมาจาก `model` หรือ `template` คำว่า “Sense/อ้างอิงตรวจแล้ว” หมายถึงการตรวจ URI/ID และแหล่งข้อมูล **ไม่ใช่** การรับรองว่าข้อความอิสระทุกประโยคของ LLM ถูกต้องทั้งหมด

เส้นทางนำเข้าคือ raw file → source mapping/adapter → normalized record → validation → RDF → named graph ไม่รวม source sense ต่างแหล่งเป็นข้อเท็จจริงเดียวอัตโนมัติ ตัวช่วย alignment เป็นเพียง candidate จนมีการตรวจอนุมัติ

การเพิ่มชุดข้อมูลใหม่ไม่ต้องแก้หน้าเว็บ หาก adapter สร้าง RDF ตาม schema ที่ API อ่านได้แล้ว แต่ความสามารถเปรียบเทียบข้ามแหล่งต้องมี alignment และการตรวจคุณภาพเพิ่ม ไม่ได้เกิดจากการ import เพียงอย่างเดียว

สถานะต้นแบบ: LLM เป็น optional; heuristic เป็นค่าเริ่มต้นสำหรับการรันในเครื่อง คำตอบแบบสนทนาจากโมเดลต้องตั้งค่า ThaiLLM ก่อน ไม่ควรสื่อว่าผล heuristic เป็นผลจาก ThaiLLM
