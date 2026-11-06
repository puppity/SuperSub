# Subtitle Translator — Design Spec

วันที่: 2026-11-07
ผู้ใช้หลัก: พี่บอล (Windows, NVIDIA RTX 4070-class GPU)
UI mockup: https://claude.ai/artifact/HSGNhercvDhGVaeESGQdG2

## 1. เป้าหมาย

โปรแกรม Windows แบบ GUI สำหรับแปลซับหนัง/อนิเมะจากภาษาญี่ปุ่นหรืออังกฤษเป็นภาษาไทย โดยแปลแบบรู้บริบท:
รู้ว่าใครพูด พูดกับใคร และความสัมพันธ์ระหว่างตัวละคร (พี่/น้อง, ครู/นักเรียน ฯลฯ) เพื่อเลือกคำเรียกและสรรพนามไทยให้ถูก

ผลลัพธ์คือไฟล์ซับแยก `<ชื่อวิดีโอ>.th.srt` และ `<ชื่อวิดีโอ>.th.ass` วางข้างไฟล์วิดีโอ

## 2. ขอบเขตตามเฟส

| เฟส | ขอบเขต |
|---|---|
| 1 (spec นี้) | MKV/MP4 ที่มีแทร็กซับแบบข้อความ (SRT/ASS/SSA/WebVTT) และไฟล์ซับแยก .srt/.ass ข้างวิดีโอ → วิเคราะห์ตัวละคร → แปล → ตรวจ/แก้ใน GUI → ส่งออก .th.srt + .th.ass; AI backend 2 แบบ (Anthropic API key และ Claude Code ที่ล็อกอินด้วย Claude Max); คิวหลายไฟล์; แพ็กเป็น .exe |
| 2 | วิดีโอไม่มีซับ: ถอดเสียงด้วย faster-whisper (large-v3-turbo, CUDA, fallback CPU); "ใช้ภาพช่วยเดาคนพูด" เฉพาะบรรทัดที่ความมั่นใจต่ำ |
| 3 | ซับภาพ PGS/VobSub (OCR) — ใช้ pipeline จาก handoff เดิม |
| 4 | Hardsub ฝังในภาพ |

นอกขอบเขตเฟส 1: แก้ความสัมพันธ์ตัวละครด้วยมือ (character sheet ที่ผู้ใช้แก้ได้), mux ซับกลับเข้า MKV, burn-in, speaker diarization จากเสียง, macOS

## 3. Tech stack

- Python 3.12, PySide6
- Widget kit: PySide6-Fluent-Widgets (ธีม Windows 11 Fluent, โหมดมืด) — ต้องยืนยันว่ารองรับ dark theme ก่อนเริ่ม; ถ้าไม่ได้ ใช้ PySide6 ล้วน + QSS ธีมมืดของเราเอง (ทำ wrapper `gui/theme.py` ให้สลับได้). ไลเซนส์ GPLv3 สำหรับใช้ส่วนตัว/ไม่เชิงพาณิชย์
- `anthropic` (Python SDK ทางการ), `pysubs2`, `keyring`, `platformdirs`
- ffmpeg/ffprobe รวมใน `bin/`
- แพ็ก: PyInstaller one-folder (`SubTranslator/SubTranslator.exe`)
- เล่นวิดีโอใน preview: `QMediaPlayer` + `QVideoWidget` (Qt Multimedia) — ถ้า codec ไม่รองรับ แสดงภาพนิ่งที่ ffmpeg จับ ณ เวลาของบรรทัดแทน
- ทดสอบ: pytest, pytest-qt

## 4. โครงสร้างโปรแกรม

```
subtitle_translator/
  app.py                  จุดเริ่ม, ตั้งธีม, เปิด MainWindow
  core/
    models.py             dataclass: Cue, Character, Episode, Project
    media.py              ffprobe/ffmpeg: list tracks, extract sub, grab frame
    subio.py              อ่าน/เขียน srt/ass ผ่าน pysubs2, ตรวจ encoding
    project_store.py      อ่าน/เขียน .stproj.json (resume)
    settings.py           ค่าตั้ง (JSON ใน %APPDATA%) + API key (keyring)
    pricing.py            ตารางราคาโมเดล + คำนวณค่าใช้จ่าย
  ai/
    backend.py            Protocol AIBackend + ผลลัพธ์กลาง
    api_backend.py        Anthropic API key
    claude_code_backend.py  เรียก `claude -p` (บัญชี Claude Max)
    json_repair.py        ตรวจ/ซ่อม JSON จาก backend ที่ไม่มี structured output
  translate/
    prompts.py            prompt ทั้งหมด
    analyze.py            รอบ 1: ตัวละคร + คนพูด
    translate.py          รอบ 2: แปลเป็นช่วง
    pipeline.py           ลำดับงานต่อ 1 ไฟล์ + resume + progress callback
  export/
    srt_writer.py, ass_writer.py
  gui/
    theme.py, main_window.py (nav rail), queue_page.py, review_page.py,
    settings_page.py, timeline_widget.py, worker.py (QThread)
  tests/
```

