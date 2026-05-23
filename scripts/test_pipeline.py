# scripts/test_pipeline.py
"""End-to-end integration test for TeachCopilot pipeline (22+ checks)."""
import sys
import io
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TEST_CHILD_ID = "00000000-0000-0000-0000-000000000001"
passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  [OK]   {label}")
        passed += 1
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        failed += 1


print("=" * 60)
print("TeachCopilot Pipeline — Integration Test (Phase 2)")
print("=" * 60)

# ================================================================
# Step 1: config
# ================================================================
print("\n--- Step 1: config ---")
from pipeline.config import (
    DATABASE_URL, EMBEDDING_MODEL, PROFILE_LLM_API_URL, PROFILE_UPDATE_INTERVAL
)
check("DATABASE_URL set", bool(DATABASE_URL))
check("EMBEDDING_MODEL set", bool(EMBEDDING_MODEL))
check("PROFILE_LLM_API_URL set", bool(PROFILE_LLM_API_URL))
check("PROFILE_UPDATE_INTERVAL is int", isinstance(PROFILE_UPDATE_INTERVAL, int))

# ================================================================
# Step 2: database
# ================================================================
print("\n--- Step 2: database ---")
from pipeline.db import get_child_full_profile, get_connection

# Ensure test child with interests/errors exists before loading profile
from pipeline.db import insert_test_child
insert_test_child()

profile = get_child_full_profile(TEST_CHILD_ID)
check("Profile loaded (Миша)", profile is not None and profile.get("name") == "Миша")

knowledge = profile.get("knowledge") if profile else []
check("Has 3 knowledge records", knowledge is not None and len(knowledge) == 3,
      f"got {len(knowledge) if knowledge else 0}")

interests = profile.get("interests") if profile else []
check("Profile has interests", interests is not None and len(interests) >= 1,
      f"got {interests}")

error_pats = profile.get("error_patterns") if profile else []
check("Profile has error_patterns", error_pats is not None and len(error_pats) >= 1,
      f"got {error_pats}")

# ON CONFLICT test: insert_test_child twice should not raise
try:
    insert_test_child()
    insert_test_child()
    profile2 = get_child_full_profile(TEST_CHILD_ID)
    k2 = profile2.get("knowledge") if profile2 else []
    check("Knowledge ON CONFLICT works (no duplicates)",
          k2 is not None and len(k2) == 3,
          f"got {len(k2) if k2 else 0}")
except Exception as e:
    check("Knowledge ON CONFLICT works (no duplicates)", False, str(e))

# ================================================================
# Step 3: RAG search
# ================================================================
print("\n--- Step 3: RAG search ---")
from pipeline.rag import search_knowledge

results = search_knowledge("что такое отрезок", subject="math")
check("RAG returns >=1 result", len(results) >= 1, f"got {len(results)}")
if results:
    top = results[0]
    relevant = ("segment" in top.get("topic", "").lower()
                or "отрезок" in top.get("content", "").lower())
    check("Top result relevant", relevant)

# RAG filter by difficulty
results_easy = search_knowledge("задачи на сложение", difficulty="easy")
check("RAG difficulty filter works",
      all(r.get("difficulty") == "easy" for r in results_easy) if results_easy else True,
      f"got {len(results_easy)} results")

# RAG filter by tags
results_tags = search_knowledge("уравнения", tags=["уравнение"])
check("RAG tags filter works",
      all("уравнение" in (r.get("tags") or []) for r in results_tags) if results_tags else True,
      f"got {len(results_tags)} results")

# ================================================================
# Step 4: prompt builder
# ================================================================
print("\n--- Step 4: prompt builder ---")
from pipeline.prompt_builder import build_system_prompt, BASE_SYSTEM_PROMPT, ADULT_SYSTEM_PROMPT

# Check prompt loaded from file (not stub fallback)
check("Prompt loaded from file (not stub)",
      len(BASE_SYSTEM_PROMPT) > 500,
      f"len={len(BASE_SYSTEM_PROMPT)}")

check("Prompt contains <system_initialization>",
      "<system_initialization>" in BASE_SYSTEM_PROMPT)

# Build child prompt with profile and RAG
child_prompt = build_system_prompt(BASE_SYSTEM_PROMPT, profile, results,
                                    speaker_type="child")

check("Prompt contains <child_safe_profile>",
      "<child_safe_profile>" in child_prompt)

check("Prompt mentions Миша", "Миша" in child_prompt)

check("Prompt has Типичные_трудности section",
      "Типичные_трудности" in child_prompt)

# Speaker detection disabled — adult prompt = child prompt now
adult_prompt = build_system_prompt(BASE_SYSTEM_PROMPT, profile, results,
                                    speaker_type="adult")
