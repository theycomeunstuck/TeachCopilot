
# $env:TEACHCOPILOT_DEBUG_RAG="1"
# uv run uvicorn server:app --host 0.0.0.0 --port 8099

from typing import Any, Optional

import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.db import get_child_full_profile
from pipeline.rag import search_knowledge

import logging
import os

DEFAULT_CHILD_ID = "00000000-0000-0000-0000-000000000001"

LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b"

app = FastAPI(title="TeachCopilot RAG API")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("teachcopilot")

DEBUG_RAG = os.getenv("TEACHCOPILOT_DEBUG_RAG", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class RagRequest(BaseModel):
    query: str
    child_id: str = DEFAULT_CHILD_ID
    limit: int = 5


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/profile")
def profile(child_id: str = DEFAULT_CHILD_ID):
    profile_data = get_child_full_profile(child_id)
    return {
        "child_id": child_id,
        "profile": profile_data,
    }


@app.post("/rag/search")
def rag_search(request: RagRequest):
    results = search_knowledge(query=request.query)

    if request.limit and request.limit > 0:
        results = results[:request.limit]

    return {
        "query": request.query,
        "child_id": request.child_id,
        "count": len(results),
        "results": results,
    }


@app.get("/v1/models")
def openai_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "teachcopilot-rag",
                "object": "model",
                "owned_by": "local",
            }
        ],
    }


def _extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content", "")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(parts)

    return ""


def _build_rag_system_message(user_text: str) -> str:
    results = search_knowledge(query=user_text)

    if DEBUG_RAG:
        logger.info("=" * 80)
        logger.info("RAG QUERY: %s", user_text)
        logger.info("RAG RESULTS COUNT: %s", len(results))

        for i, item in enumerate(results[:5], start=1):
            content_preview = (
                item.get("content")
                or item.get("image_descriptions")
                or ""
            )[:300]

            logger.info(
                "RAG RESULT %s: topic=%r score=%s content_start=%r",
                i,
                item.get("topic"),
                item.get("score"),
                content_preview,
            )

    logger.info("=" * 80)

    for i, item in enumerate(results[:5], start=1):
        print(
            f"RAG RESULT {i}: topic={item.get('topic')!r}, "
            f"score={item.get('score')}, "
            f"content_start={(item.get('content') or item.get('image_descriptions') or '')[:200]!r}",
            flush=True,
        )

    print("=" * 80, flush=True)


    chunks = []
    for index, item in enumerate(results[:5], start=1):
        topic = item.get("topic") or "Без темы"
        content = item.get("content") or ""
        score = item.get("score")

        chunks.append(
            f"[Фрагмент {index}]\n"
            f"Тема: {topic}\n"
            f"Score: {score}\n"
            f"{content}"
        )

    if chunks:
        rag_context = "\n\n".join(chunks)
    else:
        rag_context = "По базе знаний ничего релевантного не найдено."

    return f"""
Ты — TeachCopilot, спокойный детский учебный помощник.

Сейчас всегда считается, что с тобой говорит ребёнок.
Объясняй мягко, короткими шагами, без давления.
Не ругай ребёнка за ошибки.
Если вопрос учебный, сначала объясни простыми словами, потом дай пример.

Используй материалы RAG ниже, если они подходят к вопросу.
Если материалы не подходят, не выдумывай, а отвечай обычным способом.

<RAG_CONTEXT>
{rag_context}
</RAG_CONTEXT>
""".strip()


@app.post("/v1/chat/completions")
def openai_chat_completions(payload: dict[str, Any]):
    messages = payload.get("messages", [])
    user_text = _extract_last_user_text(messages)

    rag_system_message = _build_rag_system_message(user_text)

    new_messages = [
        {
            "role": "system",
            "content": rag_system_message,
        }
    ]

    for message in messages:
        if message.get("role") == "system":
            continue
        new_messages.append(message)

    lmstudio_payload = dict(payload)
    lmstudio_payload["model"] = payload.get("model") or DEFAULT_MODEL
    lmstudio_payload["messages"] = new_messages

    # Для первого стабильного MVP отключаем stream.
    # Так проще проверить, что Open WebUI получает обычный JSON-ответ.
    lmstudio_payload["stream"] = False

    response = requests.post(
        f"{LMSTUDIO_BASE_URL}/chat/completions",
        json=lmstudio_payload,
        timeout=180,
    )

    data = response.json()

    # Некоторые локальные модели возвращают скрытые рассуждения.
    # В детский интерфейс их не отдаём.
    for choice in data.get("choices", []):
        message = choice.get("message") or {}
        message.pop("reasoning_content", None)

    return JSONResponse(
        status_code=response.status_code,
        content=data,
    )