กฎขอบเขต: `core`, `ai`, `translate`, `export` ห้าม import อะไรจาก `gui` — ทดสอบได้โดยไม่เปิด GUI

## 5. Data model

```python
Cue:        id:int, start:float, end:float, src:str, ass_style:str|None,
            speaker:str|None, addressee:str|None, speaker_conf:float,
            thai:str|None, status: "pending"|"translated"|"edited"|"failed"
Character:  key:str, name_src:str, name_th:str, gender:str, role:str,
            relations:list[str], address_terms:dict[str,str], color:str
Episode:    video_path, sub_source (track index หรือไฟล์), src_lang: "ja"|"en",
            cues:list[Cue], characters:list[Character], summary:str
Project:    episode + model + backend + stage ("extracted"|"analyzed"|"translating"|"done")
            + done_chunks:list[int] + cost_so_far:float
```

`Project` บันทึกเป็น `<ชื่อวิดีโอ>.stproj.json` ข้างวิดีโอ หลังจบแต่ละขั้นและหลังแปลเสร็จแต่ละช่วง

## 6. Flow ต่อ 1 ไฟล์

1. **ตรวจไฟล์** — `ffprobe -show_streams` → แทร็ก subtitle ชนิดข้อความ; เลือก jpn ก่อน ตามด้วย eng (ตั้งค่าได้); มีหลายแทร็กภาษาเดียวกัน → ให้ผู้ใช้เลือก. ถ้ามีไฟล์ `.srt/.ass` ชื่อเดียวกันข้างวิดีโอ เสนอเป็นแหล่งได้ด้วย
2. **ดึงซับ** — `ffmpeg -map 0:s:N -c:s copy` (ASS คง style) → `subio` → `list[Cue]`. ตัด cue ที่เป็นเอฟเฟกต์/karaoke (ASS ที่มี `\k`, `\pos`+drawing, หรือ style ชื่อ OP/ED/Karaoke/Sign) ไปไว้กลุ่ม "ไม่แปล" — แสดงในตารางแต่ไม่ส่งแปล
3. **ประมาณราคา** — `count_tokens` (API backend) กับ prompt จริงของทั้งสองรอบ + ประมาณ output = 1.6× ของจำนวน token ต้นฉบับ. Claude Code backend แสดง "ใช้โควตา Max" แทนราคา
4. **รอบ 1 วิเคราะห์** — ส่งซับทั้งตอน (เลขบรรทัด + เวลา + style/actor ถ้ามี + ข้อความ) → JSON:
   `characters[]`, `summary`, `cues[] {id, speaker, addressee, confidence}`
5. **รอบ 2 แปล** — ช่วงละ 80 บรรทัด (ปรับได้ 40–150). ทุกคำขอมี: system prompt คงที่, character sheet + summary, ซับต้นฉบับทั้งตอน (ส่วนคงที่ → cache), แล้วตามด้วย: คำแปล 20 บรรทัดสุดท้ายของช่วงก่อน + บรรทัดของช่วงนี้. คืน JSON `[{id, thai}]`
6. **ตรวจผล** — id ครบ/ไม่เกิน/ไม่ซ้ำ; ข้อความไม่ว่าง; ขาด → ส่งเฉพาะ id ที่ขาดใหม่ 1 ครั้ง; ยังขาด → status `failed`
7. **ตรวจ/แก้** ใน review page (ผู้ใช้)
8. **ส่งออก** — `.th.srt` (UTF-8 with BOM) และ `.th.ass` (style ต่อตัวละคร, สีตาม `Character.color`, ฟอนต์ตั้งค่าได้ ค่าเริ่ม Leelawadee UI 52, ขอบดำ 2.5, ตำแหน่งล่างกลาง; cue กลุ่ม "ไม่แปล" ของ ASS ต้นฉบับคงไว้ตามเดิม)

### กฎการแปล (อยู่ใน prompts.py)

- ใช้คำเรียกตาม `address_terms` ให้คงที่ทั้งตอน
- สรรพนาม/คำลงท้ายตามเพศและอายุผู้พูด และระดับความสุภาพต่อผู้ฟัง
- honorific ญี่ปุ่น (-san/-chan/-kun/-senpai/-sensei) แปลเป็นคำไทยที่เทียบได้ ไม่ทับศัพท์
- ชื่อเฉพาะถอดเสียงไทยแบบเดียวกันทั้งตอน
- ยาวพออ่านทัน: เป้า ≤ 17 ตัวอักษร/วินาที ไม่เกิน 2 บรรทัด
- ห้ามเพิ่ม/ลบความหมาย; มุก/คำเล่นให้แปลความหมาย

