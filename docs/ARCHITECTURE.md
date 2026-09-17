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

Browser ไม่ Query GraphDB โดยตรง ข้อเท็จจริงและที่มามาจาก GraphDB ส่วน LLM ใช้เพื่อเข้าใจบริบทและเขียนคำตอบจาก Candidate ที่ได้รับ Backend ปฏิเสธ Sense หรือ Evidence ที่ไม่อยู่ใน Candidate Set

เส้นทางนำเข้าคือ raw file → source mapping/adapter → normalized record → validation → RDF → named graph ไม่รวม source sense ต่างแหล่งเป็นข้อเท็จจริงเดียวอัตโนมัติ ตัวช่วย alignment เป็นเพียง candidate จนมีการตรวจอนุมัติ

การเพิ่มชุดข้อมูลใหม่ไม่ต้องแก้หน้าเว็บ หาก adapter สร้าง RDF ตาม schema ที่ API อ่านได้แล้ว แต่ความสามารถเปรียบเทียบข้ามแหล่งต้องมี alignment และการตรวจคุณภาพเพิ่ม ไม่ได้เกิดจากการ import เพียงอย่างเดียว

สถานะต้นแบบ: LLM เป็น optional; heuristic เป็นค่าเริ่มต้นสำหรับการรันในเครื่อง ไม่ควรสื่อว่าผล heuristic เป็นผลจาก ThaiLLM
