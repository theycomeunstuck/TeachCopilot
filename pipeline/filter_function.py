# pipeline/filter_function.py
"""
TWO USES:
1. Kept in repo as source of truth.
2. The Filter CLASS is pasted into Open WebUI -> Workspace -> Functions -> New Function.
   Type = Filter. Paste only the class + its imports, not this docstring.

child_id resolution:
1. Message prefix "[child_id:UUID] question" (explicit, for testing)
2. user_mappings table: Open WebUI user_id -> child_id (production)
3. DEFAULT_CHILD_ID from env (fallback)

Speaker detection: DISABLED (Phase 2).
Always child mode — any user gets the child prompt and profile updates.
To re-enable: see _resolve_child_id() and outlet() comments marked # SPEAKER_DETECT.
"""
import os, re, logging
from pipeline.db import get_child_full_profile, get_connection
from pipeline.rag import search_knowledge
from pipeline.prompt_builder import build_system_prompt, BASE_SYSTEM_PROMPT
from pipeline.profile_agent import should_update, assess_interaction, update_profile
from pipeline.event_logger import log_event
from pipeline.config import PROFILE_UPDATE_INTERVAL

logger = logging.getLogger(__name__)
_CHILD_RE = re.compile(r"^\[child_id:([^\]]+)\]\s*", re.IGNORECASE)


def _resolve_child_id(user_id: str | None) -> tuple[str, str]:
    """Returns (child_id, speaker_type). Always 'child' mode (Phase 2).

    # SPEAKER_DETECT: To re-enable adult/child detection, restore the
    # user_mappings lookup below. When user_id is NOT in user_mappings,
    # return ("...", "adult") instead of ("...", "child").
    # See git log for the original version.
    """
    # Try user_mappings to find which child profile to load
    if user_id:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT child_id FROM user_mappings WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    if row:
                        return str(row["child_id"]), "child"
        except Exception:
            logger.exception("user_mappings lookup failed")

    # Fallback: default child — still child mode (not adult)
    return os.getenv("DEFAULT_CHILD_ID", ""), "child"


class Filter:
    def inlet(self, body: dict, user: dict | None = None) -> dict:
        try:
            messages = body.get("messages", [])
            if not messages:
                return body

            last_user = next(
                (m for m in reversed(messages) if m.get("role") == "user"), None
            )

            # Try explicit prefix first
            child_id = ""
            speaker_type = "child"
            if last_user:
                content = last_user.get("content", "")
                m = _CHILD_RE.match(content)
                if m:
                    child_id = m.group(1)
                    last_user["content"] = content[m.end():]

            # Fallback: resolve via user_mappings
            if not child_id:
                owui_user_id = (user or {}).get("id", "")
                child_id, speaker_type = _resolve_child_id(owui_user_id)

            query   = (last_user or {}).get("content", "")
            profile = get_child_full_profile(child_id) if child_id else None
            rag     = search_knowledge(query) if query else []
            system  = build_system_prompt(BASE_SYSTEM_PROMPT, profile, rag,
                                          speaker_type=speaker_type)

            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = system
            else:
                messages.insert(0, {"role": "system", "content": system})

            body["messages"] = messages
            logger.info("Filter OK: child_id=%s speaker=%s rag=%d",
                       child_id, speaker_type, len(rag))
        except Exception:
            logger.exception("Filter.inlet failed — passing unchanged")
        return body

    def outlet(self, body: dict, user: dict | None = None) -> dict:
        """Called after LLM response. Updates profile if enough messages."""
        try:
            messages = body.get("messages", [])
            user_msg_count = sum(1 for m in messages if m.get("role") == "user")

            if not should_update(user_msg_count):
                return body

            # Resolve child_id
            owui_user_id = (user or {}).get("id", "")
            child_id, speaker_type = _resolve_child_id(owui_user_id)

            if not child_id:
                return body  # no profile to update

            # SPEAKER_DETECT: To re-enable, add check:
            #   if not child_id or speaker_type != "child": return body

            assessments = assess_interaction(child_id, messages)
            if assessments:
                update_profile(child_id, assessments)
                log_event(child_id, "profile_update", {
                    "trigger": f"auto_{PROFILE_UPDATE_INTERVAL}msg",
                    "assessments": assessments,
                    "message_count": user_msg_count
                })
                logger.info("Profile updated: child=%s topics=%d",
                           child_id, len(assessments))
        except Exception:
            logger.exception("outlet profile update failed — continuing")
        return body