## 7. AI backends

```python
class AIBackend(Protocol):
    name: str
    def analyze(self, req: AnalyzeRequest) -> AnalyzeResult: ...
    def translate(self, req: TranslateRequest) -> TranslateResult: ...
    def estimate(self, req) -> Estimate | None: ...   # None = ไม่มีราคา (โควตา)
    def check(self) -> HealthResult: ...              # ปุ่ม "ทดสอบ"
```

### 7.1 ApiBackend (Anthropic API key)

- โมเดลค่าเริ่ม `claude-opus-5-5`; ตัวเลือก `claude-sonnet-5-5`, `claude-haiku-5-5`
- `thinking: {type: "adaptive"}`, `output_config.effort`: รอบ 1 = `high`, รอบ 2 = `medium` (ตั้งค่าได้)
- Structured output ผ่าน `output_config.format` (JSON schema) ทั้งสองรอบ
- Streaming + `get_final_message()`; `max_tokens` 64000
- Prompt caching: `cache_control` หลัง block ที่คงที่ (system + character sheet + ซับทั้งตอน)
- Refusal: Opus 5.5 / Sonnet 5.5 ส่ง `betas: ["server-side-fallback-2026-07-01"]`, `fallbacks: "default"`; Haiku 5.5 ไม่มี server-side fallback จึงไม่ส่ง; ตรวจ `stop_reason` ก่อนอ่าน content ทุกครั้ง
- `max_retries` 4; จัดการ error ตาม type ของ SDK (ไม่ match ข้อความ)
- บันทึก `usage` จริงของทุกคำขอ → `cost_so_far`
- รายละเอียด API ทุกจุดต้องยืนยันกับเอกสารใน claude-api skill ตอน implement

### 7.2 ClaudeCodeBackend (บัญชี Claude Max)

- เรียก `claude -p --output-format json --model <opus|sonnet>` ผ่าน `subprocess`, ส่ง prompt ทาง stdin, timeout ต่อคำขอ 10 นาที
- ตรวจตอนเริ่ม: หา `claude` ใน PATH และ `%USERPROFILE%\.local\bin`; ไม่พบ → แจ้งให้ติดตั้ง/ล็อกอิน
- ไม่มี structured output: prompt สั่งให้ตอบ JSON ล้วน → `json_repair` (ตัด code fence, หา array/object นอกสุด, `json.loads`) → validate schema เดียวกับ ApiBackend → ไม่ผ่านถาม 1 รอบซ้ำพร้อมข้อความ error
- ตรวจข้อความ rate-limit/usage-limit จาก stdout/stderr/exit code → สถานะ "โควตา Max เต็ม — รอถึง <เวลาที่ระบุ ถ้ามี>" และหยุดคิว (ไม่ retry ถี่)
- ไม่ใช้ caching เอง, ไม่ประมาณราคา
- คำเตือนในหน้าตั้งค่า: ใช้กับบัญชีของตัวเองบนเครื่องตัวเองเท่านั้น และให้ผู้ใช้อ่านเงื่อนไขของ Claude Max ประกอบ
- ต้องยืนยัน flag/รูปแบบผลลัพธ์ของ Claude Code CLI เวอร์ชันที่ติดตั้งตอน implement

## 8. GUI (ตาม mockup)

ธีมมืด Fluent, nav rail ซ้าย 3 หน้า:

- **คิวงาน** — ลากไฟล์/โฟลเดอร์มาวาง, การ์ดต่อไฟล์ พร้อม stepper 5 ขั้น (ดึงซับ → วิเคราะห์ตัวละคร → แปล → ตรวจ → ส่งออก), เลือกแทร็ก, แผง "สรุปก่อนเริ่ม" (backend, โมเดล, token, ราคา), ปุ่มเริ่ม/หยุด; ทำทีละไฟล์ตามลำดับ
- **ตรวจ/แก้** — preview วิดีโอพร้อมซับไทยซ้อน (สีตามคนพูด), รายการบรรทัด ต้นฉบับคู่ไทย, กล่องแก้ + เปลี่ยนคนพูด + "แปลบรรทัดนี้ใหม่" + ตัววัดความเร็วอ่าน, ตัวกรอง ทั้งหมด/ไม่มั่นใจ (`speaker_conf < 0.6`)/แก้แล้ว/ล้มเหลว, ค้นหา, ไทม์ไลน์แถวละตัวละคร, แผงตัวละคร (เปลี่ยนสีได้), ปุ่มส่งออก; คีย์ลัด Space เล่น/หยุด, ↑↓ เลือกบรรทัด, Ctrl+Enter ยืนยันแก้
- **ตั้งค่า** — backend (API key / Claude Code), API key (ซ่อน, ทดสอบ, เก็บใน keyring), โมเดลแปลพร้อมราคาและป้าย "แนะนำ" ที่ Opus 5.5, effort, ขนาดช่วงแปล, ภาษาต้นทางที่ให้ความสำคัญ, ที่บันทึก, ฟอนต์ ASS, สีใน SRT (ปิดเป็นค่าเริ่ม)

