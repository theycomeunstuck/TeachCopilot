# pipeline/db.py
import logging
import psycopg2, psycopg2.extras
from pipeline.config import DATABASE_URL

logger = logging.getLogger(__name__)


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def get_child_full_profile(child_id: str) -> dict | None:
    sql = """
        SELECT
            c.id, c.name, c.age_group, c.grade, c.language,
            p.explanation_style, p.pace, p.autonomy_level,
            p.motivation, p.prefers_visual, p.avg_response_time_sec,
            json_agg(
                json_build_object(
                    'subject', k.subject, 'topic', k.topic,
                    'status',  k.status,  'score', k.score, 'notes', k.notes
                )
            ) FILTER (WHERE k.id IS NOT NULL) AS knowledge,
            (SELECT json_agg(ci.tag)
             FROM child_interests ci WHERE ci.child_id = c.id
            ) AS interests,
            (SELECT json_agg(json_build_object(
                        'error_tag', ep.error_tag, 'subject', ep.subject,
                        'topic', ep.topic, 'count', ep.count
                    ))
             FROM error_patterns ep WHERE ep.child_id = c.id
            ) AS error_patterns
        FROM children c
        LEFT JOIN child_profiles p ON p.child_id = c.id
        LEFT JOIN child_knowledge k ON k.child_id = c.id
        WHERE c.id = %s
        GROUP BY c.id, p.id
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (child_id,))
                row = cur.fetchone()
                logger.debug("Profile loaded for child_id=%s", child_id)
                return dict(row) if row else None
    except Exception:
        logger.exception("DB error for child_id=%s", child_id)
        return None


def insert_test_child():
    """Insert known test child. Safe to call multiple times."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO children (id, name, age_group, grade, language)
                VALUES ('00000000-0000-0000-0000-000000000001',
                        'Миша', '9-11', '3-4', 'russian')
                ON CONFLICT (id) DO NOTHING
            """)
            # Upsert: delete old profile rows first, then insert fresh one
            cur.execute("""
                DELETE FROM child_profiles
                WHERE child_id = '00000000-0000-0000-0000-000000000001'
            """)
            cur.execute("""
                INSERT INTO child_profiles
                    (child_id, explanation_style, pace, autonomy_level, motivation, prefers_visual)
                VALUES ('00000000-0000-0000-0000-000000000001',
                        'very simple, step by step, with short examples',
                        'slow', 'medium', 'medium', true)
            """)
            for topic, status, score, notes in [
                ("geometry: points",     "knows",      80, None),
                ("geometry: lines/rays", "learning",   55, None),
                ("geometry: segments",   "struggling", 30,
                 "confuses ray and segment; mixes up figure and length"),
            ]:
                cur.execute("""
                    INSERT INTO child_knowledge
                        (child_id, subject, topic, status, score, notes)
                    VALUES ('00000000-0000-0000-0000-000000000001',
                            'math', %s, %s, %s, %s)
                    ON CONFLICT (child_id, subject, topic)
                    DO UPDATE SET status = EXCLUDED.status,
                                  score = EXCLUDED.score,
                                  notes = COALESCE(EXCLUDED.notes, child_knowledge.notes),
                                  updated_at = NOW()
                """, (topic, status, score, notes))
            # Interests
            for tag in ["конструкторы", "машинки", "логические задачи"]:
                cur.execute("""
                    INSERT INTO child_interests (child_id, tag)
                    VALUES ('00000000-0000-0000-0000-000000000001', %s)
                    ON CONFLICT (child_id, tag) DO NOTHING
                """, (tag,))
            # Error patterns
            for subj, topic, error_tag, cnt in [
                ("math", "geometry: segments", "ray_vs_segment", 3),
                ("math", "geometry: segments", "figure_vs_length", 2),
            ]:
                cur.execute("""
                    INSERT INTO error_patterns (child_id, subject, topic, error_tag, count)
                    VALUES ('00000000-0000-0000-0000-000000000001', %s, %s, %s, %s)
                    ON CONFLICT (child_id, subject, error_tag)
                    DO UPDATE SET count = EXCLUDED.count, last_seen = NOW()
                """, (subj, topic, error_tag, cnt))
        conn.commit()
    print("Test child inserted.")