check("Adult prompt = child prompt (speaker detection disabled)",
      adult_prompt == child_prompt)

# ================================================================
# Step 5: filter function
# ================================================================
print("\n--- Step 5: filter function ---")
try:
    from pipeline.filter_function import Filter
    check("filter_function imports OK", True)
except Exception as e:
    check("filter_function imports OK", False, str(e))

# inlet test
try:
    f = Filter()
    body = {
        "messages": [
            {"role": "user", "content": f"[child_id:{TEST_CHILD_ID}] Привет!"}
        ]
    }
    result_body = f.inlet(body, user={"id": "test_user"})
    msgs = result_body.get("messages", [])
    has_system = any(m.get("role") == "system" for m in msgs)
    check("inlet hook works", has_system, "no system message injected")
except Exception as e:
    check("inlet hook works", False, str(e))

# outlet test (should return body without errors, profile update won't fire
# because message count won't hit the interval threshold)
try:
    body_out = {
        "messages": [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "ok"}
        ]
    }
    result_out = f.outlet(body_out, user={"id": "unknown_user"})
    check("outlet hook works", result_out is not None)
except Exception as e:
    check("outlet hook works", False, str(e))

# ================================================================
# Step 6: dedup
# ================================================================
print("\n--- Step 6: dedup ---")
try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM knowledge_base")
            count_before = cur.fetchone()["cnt"]

    # Re-ingest text files
    sys.path.insert(0, str(Path(__file__).parent))
    from scripts.ingest import ingest_text
    data_dir = Path(__file__).parent.parent / "data"
    ingest_text(data_dir)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM knowledge_base")
            count_after = cur.fetchone()["cnt"]

    check("Double ingest: row count unchanged",
          count_before == count_after,
          f"before={count_before}, after={count_after}")
except Exception as e:
    check("Double ingest: row count unchanged", False, str(e))

# ================================================================
# Step 7: user_mappings
# ================================================================
print("\n--- Step 7: user_mappings ---")
try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Insert test mapping
            cur.execute("""
                INSERT INTO user_mappings (user_id, child_id)
                VALUES ('test_pipeline_user', %s)
                ON CONFLICT (user_id) DO UPDATE SET child_id = EXCLUDED.child_id
            """, (TEST_CHILD_ID,))
        conn.commit()

    from pipeline.filter_function import _resolve_child_id
    cid, stype = _resolve_child_id("test_pipeline_user")
    check("Known user -> child mode",
          cid == TEST_CHILD_ID and stype == "child",
          f"got child_id={cid}, speaker={stype}")

    cid2, stype2 = _resolve_child_id("nonexistent_user_xyz")
    check("Unknown user -> child mode (speaker detection disabled)",
          stype2 == "child",
          f"got speaker={stype2}")

    # Cleanup test mapping
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_mappings WHERE user_id = 'test_pipeline_user'")
        conn.commit()
except Exception as e:
    check("Known user -> child mode", False, str(e))
    check("Unknown user -> adult mode", False, str(e))

# ================================================================
# Step 8: profile agent
# ================================================================
print("\n--- Step 8: profile agent ---")
from pipeline.profile_agent import should_update

check("should_update(5) == True", should_update(5) is True)
check("should_update(3) == False", should_update(3) is False)

# ================================================================
# Step 9: multimodal (requires prior PDF ingest)
# ================================================================
print("\n--- Step 9: multimodal ---")
try:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM knowledge_base
                WHERE page_number IS NOT NULL AND image_path IS NOT NULL
                  AND image_path != ''
            """)
            img_count = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT COUNT(*) AS cnt FROM knowledge_base
                WHERE page_number IS NOT NULL AND image_descriptions IS NOT NULL
                  AND image_descriptions != ''
            """)
            desc_count = cur.fetchone()["cnt"]

    if img_count == 0 and desc_count == 0:
        print("  [SKIP] No PDF pages ingested yet — run: python scripts/ingest.py --mode pdf ...")
        print("         These checks will pass after PDF ingestion.")
    else:
        check("PDF pages have image_path set", img_count > 0,
              f"found {img_count} rows with image_path")
        check("PDF pages have image_descriptions filled", desc_count > 0,
              f"found {desc_count} rows with descriptions")
except Exception as e:
    check("PDF pages have image_path set", False, str(e))
    check("PDF pages have image_descriptions filled", False, str(e))

# ================================================================
# Summary
# ================================================================
print("\n" + "=" * 60)
total = passed + failed
print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
if failed == 0:
    print("ALL STEPS PASSED")
else:
    print(f"!!! {failed} STEP(S) FAILED — fix before continuing")
print("=" * 60)