งาน AI/ffmpeg ทั้งหมดรันใน `QThread` worker ส่ง progress ผ่าน signal — GUI ห้ามค้าง

## 9. การจัดการ error

| สถานการณ์ | พฤติกรรม |
|---|---|
| 429/5xx/เน็ตหลุด (API) | SDK retry; ยังไม่ผ่าน → ไฟล์ "หยุดชั่วคราว · ทำต่อ"; ช่วงที่เสร็จแล้วอยู่ใน `.stproj.json` |
| 401 key ผิด | หยุดคิว, เปิดหน้าตั้งค่า |
| 400 | log, ไฟล์ "ผิดพลาด", คิวไปต่อ |
| refusal (หลัง fallback) | บรรทัดในช่วงนั้น `failed` พร้อมป้าย ให้แปลเอง |
| `max_tokens` | แบ่งช่วงครึ่งแล้วส่งใหม่ (ต่ำสุด 10 บรรทัด) |
| JSON ขาด/เกิน id | ส่งเฉพาะที่ขาดใหม่ 1 ครั้ง → `failed` |
| โควตา Max เต็ม | หยุดคิว แสดงเวลาที่รอ |
| ไม่พบ `claude` / ไม่ได้ล็อกอิน | แจ้งวิธีติดตั้ง/ล็อกอิน, เสนอสลับไป API |
| ไม่มีแทร็กซับข้อความ | "ต้องถอดเสียง (เฟส 2)" / ซับภาพ "(เฟส 3)" |
| encoding อ่านไม่ได้ | ลอง utf-8-sig → utf-16 → cp932 → cp874 → แจ้ง error |
| output มีอยู่แล้ว | ถามก่อนเขียนทับ; ไม่แตะไฟล์ต้นฉบับเด็ดขาด |
| ไม่พบ ffmpeg | แจ้งตอนเปิดโปรแกรม |

ข้อความ error เป็นภาษาไทย + ปุ่ม "คัดลอกรายละเอียด"; log ที่ `%LOCALAPPDATA%\SubTranslator\logs` (หมุนไฟล์ 5×2 MB); ห้ามมี API key ใน log

## 10. การทดสอบ

- **Unit (pytest):** subio round-trip (srt/ass, timestamp, style, encoding), ตัด cue karaoke, แบ่งช่วง, validator id, json_repair, pricing, ass_writer style ต่อตัวละคร, project_store resume
- **Backend ปลอม:** `FakeBackend` คืนผลตายตัว — ทดสอบ pipeline: retry, แบ่งช่วงเมื่อ `max_tokens`, refusal, id หาย, resume กลางทาง
- **ClaudeCodeBackend:** mock `subprocess` ด้วย stdout ตัวอย่าง (สำเร็จ, JSON มี code fence, usage-limit, ไม่พบคำสั่ง)
- **Media:** MKV ตัวอย่างสั้น (สร้างด้วย ffmpeg ใน fixture: วิดีโอสีพื้น 10 วินาที + แทร็ก ass jpn + srt eng) ทดสอบ list/extract จริง
- **Live (`pytest -m live`, รันเมื่อสั่งเท่านั้น):** แปล 20 บรรทัดจริงทั้งสอง backend; ตรวจครบทุก id และ `お姉ちゃん` → "พี่"
- **GUI:** pytest-qt smoke test เปิดทุกหน้า + checklist ทดสอบมือ
- **Release:** build .exe → เปิดบนเครื่องไม่มี Python → แปลจริง 1 ไฟล์ → เปิดผลใน VLC

## 11. เกณฑ์ว่าเฟส 1 เสร็จ

- ลาก MKV ที่มีซับ jpn หรือ eng → ได้ `.th.srt` + `.th.ass` ที่เปิดใน VLC ได้ ภาษาไทยแสดงถูก เวลาตรงต้นฉบับ
- ไฟล์ทดสอบที่มีพี่น้อง: คำเรียกพี่/น้องถูกและคงที่ทั้งตอน
- ปิดโปรแกรมกลางการแปลแล้วเปิดใหม่ ทำต่อได้โดยไม่แปลช่วงที่เสร็จซ้ำ
- ใช้ได้ทั้ง API key และ Claude Code (Max)
- `.exe` เปิดได้บน Windows ที่ไม่มี Python
