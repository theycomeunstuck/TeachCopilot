# scripts/ingest.py
"""Embed text documents and insert into knowledge_base (pgvector).
Supports --mode text (dedup) and --mode pdf (qwen3-vl multimodal)."""
import sys
import argparse
import base64
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from pipeline.config import (
    DATABASE_URL, PROFILE_LLM_API_URL, PROFILE_LLM_MODEL
)
from pipeline.rag import embed_text

IMAGES_DIR = Path(__file__).parent.parent / "data" / "images"

VISION_PROMPT = """Ты помощник учителя математики. Опиши содержимое этой страницы
из школьного учебника для 3-4 класса.

Обязательно опиши:
1. Какие геометрические фигуры изображены (точки, прямые, лучи, отрезки, углы, треугольники, круги)
2. Какие обозначения использованы (буквы у точек, названия фигур)
3. Если есть задание или объяснение темы — перепиши его текст
4. Если есть числовые данные — укажи их

Отвечай на русском. Будь точным — ученик будет работать с этим описанием."""


# ---- Text mode ----

SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".rtf"}


def read_file_text(file_path: Path) -> str:
    """Extract plain text from .txt, .md, .docx, or .rtf files."""
    ext = file_path.suffix.lower()
    if ext in (".txt", ".md"):
        return file_path.read_text(encoding="utf-8")
    elif ext == ".docx":
        from docx import Document  # pip install python-docx
        doc = Document(str(file_path))
        return "\n".join(p.text for p in doc.paragraphs)
    elif ext == ".rtf":
        from striprtf.striprtf import rtf_to_text  # pip install striprtf
        raw = file_path.read_bytes().decode("utf-8", errors="replace")
        return rtf_to_text(raw)
    raise ValueError(f"Unsupported file type: {ext}")


def parse_document(file_path: Path) -> dict:
    """Parse TOPIC: and SUBJECT: from first two lines, rest is content."""
    text = read_file_text(file_path)
    lines = text.strip().splitlines()

    topic = ""
    subject = ""
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("TOPIC:"):
            topic = stripped[len("TOPIC:"):].strip()
        elif stripped.upper().startswith("SUBJECT:"):
            subject = stripped[len("SUBJECT:"):].strip()
        elif stripped == "":
            continue
        else:
            content_start = i
            break

    content = "\n".join(lines[content_start:]).strip()
    return {"topic": topic, "subject": subject, "content": content,
            "source_file": str(file_path.name)}


def ingest_file(cur, doc: dict) -> str:
    """Embed content and upsert into knowledge_base. Returns 'insert' or 'update'."""
    embedding = embed_text(doc["content"])
    cur.execute("""
        INSERT INTO knowledge_base (subject, topic, content, source_file, embedding)
        VALUES (%s, %s, %s, %s, %s::vector)
        ON CONFLICT (source_file, topic)
        DO UPDATE SET content = EXCLUDED.content,
                      embedding = EXCLUDED.embedding
        RETURNING (xmax = 0) AS is_insert
    """, (doc["subject"], doc["topic"], doc["content"],
          doc["source_file"], embedding))
    row = cur.fetchone()
    return "insert" if row and row[0] else "update"


def ingest_text(data_dir: Path):
    files = sorted(
        f for ext in SUPPORTED_EXTENSIONS for f in data_dir.glob(f"*{ext}")
    )
    if not files:
        print(f"No supported files ({', '.join(SUPPORTED_EXTENSIONS)}) found in {data_dir}")
        return

    print(f"Found {len(files)} file(s) in {data_dir}")
    stats = {"new": 0, "updated": 0}

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for f in files:
                doc = parse_document(f)
                result = ingest_file(cur, doc)
                stats["new" if result == "insert" else "updated"] += 1
                print(f"  [OK] {f.name} -> topic={doc['topic']!r} ({result})")
        conn.commit()

    print(f"Done: {stats['new']} new, {stats['updated']} updated.")


# ---- PDF mode ----

def describe_image(image_path: Path) -> str:
    """Send page image to qwen3-vl for text description."""
    import requests
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()

    resp = requests.post(
        f"{PROFILE_LLM_API_URL}/chat/completions",
        json={
            "model": PROFILE_LLM_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{img_b64}"
                    }}
                ]
            }],
            "temperature": 0.1,
            "max_tokens": 1000
        },
        timeout=120
    )
    return resp.json()["choices"][0]["message"]["content"]


def upsert_pdf_page(cur, source_file, page_number, subject, topic,
                    content, image_descriptions, image_path, embedding) -> str:
    """Insert or update a PDF page in knowledge_base. Returns 'insert' or 'update'."""
    cur.execute("""
        INSERT INTO knowledge_base
            (source_file, page_number, subject, topic, content,
             image_descriptions, image_path, embedding)
        VALUES (%(sf)s, %(pn)s, %(subj)s, %(topic)s, %(content)s,
                %(desc)s, %(img)s, %(emb)s::vector)
        ON CONFLICT (source_file, page_number)
        DO UPDATE SET
            content = EXCLUDED.content,
            image_descriptions = EXCLUDED.image_descriptions,
            image_path = EXCLUDED.image_path,
            embedding = EXCLUDED.embedding,
            topic = EXCLUDED.topic
        RETURNING (xmax = 0) AS is_insert
    """, {
        "sf": source_file, "pn": page_number,
        "subj": subject, "topic": topic,
        "content": content, "desc": image_descriptions,
        "img": image_path, "emb": embedding
    })
    row = cur.fetchone()
    return "insert" if row and row[0] else "update"


