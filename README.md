# แปลซับไทย (SubTranslator)

แปลซับญี่ปุ่น/อังกฤษในไฟล์ MKV/MP4 เป็นภาษาไทยแบบรู้บริบทตัวละคร แล้วส่งออก `.th.srt` และ `.th.ass` ข้างวิดีโอ

## ใช้งาน
1. เปิด `SubTranslator.exe`
2. ตั้งค่า → เลือกวิธีเชื่อมต่อ
   - Anthropic API key: สร้าง key ที่ Claude Console (ต้องเติมเงินก่อน) แล้ววางในช่อง กด "บันทึกและทดสอบ"
   - Claude Code (บัญชี Max): ติดตั้ง Claude Code แล้วพิมพ์ `claude` ใน Terminal เพื่อล็อกอินครั้งแรก
3. คิวงาน → ลากไฟล์หรือโฟลเดอร์มาวาง → เริ่มแปลทั้งคิว
4. ตรวจ/แก้ → แก้คำแปล → ส่งออก

หยุดกลางทางได้ เปิดโปรแกรมใหม่แล้วกดเริ่ม งานจะทำต่อจากที่ค้าง (ข้อมูลอยู่ใน `<ชื่อวิดีโอ>.stproj.json`)

## สำหรับนักพัฒนา
    python -m venv .venv && .venv\Scripts\activate
    pip install -e ".[dev]"
    pytest                 # ไม่เรียก AI จริง
    pytest -m live         # เรียก AI จริง เสียเงิน/โควตา
    pyinstaller --noconfirm SubTranslator.spec
