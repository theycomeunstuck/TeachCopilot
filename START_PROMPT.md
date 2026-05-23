# START_PROMPT.md — TeachCopilot Conference MVP

Read all skills in `.claude/skills/` before doing anything else.
Read `MVP_STATUS.md` to understand what's already built.

## Goal
Bring the pipeline to conference-demo quality:
1. Pedagogical prompt from file (colleague's system_prompt.txt)
2. Dedup-safe ingestion + multimodal PDF ingest (qwen3-vl)
3. Profile Agent (dynamic score updates — visible proof of adaptivity)
4. Speaker detection (child vs adult via user_mappings)
5. Profile management CLI

Work autonomously, test after each step.

---

## Step 0 — Upgrade dependencies

1. Update `requirements.txt` — add:
```
requests
pymupdf            # PDF → page images (imports as fitz)
Pillow
```
2. Install: `pip install -r requirements.txt`
3. Create `prompts/` directory:
   - Copy `system_prompt(2).txt` → `prompts/system_prompt.txt`
   - Create `prompts/adult_prompt.txt`
4. Verify pymupdf: `python -c "import fitz; print(fitz.version)"`

---

## Step 1 — Schema upgrade

Update `db/schema.sql` with ALL new columns, tables, and constraints:

```sql
-- New column on knowledge_base
ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS image_path VARCHAR(500);

-- Constraints for dedup
ALTER TABLE knowledge_base
  ADD CONSTRAINT knowledge_base_source_topic_uq UNIQUE (source_file, topic);
ALTER TABLE knowledge_base
  ADD CONSTRAINT knowledge_base_source_page_uq UNIQUE (source_file, page_number);
ALTER TABLE child_knowledge
  ADD CONSTRAINT child_knowledge_child_subject_topic_uq UNIQUE (child_id, subject, topic);

-- New tables
CREATE TABLE IF NOT EXISTS user_mappings (
    user_id  TEXT PRIMARY KEY,
    child_id UUID REFERENCES children(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    child_id UUID REFERENCES children(id) ON DELETE CASCADE,
    event_type VARCHAR(100),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS events_child_id_idx
    ON events (child_id, created_at DESC);
```

Run: `python scripts/apply_schema.py`
Verify: no errors, all tables exist.

---

## Step 2 — Prompt from file

1. Update `pipeline/prompt_builder.py`:
   - Load BASE_SYSTEM_PROMPT from `prompts/system_prompt.txt` (pathlib, utf-8, fallback)
   - Load ADULT_SYSTEM_PROMPT from `prompts/adult_prompt.txt`
   - Profile in XML tags: `<child_safe_profile>`, `<child_learning_profile>`
   - Knowledge grouped: Что_уже_знает / Что_изучает_сейчас / Типичные_трудности
   - `speaker_type` parameter: child → pedagogy prompt, adult → analytics prompt

2. Test: assembled prompt ~3000+ chars, contains `<system_initialization>`,
   `<pedagogical_mission>`, mentions Миша, shows ⚠ segments.

---

## Step 3 — Dedup-safe ingestion (text)

1. Update `scripts/ingest.py`:
   - Text mode: ON CONFLICT (source_file, topic) DO UPDATE
   - Log: "X new, Y updated"

2. Update `pipeline/db.py` → `insert_test_child()`:
   - Knowledge: ON CONFLICT (child_id, subject, topic) DO UPDATE

3. Test: `python scripts/ingest.py --dir data` twice → second run "0 new, 5 updated"

---

## Step 4 — Multimodal PDF ingest

Read `.claude/skills/multimodal-ingest.md` fully before starting.

1. Add `--mode pdf --file <path> --subject <s> --topic <t>` to `scripts/ingest.py`

2. For each page:
   - pymupdf render → PNG saved to `data/images/<stem>_p001.png`
   - pymupdf extract text → raw_text
   - Send PNG to qwen3-vl (LM Studio :1234) → text description
   - Combine raw_text + description → sentence-transformer → embedding (384d)
   - Upsert: ON CONFLICT (source_file, page_number) DO UPDATE
   - Store image_path in DB

3. Vision prompt (Russian, school math context):
   "Опиши содержимое этой страницы из школьного учебника для 3-4 класса.
    Какие фигуры, обозначения, задания, числовые данные."

4. Test with real PDF: `python scripts/ingest.py --mode pdf --file "data/3 класс 1 часть_Математика.pdf" --subject math --topic "математика 3 класс"`
   - Verify: rows in knowledge_base with page_number, image_descriptions filled, image_path set
   - Verify: `data/images/` contains PNG files
   - Verify: text search "что такое отрезок" finds relevant pages

**Timing:** ~4-6 sec per page. 200 pages ≈ 15-20 min. One-time job.

---

## Step 5 — Speaker detection via user_mappings

1. Update `pipeline/filter_function.py`:
   - `_resolve_child_id(user_id)` → lookup user_mappings → (child_id, speaker_type)
   - inlet: try explicit `[child_id:UUID]` prefix first, fallback to user_mappings
   - Pass speaker_type to `build_system_prompt()`

2. Test:
   - Insert mapping: `INSERT INTO user_mappings VALUES ('test_user', '<misha_uuid>')`
   - Verify: inlet with user_id="test_user" → child mode + Миша profile
   - Verify: inlet with unknown user_id → adult mode

---

## Step 6 — Profile management CLI

Create `scripts/manage_profiles.py` with argparse subcommands:

```
list                    — all children (id, name, grade)
show <child_id>         — full profile + knowledge + events
create                  — --name --age-group --grade
add-knowledge           — --subject --topic --status --score
update-knowledge        — --topic --score --status
link-user <child_id>    — --user-id <owui_user_id>
delete <child_id>       — with confirmation
```

All output in Russian. Knowledge uses ON CONFLICT for upsert.

Test: create → add-knowledge → show → verify.

---

## Step 7 — Profile Agent (dynamic updates)

Read `.claude/skills/profile-agent.md` before starting.

1. Add to `pipeline/config.py`:
```python
PROFILE_LLM_API_URL = os.getenv("PROFILE_LLM_API_URL", "http://127.0.0.1:1234/v1")
PROFILE_LLM_MODEL   = os.getenv("PROFILE_LLM_MODEL", "qwen/qwen3-vl-4b")
PROFILE_UPDATE_INTERVAL = int(os.getenv("PROFILE_UPDATE_INTERVAL", "5"))
```

2. Create `pipeline/event_logger.py`: `log_event(child_id, event_type, payload)`

3. Create `pipeline/profile_agent.py`:
   - `should_update(msg_count)` → every 5 user messages
   - `assess_interaction(child_id, messages)` → LLM returns JSON assessments
   - `update_profile(child_id, assessments)` → apply score deltas, clamped 0-100
   - Status thresholds: struggling→learning at 40, learning→knows at 75

4. Update `pipeline/filter_function.py`:
   - Add `outlet()` method
   - On every 5th user message: assess → update → log event
   - Never block response — catch all exceptions

5. Test: simulate 5 exchanges → verify score changed → verify event logged

**Important:** Profile Agent calls LM Studio :1234 DIRECTLY — not through
Open WebUI. Open WebUI proxies the main LLM; using the same endpoint would loop.

---

## Step 8 — Update tests

Update `scripts/test_pipeline.py`:

```
--- Step 1: config ---
  [OK] DATABASE_URL set
  [OK] EMBEDDING_MODEL set
  [OK] PROFILE_LLM_API_URL set
  [OK] PROFILE_UPDATE_INTERVAL is int

--- Step 2: database ---
  [OK] Profile loaded (Миша)
  [OK] Has 3 knowledge records
  [OK] Knowledge ON CONFLICT works (no duplicates)

--- Step 3: RAG search ---
  [OK] RAG returns ≥1 result
  [OK] Top result relevant

--- Step 4: prompt builder ---
  [OK] Prompt loaded from file (not stub)
  [OK] Prompt contains <system_initialization>
  [OK] Prompt contains <child_safe_profile>
  [OK] Prompt mentions Миша
  [OK] Prompt has Типичные_трудности section
  [OK] Adult prompt different from child prompt

--- Step 5: filter function ---
  [OK] filter_function imports OK
  [OK] inlet hook works
  [OK] outlet hook works

--- Step 6: dedup ---
  [OK] Double ingest: row count unchanged

--- Step 7: user_mappings ---
  [OK] Known user → child mode
  [OK] Unknown user → adult mode

--- Step 8: profile agent ---
  [OK] should_update(5) == True
  [OK] should_update(3) == False

--- Step 9: multimodal ---
  [OK] PDF pages have image_path set
  [OK] PDF pages have image_descriptions filled

Results: 22/22 passed
ALL STEPS PASSED
```

---

## Step 9 — Update MVP_STATUS.md

**This is the handoff document.** Must contain all sections below.

### Что реализовано

One line per file created/modified, with purpose:

| File | Purpose |
|---|---|
| `pipeline/__init__.py` | Package marker |
| `pipeline/config.py` | All settings from .env — single source of truth |
| `pipeline/db.py` | PostgreSQL: child profile fetch + test data (with ON CONFLICT) |
| `pipeline/rag.py` | pgvector: embed query + cosine similarity search |
| `pipeline/prompt_builder.py` | Load prompt from file + profile in XML tags + RAG + speaker_type |
| `pipeline/filter_function.py` | Open WebUI Filter: inlet (personalization) + outlet (profile update) |
| `pipeline/profile_agent.py` | Dynamic profile updates via LLM assessment every 5 messages |
| `pipeline/event_logger.py` | Event logging to PostgreSQL |
| `scripts/apply_schema.py` | Apply schema via psycopg2 |
| `scripts/ingest.py` | Embed documents: --mode text (dedup) + --mode pdf (qwen3-vl) |
| `scripts/manage_profiles.py` | CLI: list/show/create/add-knowledge/link-user/delete |
| `scripts/test_pipeline.py` | Integration test (22+ checks) |
| `db/schema.sql` | Schema: children, profiles, knowledge, knowledge_base, user_mappings, events |
| `prompts/system_prompt.txt` | Pedagogical prompt (colleague edits independently) |
| `prompts/adult_prompt.txt` | Teacher-facing mode prompt |
| `data/` | Educational text files (geometry) |
| `data/images/` | Saved PDF page images for showing to child |
| `FILTER_INSTALL.md` | Open WebUI installation guide |
| `MVP_STATUS.md` | This document |

### Что умеет система

1. **Персонализация**: знает имя, уровень, трудности ребёнка из БД → подставляет в промпт
2. **RAG**: ищет релевантный учебный материал по вопросу ребёнка
3. **Педагогический промпт**: 1500+ слов методики (от коллеги), загружается из файла
4. **Мультимодальный инжест**: PDF учебника → картинки страниц + описания (qwen3-vl) + эмбеддинги
5. **Динамическое обновление профиля**: каждые 5 сообщений LLM оценивает понимание → score обновляется
6. **Детект спикера**: user_id → user_mappings → ребёнок/взрослый → разные режимы промпта
7. **Управление профилями**: CLI для создания/просмотра/обновления без UI
8. **Дедупликация**: повторный инжест не создаёт дубликатов

### Test results

(Paste actual output of test_pipeline.py)

### How to run locally

(Copy-pasteable commands)

### Server migration checklist

(All env vars, what to change for production)

### Conference demo script

1. Terminal: `manage_profiles.py show <misha_id>` → score 30 on segments
2. Open WebUI, Миша: "Привет!" → AI responds by name, suggests segments
3. 5 exchanges about segments (AI uses lesson_flow)
4. Terminal: `manage_profiles.py show <misha_id>` → score changed (~50)
5. Unknown user speaks → AI switches to teacher analytics mode
6. Teacher: "Как дела у Миши?" → progress summary

### Phase 3 — what comes next

- CLIP: поиск по фото тетрадки → найти страницу учебника
- Orchestrator: маршрутизация между агентами
- Task/Narrative Agent: миссии, генерация заданий в контексте истории
- Multiple subjects: русский язык, английский
- Group dynamics: несколько учеников, командные миссии
- Stable Diffusion: генерация иллюстраций
- Attempt Analyzer: отдельный агент для оценки ответов
- UI для педагога (сейчас только CLI)

### Known limitations / TODOs

(List anything incomplete)

---

## Error handling

| Error | Fix |
|---|---|
| pymupdf import error | `pip install pymupdf` (imports as `fitz`) |
| qwen3-vl timeout on page | Increase timeout to 120s; or skip page, log warning |
| prompts/system_prompt.txt missing | Fallback stub + warning |
| Profile Agent LLM timeout | Log, skip update, continue |
| user_mappings empty | Falls back to DEFAULT_CHILD_ID |
| ON CONFLICT fails | Re-run apply_schema.py |
| PDF too large (1000+ pages) | Run in batches: `--pages 1-50`, `--pages 51-100` |
| Stuck after 2 attempts | Document in MVP_STATUS.md, continue |

---

When fully done, print in Russian:
- Что было сделано (одна строка на модуль)
- Как запустить демо для конференции (пошагово)
- Какие env-переменные добавлены
- Что нужно сделать вручную (Open WebUI, link-user, ingest PDF)