def ingest_pdf(pdf_path: Path, subject: str, topic_prefix: str,
               page_range: str | None = None):
    """Ingest a PDF: one row per page with image + description + embedding."""
    import fitz  # pymupdf

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    # Parse page range
    start_page, end_page = 0, total_pages
    if page_range:
        parts = page_range.split("-")
        start_page = int(parts[0]) - 1  # 0-based
        end_page = int(parts[1]) if len(parts) > 1 else int(parts[0])

    print(f"Processing {pdf_path.name}: pages {start_page+1}-{end_page} of {total_pages}")
    stats = {"new": 0, "updated": 0, "skipped": 0}

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for page_num in range(start_page, end_page):
                page = doc[page_num]
                page_idx = page_num + 1  # 1-based

                # 1. Extract text
                raw_text = page.get_text("text").strip()

                # 2. Render page as image
                pix = page.get_pixmap(dpi=200)
                img_filename = f"{pdf_path.stem}_p{page_idx:03d}.png"
                img_path = IMAGES_DIR / img_filename
                pix.save(str(img_path))

                # 3. Get vision description
                description = ""
                if raw_text or page.get_images():
                    try:
                        description = describe_image(img_path)
                        print(f"  p.{page_idx}: vision OK ({len(description)} chars)")
                    except Exception as e:
                        print(f"  p.{page_idx}: vision FAILED ({e}), text only")

                # 4. Combine content for embedding
                combined = "\n".join(filter(None, [raw_text, description]))
                if not combined.strip():
                    stats["skipped"] += 1
                    print(f"  p.{page_idx}: SKIPPED (blank)")
                    continue

                # 5. Embed
                embedding = embed_text(combined)

                # 6. Upsert
                topic = f"{topic_prefix} (стр. {page_idx})"
                rel_img_path = str(img_path.relative_to(Path(__file__).parent.parent))
                result = upsert_pdf_page(
                    cur,
                    source_file=pdf_path.name,
                    page_number=page_idx,
                    subject=subject,
                    topic=topic,
                    content=raw_text,
                    image_descriptions=description,
                    image_path=rel_img_path,
                    embedding=embedding,
                )
                stats["new" if result == "insert" else "updated"] += 1

        conn.commit()

    doc.close()
    total = stats["new"] + stats["updated"] + stats["skipped"]
    print(f"Done: {pdf_path.name} — {total} pages "
          f"({stats['new']} new, {stats['updated']} updated, {stats['skipped']} blank)")


# ---- JSON mode (task banks) ----

def ingest_json(json_path: Path):
    """Ingest JSON task bank: each task becomes a separate knowledge_base row."""
    import json as _json
    tasks = _json.loads(json_path.read_text(encoding="utf-8"))
    if not tasks:
        print(f"No tasks found in {json_path}")
        return

    print(f"Found {len(tasks)} task(s) in {json_path.name}")
    stats = {"new": 0, "updated": 0}

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for t in tasks:
                task_id = t.get("task_id", "unknown")
                topic_id = t.get("topic_id", "general")
                subject = t.get("subject", "math")
                content = t.get("content", "")
                answer = t.get("answer", "")
                tags = ", ".join(t.get("tags", []))
                difficulty = t.get("difficulty", "")
                grade = t.get("grade", "")

                # Content for embedding: question + tags + answer context
                embed_content = f"{content}\nТеги: {tags}\nОтвет: {answer}"

                embedding = embed_text(embed_content)
                topic = f"{topic_id}: {task_id}"

                cur.execute("""
                    INSERT INTO knowledge_base
                        (subject, topic, content, source_file, difficulty, tags, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (source_file, topic)
                    DO UPDATE SET content = EXCLUDED.content,
                                  difficulty = EXCLUDED.difficulty,
                                  tags = EXCLUDED.tags,
                                  embedding = EXCLUDED.embedding
                    RETURNING (xmax = 0) AS is_insert
                """, (subject, topic, embed_content, json_path.name,
                      difficulty or None,
                      t.get("tags") or None,
                      embedding))
                row = cur.fetchone()
                result = "insert" if row and row[0] else "update"
                stats["new" if result == "insert" else "updated"] += 1
                print(f"  [OK] {task_id} -> topic={topic!r} ({result})")
        conn.commit()

    print(f"Done: {stats['new']} new, {stats['updated']} updated.")


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description="Ingest documents into knowledge_base")
    parser.add_argument("--dir", type=str, help="Directory with .txt files (text mode)")
    parser.add_argument("--mode", type=str, default="text", choices=["text", "pdf", "json"],
                        help="Ingestion mode")
    parser.add_argument("--file", type=str, help="PDF file path (pdf mode)")
    parser.add_argument("--subject", type=str, default="math", help="Subject name")
    parser.add_argument("--topic", type=str, help="Topic prefix (pdf mode)")
    parser.add_argument("--pages", type=str, help="Page range, e.g. 1-50 (pdf mode)")
    args = parser.parse_args()

    if args.mode == "text":
        if not args.dir:
            print("Error: --dir required for text mode")
            return
        ingest_text(Path(args.dir))
    elif args.mode == "pdf":
        if not args.file:
            print("Error: --file required for pdf mode")
            return
        if not args.topic:
            print("Error: --topic required for pdf mode")
            return
        pdf_path = Path(args.file)
        if not pdf_path.exists():
            print(f"Error: file not found: {pdf_path}")
            return
        ingest_pdf(pdf_path, args.subject, args.topic, args.pages)
    elif args.mode == "json":
        if not args.file:
            print("Error: --file required for json mode")
            return
        json_path = Path(args.file)
        if not json_path.exists():
            print(f"Error: file not found: {json_path}")
            return
        ingest_json(json_path)


if __name__ == "__main__":
    main()
