# pipeline/profile_agent.py
"""Dynamic child profile updates based on LLM conversation analysis.
Calls LM Studio directly (not via Open WebUI) to avoid proxy loops."""
import json
import logging
import requests
from pipeline.config import (
    PROFILE_LLM_API_URL, PROFILE_LLM_MODEL, PROFILE_UPDATE_INTERVAL
)
from pipeline.db import get_child_full_profile, get_connection

logger = logging.getLogger(__name__)

ASSESS_PROMPT = """You are a learning assessment system. Analyze this conversation
between a tutor and a child student.

Child profile:
{profile_json}

Last {n} messages:
{messages}

For each topic discussed, assess:
1. Did the child demonstrate understanding? (score_delta: -10 to +15)
2. What is the appropriate status? (knows / learning / struggling)
3. Brief reasoning (1 sentence)
4. List specific error tags if the child made mistakes (e.g. "ray_vs_segment", "figure_vs_length")

Respond ONLY with valid JSON array, no other text:
[
  {{"topic": "geometry: segments", "score_delta": 10, "new_status": "learning",
    "reasoning": "Child correctly identified segment endpoints after hint",
    "error_tags": ["ray_vs_segment"]}}
]

Rules:
- score_delta is RELATIVE to current score (positive = improved, negative = regressed)
- Only assess topics that were actually discussed
- If unsure, use score_delta: 0 and keep current status
- Maximum +15 per assessment (avoid score inflation)
- Minimum -10 per assessment (avoid discouragement from one bad session)
- error_tags: short snake_case labels for specific mistakes; empty list if no errors"""


def should_update(message_count: int) -> bool:
    """Update every PROFILE_UPDATE_INTERVAL user messages."""
    return message_count > 0 and message_count % PROFILE_UPDATE_INTERVAL == 0


def assess_interaction(child_id: str, messages: list[dict]) -> list[dict]:
    """Ask LLM to assess child's understanding. Returns list of assessments."""
    profile = get_child_full_profile(child_id)
    if not profile:
        return []

    # Take last N user+assistant messages
    recent = [m for m in messages if m.get("role") in ("user", "assistant")]
    recent = recent[-(PROFILE_UPDATE_INTERVAL * 2):]

    prompt = ASSESS_PROMPT.format(
        profile_json=json.dumps(profile, default=str, ensure_ascii=False),
        n=len(recent),
        messages="\n".join(f"{m['role']}: {m['content']}" for m in recent)
    )

    try:
        resp = requests.post(
            f"{PROFILE_LLM_API_URL}/chat/completions",
            json={
                "model": PROFILE_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=30
        )
        text = resp.json()["choices"][0]["message"]["content"]
        # Strip markdown fences if present
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(text)
    except Exception:
        logger.exception("Assessment LLM call failed")
        return []


def update_profile(child_id: str, assessments: list[dict]) -> None:
    """Apply score deltas with clamping, status transitions, and error pattern tracking."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for a in assessments:
                    topic = a.get("topic", "")
                    score_delta = a.get("score_delta", 0)
                    new_status = a.get("new_status", "learning")

                    # Clamp delta
                    score_delta = max(-10, min(15, score_delta))

                    # Get current score
                    cur.execute("""
                        SELECT score, status FROM child_knowledge
                        WHERE child_id = %s AND topic = %s
                    """, (child_id, topic))
                    row = cur.fetchone()

                    if row is None:
                        # New topic discovered — create at base 50 + delta
                        new_score = max(0, min(100, 50 + score_delta))
                        cur.execute("""
                            INSERT INTO child_knowledge
                                (child_id, subject, topic, status, score)
                            VALUES (%s, 'math', %s, %s, %s)
                            ON CONFLICT (child_id, subject, topic) DO NOTHING
                        """, (child_id, topic, new_status, new_score))
                        logger.info("New topic created: %s = %s (%d)",
                                   topic, new_status, new_score)
                    else:
                        current_score = row["score"] or 0
                        new_score = max(0, min(100, current_score + score_delta))

                        # Status transition rules (prevent oscillation)
                        if new_status == "learning" and new_score < 40:
                            new_status = "struggling"
                        elif new_status == "knows" and new_score < 75:
                            new_status = "learning"

                        cur.execute("""
                            UPDATE child_knowledge
                            SET score = %s, status = %s, updated_at = NOW()
                            WHERE child_id = %s AND topic = %s
                        """, (new_score, new_status, child_id, topic))
                        logger.info("Topic updated: %s %d→%d (%s)",
                                   topic, current_score, new_score, new_status)

                    # Upsert error patterns
                    for error_tag in a.get("error_tags", []):
                        if not error_tag:
                            continue
                        cur.execute("""
                            INSERT INTO error_patterns
                                (child_id, subject, topic, error_tag, count, last_seen)
                            VALUES (%s, 'math', %s, %s, 1, NOW())
                            ON CONFLICT (child_id, subject, error_tag)
                            DO UPDATE SET count = error_patterns.count + 1,
                                          topic = EXCLUDED.topic,
                                          last_seen = NOW()
                        """, (child_id, topic, error_tag))
                        logger.info("Error pattern: %s/%s count+1", topic, error_tag)

            conn.commit()
    except Exception:
        logger.exception("update_profile failed for child=%s", child_id)
