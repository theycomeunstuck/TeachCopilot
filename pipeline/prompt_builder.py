# pipeline/prompt_builder.py
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Pedagogical prompt loaded from file — colleague edits this independently
_PROMPT_DIR = Path(__file__).parent.parent / "prompts"
_CHILD_PROMPT_FILE = _PROMPT_DIR / "system_prompt.txt"
_ADULT_PROMPT_FILE = _PROMPT_DIR / "adult_prompt.txt"


def _load_prompt(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s — using fallback", path)
        return fallback


BASE_SYSTEM_PROMPT = _load_prompt(_CHILD_PROMPT_FILE, "You are a friendly AI tutor.")
ADULT_SYSTEM_PROMPT = _load_prompt(_ADULT_PROMPT_FILE, """Вы — TeachCopilot в режиме педагога.
Текущий пользователь — взрослый (учитель или родитель).
Предоставляйте аналитику, прогресс, рекомендации. Отвечайте на русском.""")


def build_system_prompt(base: str, profile: dict | None, rag: list[dict],
                        speaker_type: str = "child") -> str:
    # SPEAKER_DETECT: To re-enable adult mode, restore this block:
    #   if speaker_type == "adult":
    #       return _build_adult_prompt(profile, rag)
    # Currently always builds child prompt regardless of speaker_type.

    if not profile:
        logger.warning("No profile — base prompt only")
        return base

    parts = [base.strip()]

    # --- Child profile section (generated from DB, not from file) ---
    parts.append("\n<child_safe_profile>")
    parts.append(f"Имя_для_обращения: {profile.get('name', 'ученик')}")
    parts.append(f"Возрастная_группа: {profile.get('age_group', '?')}")
    interests = profile.get('interests', '')
    if interests:
        parts.append(f"Интересы: {interests}")
    parts.append("</child_safe_profile>")

    # --- Learning profile section ---
    parts.append("\n<child_learning_profile>")
    parts.append(f"Школьный_уровень: {profile.get('grade', '?')} класс")
    parts.append(f"Язык: {profile.get('language', 'русский')}")
    parts.append(f"Предпочтительный_стиль_объяснения: {profile.get('explanation_style', 'просто')}")
    parts.append(f"Темп: {profile.get('pace', 'средний')}")
    parts.append(f"Уровень_самостоятельности: {profile.get('autonomy_level', 'средний')}")
    parts.append(f"Мотивация: {profile.get('motivation', 'средняя')}")
    parts.append(f"Предпочитает_визуальное: {profile.get('prefers_visual', False)}")

    # Speed
    speed = profile.get("avg_response_time_sec")
    if speed:
        parts.append(f"Средняя_скорость_ответа: {speed:.0f} сек")

    # Interests
    interests_list = profile.get("interests") or []
    if interests_list:
        parts.append(f"\nИнтересы: {', '.join(interests_list)}")

    knowledge = profile.get("knowledge") or []
    knows = [k for k in knowledge if k.get("status") == "knows"]
    learning = [k for k in knowledge if k.get("status") == "learning"]
    struggling = [k for k in knowledge if k.get("status") == "struggling"]

    if knows:
        parts.append("\nЧто_уже_знает:")
        for k in knows:
            parts.append(f"- {k.get('topic')} (score: {k.get('score', '?')}/100)")

    if learning:
        parts.append("\nЧто_изучает_сейчас:")
        for k in learning:
            parts.append(f"- {k.get('topic')} (score: {k.get('score', '?')}/100)")

    if struggling:
        parts.append("\nТипичные_трудности:")
        for k in struggling:
            line = f"- {k.get('topic')} (score: {k.get('score', '?')}/100)"
            if k.get("notes"):
                line += f" — {k['notes']}"
            parts.append(line)

    parts.append("</child_learning_profile>")

    # --- Error patterns section ---
    error_patterns = profile.get("error_patterns") or []
    if error_patterns:
        sorted_errors = sorted(error_patterns, key=lambda e: e.get("count", 0), reverse=True)
        parts.append("\n<error_patterns>")
        parts.append("Типичные_ошибки:")
        for ep in sorted_errors[:10]:
            parts.append(f"- {ep.get('error_tag')} (предмет: {ep.get('subject', '?')}, "
                        f"тема: {ep.get('topic', '?')}, повторений: {ep.get('count', 1)})")
        parts.append("</error_patterns>")

    # --- RAG content ---
    if rag:
        parts.append("\n<relevant_material>")
        for r in rag:
            parts.append(f"## {r.get('topic', '')}")
            parts.append(r.get("content", ""))
            if r.get("image_descriptions"):
                parts.append(f"[Визуальный контекст: {r['image_descriptions']}]")
        parts.append("</relevant_material>")

    result = "\n".join(parts)
    logger.debug("Prompt built: %d chars, speaker=%s", len(result), speaker_type)
    return result


def _build_adult_prompt(profile: dict | None, rag: list[dict]) -> str:
    """Teacher-facing prompt with analytics and recommendations."""
    parts = [ADULT_SYSTEM_PROMPT.strip()]
    if profile:
        parts.append(f"\n## Ученик: {profile.get('name', '?')}")
        for k in (profile.get("knowledge") or []):
            parts.append(f"- {k.get('topic')}: {k.get('status')} ({k.get('score')}/100)"
                        + (f" — {k['notes']}" if k.get('notes') else ""))
    parts.append("\nПредоставьте анализ прогресса и рекомендации по следующим шагам.")
    return "\n".join(parts)
