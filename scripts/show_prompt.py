# scripts/show_prompt.py
"""Show the full system prompt that would go to LLM for a given child + query.
Usage: python scripts/show_prompt.py "сколько будет 5 + 3"
       python scripts/show_prompt.py "что такое отрезок" --child-id UUID
"""
import sys
import io
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pipeline.config import DEFAULT_CHILD_ID
from pipeline.db import get_child_full_profile
from pipeline.rag import search_knowledge
from pipeline.prompt_builder import build_system_prompt, BASE_SYSTEM_PROMPT


def main():
    parser = argparse.ArgumentParser(description="Show assembled system prompt")
    parser.add_argument("query", help="User query (what the child asks)")
    parser.add_argument("--child-id", default=DEFAULT_CHILD_ID,
                        help="Child UUID (default from .env)")
    args = parser.parse_args()

    child_id = args.child_id
    query = args.query

    print("=" * 70)
    print(f"Query:    {query}")
    print(f"Child ID: {child_id}")
    print("=" * 70)

    # Load profile
    profile = get_child_full_profile(child_id)
    if profile:
        print(f"Profile:  {profile['name']} (class {profile.get('grade', '?')})")
    else:
        print("Profile:  NOT FOUND (prompt without personalization)")

    # RAG search
    rag = search_knowledge(query) if query else []
    print(f"RAG hits: {len(rag)}")
    for r in rag:
        print(f"  - {r['topic']} (score={r['score']:.3f})")

    # Build prompt
    prompt = build_system_prompt(BASE_SYSTEM_PROMPT, profile, rag,
                                 speaker_type="child")

    print("=" * 70)
    print("FULL SYSTEM PROMPT BELOW")
    print("=" * 70)
    print(prompt)
    print("=" * 70)
    print(f"Prompt length: {len(prompt)} chars")
    print("=" * 70)


if __name__ == "__main__":
    main()
