# TeachCopilot — AI Tutor Pipeline

AI-powered tutoring system for children aged 7-12. Voice-first architecture:
child speaks, Whisper transcribes, pipeline personalizes the LLM response,
TTS speaks back.

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Database](#database)
- [Knowledge Base (RAG)](#knowledge-base-rag)
- [Pipeline Modules](#pipeline-modules)
- [Profile Agent](#profile-agent)
- [Multimodal Ingest (PDF + Vision)](#multimodal-ingest)
- [Local RAG API Server](#local-rag-api-server)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Testing](#testing)
- [Deployment](#deployment)
- [Project Phases](#project-phases)

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 Open WebUI (Svelte)                  │
│              child speaks / types query              │
└───────────────────────┬─────────────────────────────┘
                        │ messages (JSON)
                        v
              ┌───────────────────┐
              │  Filter Function  │
              │   inlet() hook    │
              └──┬──────┬────────┘
                 │      │
        ┌────────┘      └────────┐
        v                        v
  ┌───────────┐          ┌─────────────┐
  │ PostgreSQL │          │  RAG Search │
  │  profile   │          │  (pgvector) │
  │  lookup    │          └─────────────┘
  └───────────┘                  │
        │                        │
        └────────┬───────────────┘
                 v
        ┌──────────────────┐
        │  Prompt Builder  │
        │  profile + RAG   │
        │  + system prompt │
        └────────┬─────────┘
                 │ system message injected
                 v
        ┌──────────────────┐
        │     Main LLM     │
        │ (Claude / local) │
        └────────┬─────────┘
                 │ response
                 v
        ┌──────────────────┐
        │  Filter Function │
        │  outlet() hook   │
        └──┬───────────────┘
           │
           ├── every 5 msgs ──> Profile Agent ──> update child_knowledge
           │                    (LM Studio)       log event
           │
           └── return response to child
```

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| Database | PostgreSQL + pgvector extension |
| Embeddings | sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`, fallback `all-MiniLM-L6-v2`) |
| Embedding dimension | 384 |
| LLM (main) | Claude API or any model via Open WebUI |
| LLM (Profile Agent + Vision) | qwen3-vl-4b via LM Studio (localhost:1234) |
| Frontend | Open WebUI (Svelte) |
| Local API Server | FastAPI + Uvicorn, OpenAI-compatible `/v1/chat/completions` proxy |
| LLM Runtime | LM Studio on `127.0.0.1:1234/v1` |
| ASR | Whisper (external) |
| TTS | External service |
| PDF processing | PyMuPDF (fitz) |
| Vector search | pgvector cosine similarity (IVFFlat index) |
| OS | Windows 11 (cross-platform scripts) |

**Hardware (production):** Windows 11, RTX 4070 Ti Super, 128 GB RAM, i9-13900K

---

## Project Structure

```
MachineLearning/
├── pipeline/                  # Core pipeline modules
│   ├── __init__.py
│   ├── config.py              # Environment variables, model settings
│   ├── db.py                  # PostgreSQL connection, profile queries
│   ├── rag.py                 # Embedding model, vector search
│   ├── prompt_builder.py      # System prompt assembly (profile + RAG)
│   ├── filter_function.py     # Open WebUI Filter (inlet/outlet hooks)
│   ├── profile_agent.py       # Dynamic profile updates via LLM
│   └── event_logger.py        # Event logging to PostgreSQL
│
├── scripts/                   # CLI tools
│   ├── apply_schema.py        # Create/update DB tables
│   ├── ingest.py              # Ingest data: text, PDF, JSON
│   ├── manage_profiles.py     # Child profile CRUD CLI
│   ├── show_prompt.py         # Debug: show assembled prompt
│   └── test_pipeline.py       # Integration tests (27 checks)
│
├── db/
│   └── schema.sql             # Database schema (8 tables)
│
├── prompts/
│   ├── system_prompt.txt      # Main pedagogical prompt (592 lines)
│   └── adult_prompt.txt       # Teacher/parent mode prompt
│
├── data/                      # Educational content for RAG
│   ├── math_points.txt        # Geometry: points
│   ├── *.txt / *.md / *.docx / *.rtf / *.json
│   └── *.pdf                  # PDF textbooks/source books for ingestion()
│   └── images/                # PDF page renders (PNG, auto-generated)
│
├── _docs/                     # Design documents
│   ├── Описание системы_TeachCopilot.docx
│   ├── child profile.txt
│   ├── personal data.txt
│   ├── system prompt.txt
│   └── широкая база.rtf
│
├── .env                       # Environment configuration
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project metadata
├── CLAUDE.md                  # AI assistant instructions
├── MVP_STATUS.md              # Implementation status
├── FILTER_INSTALL.md          # Open WebUI installation guide
├── START_PROMPT.md            # Phase 2 implementation plan
└── server.py                  # FastAPI RAG API server, OpenAI-compatible proxy to LM Studio
```

---

## Database

PostgreSQL with pgvector extension. 8 tables:

### Schema

```sql
-- Child records
children (
    id          UUID PRIMARY KEY,       -- gen_random_uuid()
    name        VARCHAR(100),
    age_group   VARCHAR(20),            -- e.g. "9-11"
    grade       VARCHAR(20),            -- e.g. "3-4"
    language    VARCHAR(20),            -- default 'russian'
    created_at  TIMESTAMPTZ
)

-- Learning preferences
child_profiles (
    id                      UUID PRIMARY KEY,
    child_id                UUID -> children(id),
    explanation_style       TEXT,            -- e.g. "very simple, step by step"
    pace                    VARCHAR(50),     -- slow / medium / fast
    autonomy_level          VARCHAR(50),     -- low / medium / high
    motivation              VARCHAR(50),
    prefers_visual          BOOLEAN,
    avg_response_time_sec   REAL,            -- average answer speed (seconds)
    updated_at              TIMESTAMPTZ
)

-- Per-topic knowledge tracking
child_knowledge (
    id          UUID PRIMARY KEY,
    child_id    UUID -> children(id),
    subject     VARCHAR(100),           -- e.g. "math"
    topic       VARCHAR(200),           -- e.g. "geometry: segments"
    status      VARCHAR(50),            -- knows | learning | struggling
    score       SMALLINT (0-100),
    notes       TEXT,
    updated_at  TIMESTAMPTZ,
    UNIQUE (child_id, subject, topic)
)

-- Child interests (tags for personalization)
child_interests (
    id          UUID PRIMARY KEY,
    child_id    UUID -> children(id),
    tag         VARCHAR(100),           -- e.g. "космос", "машинки", "логика"
    created_at  TIMESTAMPTZ,
    UNIQUE (child_id, tag)
)

-- Recurring error patterns
error_patterns (
    id          UUID PRIMARY KEY,
    child_id    UUID -> children(id),
    subject     VARCHAR(100),
    topic       VARCHAR(200),
    error_tag   VARCHAR(100),           -- e.g. "ray_vs_segment", "figure_vs_length"
    count       INTEGER DEFAULT 1,      -- number of times this error occurred
    last_seen   TIMESTAMPTZ,
    UNIQUE (child_id, subject, error_tag)
)

-- Educational content + vector embeddings (RAG)
knowledge_base (
    id                  UUID PRIMARY KEY,
    subject             VARCHAR(100),
    topic               VARCHAR(200),
    content             TEXT,
    image_descriptions  TEXT,           -- Vision model output (PDF pages)
    source_file         VARCHAR(500),
    page_number         INTEGER,        -- PDF page (NULL for text/json)
    image_path          VARCHAR(500),   -- Path to page PNG
    difficulty          VARCHAR(50),    -- easy / medium / hard (from JSON tasks)
    tags                TEXT[],         -- PostgreSQL array of tags (from JSON tasks)
    embedding           vector(384),    -- sentence-transformer embedding
    created_at          TIMESTAMPTZ,
    UNIQUE (source_file, topic),        -- Text/JSON dedup
    UNIQUE (source_file, page_number)   -- PDF dedup
)

-- Open WebUI user -> child mapping
user_mappings (
    user_id     TEXT PRIMARY KEY,       -- Open WebUI user ID
    child_id    UUID -> children(id),
    created_at  TIMESTAMPTZ
)

-- Analytics and profile update log
events (
    id          UUID PRIMARY KEY,
    child_id    UUID -> children(id),
    event_type  VARCHAR(100),           -- e.g. "profile_update"
    payload     JSONB,
    created_at  TIMESTAMPTZ
)
```

### Indexes

- `knowledge_base_embedding_idx` — IVFFlat on `embedding` column (cosine, lists=100)
- `events_child_id_idx` — on `(child_id, created_at DESC)` for analytics queries

### Applying Schema

```bash
python scripts/apply_schema.py
```

Reads `db/schema.sql` and executes via psycopg2. Safe to re-run (all statements use `IF NOT EXISTS`).

---

## Knowledge Base (RAG)

Vector search over educational content using pgvector.

### How It Works

1. Content is embedded using sentence-transformers (384-dimensional vectors)
2. When a child asks a question, the query is embedded with the same model
3. pgvector finds the top-K most similar content by cosine similarity
4. Results are injected into the system prompt as `<relevant_material>`

### Embedding Model

Auto-detection with fallback:

| Priority | Model | Language | When |
|---|---|---|---|
| 1st | `paraphrase-multilingual-MiniLM-L12-v2` | Multilingual (Russian) | Default |
| 2nd | `all-MiniLM-L6-v2` | English-only | If multilingual unavailable |

Set `EMBEDDING_MODEL=auto` in `.env` (default) for auto-detection, or specify a model name explicitly.

### Data Formats

**Text documents** (`*.txt`, `*.md`, `*.docx`, `*.rtf`):
Text mode reads files from the directory passed via `--dir`.
The directory does not have to be `data/`, but `data/` or `books/` is commonly used for local RAG assets.
```
TOPIC: geometry: segments
SUBJECT: math

Content text here in Russian...
```

**JSON task banks** (`data/*.json`):
```json
[
  {
    "task_id": "math3-1-001",
    "topic_id": "arithmetic_basics",
    "subject": "math",
    "grade": 3,
    "task_type": "calculation",
    "difficulty": "easy",
    "content": "Вычисли: 27 + 40; 18 + 6",
    "answer": "67; 24",
    "tags": ["сложение", "устный счет"]
  }
]
```

**PDF textbooks** (via `--mode pdf`):
Each page becomes a separate knowledge_base row with raw text + vision description + embedding.

### Ingesting Data

```bash
# Text files (reads TOPIC: and SUBJECT: headers)
uv run python scripts/ingest.py --mode pdf \
  --file "books/textbook.pdf" \

# JSON task bank (each task = separate row)
uv run python scripts/ingest.py --mode json --file data/math_tasks.json


# PDF with vision descriptions (Phase 2)
# Rendered page images are generated automatically into `data/images/`.
# PDF textbook from /books with vision descriptions. if `--pages == None`, the whole book will be processed.
uv run python scripts/ingest.py --mode pdf \
  --file "books/3 класс 1 часть_Математика.pdf" \
  --topic "Математика 3 класс, часть 1" \
  --subject math \
  --pages 1-50
```

All modes support **deduplication** — re-running updates existing rows, does not create duplicates.

### RAG Configuration

| Variable | Default | Description |
|---|---|---|
| `RAG_TOP_K` | 3 | Number of results to return |
| `RAG_MIN_SCORE` | 0.3 | Minimum cosine similarity threshold |
| `EMBEDDING_MODEL` | auto | Model name or "auto" for fallback |

---

## Pipeline Modules

### config.py

Single source of truth for all settings. Reads `.env` via python-dotenv.

### db.py

- `get_connection()` — Returns psycopg2 connection with RealDictCursor (named fields)
- `get_child_full_profile(child_id)` — JOINs `children` + `child_profiles` + `child_knowledge` + `child_interests` + `error_patterns`, returns dict with all profile data, knowledge, interests, and error patterns
- `insert_test_child()` — Creates test child "Misha" with 3 knowledge records, 3 interests, 2 error patterns

### rag.py

- `_get_model()` — Lazy-loads embedding model (multilingual preferred, English fallback)
- `embed_text(text)` — Encodes text to 384d vector
- `search_knowledge(query, subject=None, difficulty=None, tags=None)` — Cosine similarity search via pgvector with optional filters by subject, difficulty, and tags (PostgreSQL array overlap)

### prompt_builder.py

Loads base prompt from `prompts/system_prompt.txt` (592 lines of pedagogical instructions in Russian with XML-tagged sections).

`build_system_prompt()` assembles:
1. Base pedagogical prompt
2. `<child_safe_profile>` — name, age (safe for child to see)
3. `<child_learning_profile>` — learning preferences, interests, speed, knowledge state grouped by status
4. `<error_patterns>` — recurring mistakes sorted by frequency
5. `<relevant_material>` — RAG results including image descriptions from PDF pages

### filter_function.py

Open WebUI Filter with two hooks:

**`inlet(body, user)`** — before LLM:
1. Extracts child_id from `[child_id:UUID]` message prefix (testing) or `user_mappings` table (production)
2. Falls back to `DEFAULT_CHILD_ID`
3. Loads profile, runs RAG search, builds system prompt
4. Injects system message into conversation

**`outlet(body, user)`** — after LLM response:
1. Counts user messages
2. Every 5 messages triggers Profile Agent
3. Logs events to database
4. Never blocks the response

Speaker detection is currently **disabled** (Phase 2) — all users are treated as children. Re-enable by searching for `# SPEAKER_DETECT` comments.

### event_logger.py

`log_event(child_id, event_type, payload)` — Inserts JSONB event into `events` table. Never raises exceptions.

---

## Profile Agent

Automatically updates child's knowledge profile based on conversation analysis.

### How It Works

```
Every 5 user messages:
  1. Collect last 10 messages from conversation
  2. Load child's current profile from DB
  3. Send to LLM (qwen3-vl-4b via LM Studio :1234)
  4. LLM returns JSON assessments per topic + error tags
  5. Apply score deltas with clamping
  6. Update child_knowledge in PostgreSQL
  7. Upsert error_patterns (increment count per error_tag)
  8. Log event to events table
```

### Assessment Output (from LLM)

```json
[
  {
    "topic": "geometry: segments",
    "score_delta": 10,
    "new_status": "learning",
    "reasoning": "Child correctly identified segment endpoints after hint",
    "error_tags": ["ray_vs_segment"]
  }
]
```

### Score Update Rules

| Rule | Value |
|---|---|
| Max score delta per assessment | +15 |
| Min score delta per assessment | -10 |
| Score range | 0-100 (clamped) |
| New topic base score | 50 |
| `struggling` -> `learning` threshold | score >= 40 |
| `learning` -> `knows` threshold | score >= 75 |

### Configuration

| Variable | Default | Description |
|---|---|---|
| `PROFILE_LLM_API_URL` | `http://127.0.0.1:1234/v1` | LM Studio endpoint |
| `PROFILE_LLM_MODEL` | `qwen/qwen3-vl-4b` | Vision model for assessment |
| `PROFILE_UPDATE_INTERVAL` | 5 | Messages between updates |

Uses a **separate LLM endpoint** (LM Studio), not Open WebUI, to avoid proxy loops.

---

## Multimodal Ingest

PDF textbook ingestion with vision-based page descriptions (Phase 2).

### Pipeline

```
PDF file
  │
  pymupdf ──> extract text + render page to PNG (200 DPI)
  │
  for each page:
  ├── raw text from PDF
  ├── PNG saved to data/images/
  └── PNG sent to qwen3-vl (LM Studio :1234)
      │
      └── text description of diagrams, figures, tasks
          │
          combined = raw_text + description
          │
          sentence-transformer ──> embedding (384d)
          │
          INSERT into knowledge_base
            (content, image_descriptions, image_path, embedding)
```

### Why Not CLIP

- CLIP would require a second embedding column + index + separate search path
- CLIP text search in Russian is weak
- For our use case (child asks text question -> find textbook page), text search is sufficient
- Instead: qwen3-vl converts images to rich text descriptions, then embed text once

### Performance

~3-7 seconds per page on RTX 4070 Ti Super (mainly qwen3-vl-4b inference). A 200-page textbook takes ~15-20 minutes (one-time batch).

---
## Local RAG API Server

`server.py` provides a local FastAPI server for testing and using TeachCopilot as an OpenAI-compatible API.

It exposes:

- `GET /health` — health check
- `GET /profile` — returns the default or requested child profile
- `POST /rag/search` — direct RAG search endpoint
- `POST /v1/chat/completions` — OpenAI-compatible chat endpoint
- `GET /v1/models` — OpenAI-compatible models endpoint

The chat endpoint:

1. extracts the latest user message,
2. searches the local knowledge base via RAG,
3. builds a child-friendly system message with RAG context,
4. forwards the request to LM Studio at `http://127.0.0.1:1234/v1/chat/completions`,
5. removes hidden `reasoning_content` from the model response before returning it.


---
## Installation

### Prerequisites

- Python 3.10+
- PostgreSQL with pgvector extension
- LM Studio with qwen3-vl-4b model (for Profile Agent and PDF vision)

### Steps

```bash
# 1. Clone repository
git clone <repository-url>
cd MachineLearning

# 2. Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 3. Install dependencies (you can also do it w/o `uv`)
winget install astral-sh.uv
uv init
uv add -r requirements.txt

# 4. Install pgvector extension in PostgreSQL
# (run as superuser in psql)
CREATE EXTENSION vector;

# 5. Create database
createdb teachcopilot

# 6. Configure environment
cp .env.example .env  # or create .env manually (see Configuration)

# 7. Apply database schema
uv run python scripts/apply_schema.py

# 8. Create test child profile
uv run python -c "from pipeline.db import insert_test_child; insert_test_child()"

# 9. Ingest educational content
uv run python scripts/ingest.py --dir data --mode text
uv run python scripts/ingest.py --mode json --file data/math_tasks.json

# 10. Run tests. (the tests don't require a running server.py)
uv run python scripts/test_pipeline.py

# 11. Start local RAG API server
uv run uvicorn server:app --host 0.0.0.0 --port 8099
```

---

## Configuration

Environment variables in `.env`:

```ini
# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/teachcopilot

# Local RAG API server
TEACHCOPILOT_DEBUG_RAG=0
# LM Studio is currently expected at:
# http://127.0.0.1:1234/v1

# Embedding model: "auto" for multilingual with fallback, or explicit model name
EMBEDDING_MODEL=auto

# RAG search
RAG_TOP_K=3
RAG_MIN_SCORE=0.3         # raise to 0.5 for production with multilingual model

# Profile Agent (LM Studio endpoint, NOT Open WebUI)
PROFILE_LLM_API_URL=http://127.0.0.1:1234/v1
PROFILE_LLM_MODEL=qwen/qwen3-vl-4b
PROFILE_UPDATE_INTERVAL=5

# Default child for fallback
DEFAULT_CHILD_ID=00000000-0000-0000-0000-000000000001

# Logging
LOG_LEVEL=DEBUG

# Salt for data hashing
TEACHCOPILOT_SALT=<random-hex-string>
```

---

## Usage

### Profile Management CLI

```bash
# List all children
uv run python scripts/manage_profiles.py list

# Show full profile
uv run python scripts/manage_profiles.py show 00000000-0000-0000-0000-000000000001

# Create new child
uv run python scripts/manage_profiles.py create \
  --name "Аня" --age-group "9-11" --grade "4" \
  --style "simple, with examples" --pace medium

# Add/update knowledge record
uv run python scripts/manage_profiles.py add-knowledge 00000000-0000-0000-0000-000000000001 \
  --subject math --topic "geometry: angles" --status learning --score 60

# Update knowledge score
uv run python scripts/manage_profiles.py update-knowledge 00000000-0000-0000-0000-000000000001 \
  --topic "geometry: segments" --score 55 --status learning

# Link Open WebUI user to child profile
uv run python scripts/manage_profiles.py link-user 00000000-0000-0000-0000-000000000001 \
  --user-id 8b2a57080ca0410e8e00e1d3d68ee246

# Add interest tag
uv run python scripts/manage_profiles.py add-interest 00000000-0000-0000-0000-000000000001 \
  --tag "космос"

# Remove interest tag
uv run python scripts/manage_profiles.py remove-interest 00000000-0000-0000-0000-000000000001 \
  --tag "космос"

# Delete profile
uv run python scripts/manage_profiles.py delete 00000000-0000-0000-0000-000000000001

# View recent events
uv run python scripts/manage_profiles.py events 00000000-0000-0000-0000-000000000001 --last 10
```

### Local RAG API Server


```bash
# Local RAG API Server
uv run uvicorn server:app --host 0.0.0.0 --port 8099
# Health check
curl http://localhost:8099/health

# Direct RAG search
curl -X POST http://localhost:8099/rag/search \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"что такое отрезок?\",\"limit\":5}"

# OpenAI-compatible chat endpoint:
curl -X POST http://localhost:8099/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Объясни, что такое угол\"}]}"
```

### Debug: View Assembled Prompt

```bash
uv run python scripts/show_prompt.py "сколько будет 5 + 3"
uv run python scripts/show_prompt.py "что такое отрезок" --child-id 00000000-0000-0000-0000-000000000001
```

Shows: child profile, RAG hits with scores, full system prompt text, total character count.

### Open WebUI Filter Installation

See [FILTER_INSTALL.md](FILTER_INSTALL.md) for step-by-step instructions.

Quick summary:
1. Open WebUI -> Workspace -> Functions -> New Function
2. Type = Filter, name = "TeachCopilot Filter"
3. Paste `pipeline/filter_function.py` (the Filter class + imports)
4. Enable filter for your model in Workspace -> Models

Test with message prefix: `[child_id:00000000-0000-0000-0000-000000000001] объясни что такое отрезок`

---

## Testing

```bash
uv run python scripts/test_pipeline.py
```

Runs 27 integration checks across 9 steps:

| Step | What is checked |
|---|---|
| 1. Config | DATABASE_URL, EMBEDDING_MODEL, PROFILE_LLM_API_URL loaded |
| 2. Database | Profile loads, interests, error_patterns, child_knowledge ON CONFLICT dedup |
| 3. RAG search | Model loads, vector search returns relevant results |
| 4. Prompt builder | Loads from file, contains XML tags, mentions child name |
| 5. Filter Function | inlet/outlet hooks execute without errors |
| 6. Dedup | Re-ingesting same data doesn't create duplicates |
| 7. User mappings | Known user resolves to child; unknown user uses default |
| 8. Profile Agent | `should_update(5)` triggers, `should_update(3)` doesn't |
| 9. Multimodal | Checks image_descriptions populated (skipped if no PDF ingested) |

Expected output: `27/27 passed, ALL STEPS PASSED`

---

## Deployment

### Production Server Setup
Can be completed w/o `uv`
```bash
# 1. Clone and install
git clone https://github.com/theycomeunstuck/TeachCopilot
python -m venv .venv && .venv\Scripts\activate
uv add -r requirements.txt

# 2. Configure .env for production
DATABASE_URL=postgresql://user:pass@localhost:5432/teachcopilot
EMBEDDING_MODEL=auto
RAG_MIN_SCORE=0.5         # higher threshold for multilingual model
LOG_LEVEL=INFO

# 3. Enable GPU in pipeline/rag.py (line 22)
# Change: SentenceTransformer(_EMBEDDING_PREFERRED)
# To:     SentenceTransformer(_EMBEDDING_PREFERRED, device='cuda')

# 4. Apply schema and ingest
uv run python scripts/apply_schema.py
uv run python scripts/ingest.py --dir data --mode text
uv run python scripts/ingest.py --mode json --file data/math_tasks.json

# 5. Start LM Studio with qwen3-vl-4b on port 1234

# 6. Install Filter Function in Open WebUI (see FILTER_INSTALL.md) for a dynamic system prompt

# 9. Create child profiles and link users
uv run python scripts/manage_profiles.py create --name "Аня" --age-group "9-11" --grade "3"
uv run python scripts/manage_profiles.py link-user <child-uuid> --user-id <openwebui-user-id>

# 8. Verify
uv run python scripts/test_pipeline.py

# 9. Start TeachCopilot RAG API server
uv run uvicorn server:app --host 0.0.0.0 --port 8099
```

### UUID Compatibility

The database uses standard UUID with dashes: `00000000-0000-0000-0000-000000000001`.
`user_mappings.user_id` is TEXT — any format works (hex without dashes, UUID with dashes, etc.).
For example, `uuid.uuid4().hex` format `8b2a57080ca0410e8e00e1d3d68ee246` is accepted.

---

## Project Phases

| Phase | Scope | Status |
|---|---|---|
| **Phase 1 (MVP)** | Math text-only, child profile, RAG, Filter Function, profile CLI | Done |
| **Phase 2** | Multimodal PDF ingest (qwen3-vl vision), Profile Agent (dynamic updates), dedup, JSON task banks, embedding auto-detect | Done |
| **Phase 3** | Multiple subjects, Orchestrator, Task/Narrative Agent, missions, group dynamics, Stable Diffusion, teacher UI, voice age detection | Planned |

### Phase 3 Roadmap

- CLIP embeddings for photo-of-notebook -> find page reverse search
- Orchestrator agent for routing between specialized agents
- Task/Narrative Agent for generating personalized exercises
- Multiple subjects beyond math
- Group dynamics for classroom scenarios
- Stable Diffusion for visual explanations
- Attempt Analyzer for detailed mistake classification
- Teacher dashboard UI
- Voice-based age/speaker detection (replace user_mappings)
