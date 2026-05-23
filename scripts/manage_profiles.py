# scripts/manage_profiles.py
"""CLI для управления профилями детей в TeachCopilot."""
import sys
import argparse
import io
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pipeline.db import get_connection, get_child_full_profile
from pipeline.config import DATABASE_URL


def cmd_list(args):
    """Список всех профилей."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, age_group, grade FROM children ORDER BY name")
            rows = cur.fetchall()
    if not rows:
        print("Нет профилей.")
        return
    print(f"Найдено профилей: {len(rows)}")
    for r in rows:
        print(f"  {r['id']}  {r['name']}  (класс {r['grade']}, возраст {r['age_group']})")


def cmd_show(args):
    """Полный профиль ребёнка."""
    profile = get_child_full_profile(args.child_id)
    if not profile:
        print(f"Профиль не найден: {args.child_id}")
        return

    print(f"=== Профиль: {profile['name']} ===")
    print(f"ID:           {profile['id']}")
    print(f"Возраст:      {profile.get('age_group', '?')}")
    print(f"Класс:        {profile.get('grade', '?')}")
    print(f"Язык:         {profile.get('language', '?')}")
    print(f"Стиль:        {profile.get('explanation_style', '?')}")
    print(f"Темп:         {profile.get('pace', '?')}")
    print(f"Самост.:      {profile.get('autonomy_level', '?')}")
    print(f"Мотивация:    {profile.get('motivation', '?')}")
    print(f"Визуальное:   {profile.get('prefers_visual', '?')}")

    speed = profile.get("avg_response_time_sec")
    if speed:
        print(f"Ср. скорость: {speed:.0f} сек")

    # Interests
    interests = profile.get("interests") or []
    if interests:
        print(f"\nИнтересы: {', '.join(interests)}")
    else:
        print("\nИнтересы: нет")

    knowledge = profile.get("knowledge") or []
    if knowledge:
        print(f"\nЗнания ({len(knowledge)} тем):")
        for k in knowledge:
            icon = {"knows": "✓", "learning": "→", "struggling": "⚠"}.get(k.get("status", ""), "·")
            line = f"  {icon} {k.get('subject')}/{k.get('topic')} — {k.get('status')} ({k.get('score')}/100)"
            if k.get("notes"):
                line += f"\n      Заметки: {k['notes']}"
            print(line)
    else:
        print("\nЗнания: нет записей")

    # Error patterns
    errors = profile.get("error_patterns") or []
    if errors:
        sorted_errors = sorted(errors, key=lambda e: e.get("count", 0), reverse=True)
        print(f"\nТипичные ошибки ({len(errors)}):")
        for ep in sorted_errors:
            print(f"  - {ep.get('error_tag')} ({ep.get('subject')}/{ep.get('topic')}) "
                  f"x{ep.get('count', 1)}")
    else:
        print("\nТипичные ошибки: нет")

    # Events
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT event_type, payload, created_at
                    FROM events WHERE child_id = %s
                    ORDER BY created_at DESC LIMIT 5
                """, (args.child_id,))
                events = cur.fetchall()
        if events:
            print(f"\nПоследние события ({len(events)}):")
            for e in events:
                print(f"  [{e['created_at']}] {e['event_type']}")
    except Exception:
        pass


def cmd_create(args):
    """Создать новый профиль."""
    child_id = str(uuid.uuid4())
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO children (id, name, age_group, grade, language)
                VALUES (%s, %s, %s, %s, %s)
            """, (child_id, args.name, args.age_group, args.grade, args.language or 'russian'))
            cur.execute("""
                INSERT INTO child_profiles
                    (child_id, explanation_style, pace, autonomy_level, motivation, prefers_visual)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (child_id,
                  args.style or 'simple, step by step',
                  args.pace or 'medium',
                  args.autonomy or 'medium',
                  args.motivation or 'medium',
                  args.visual or False))
        conn.commit()
    print(f"Создан профиль: {args.name} ({child_id})")


def cmd_add_knowledge(args):
    """Добавить или обновить знание."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO child_knowledge (child_id, subject, topic, status, score, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (child_id, subject, topic)
                DO UPDATE SET status = EXCLUDED.status,
                              score = EXCLUDED.score,
                              notes = COALESCE(EXCLUDED.notes, child_knowledge.notes),
                              updated_at = NOW()
            """, (args.child_id, args.subject, args.topic, args.status, args.score, args.notes))
        conn.commit()
    print(f"Знание обновлено: {args.topic} → {args.status} ({args.score}/100)")


def cmd_update_knowledge(args):
    """Обновить score/status существующего знания."""
    sets = []
    params = []
    if args.score is not None:
        sets.append("score = %s")
        params.append(args.score)
    if args.status:
        sets.append("status = %s")
        params.append(args.status)
    if args.notes:
        sets.append("notes = %s")
        params.append(args.notes)
    sets.append("updated_at = NOW()")

    if not sets:
        print("Нечего обновлять — укажите --score, --status, или --notes")
        return

    params.extend([args.child_id, args.topic])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE child_knowledge SET {', '.join(sets)}
                WHERE child_id = %s AND topic = %s
            """, params)
            if cur.rowcount == 0:
                print(f"Не найдено: {args.topic}")
                return
        conn.commit()
    print(f"Обновлено: {args.topic}")


def cmd_link_user(args):
    """Привязать Open WebUI user_id к ребёнку."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_mappings (user_id, child_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET child_id = EXCLUDED.child_id
            """, (args.user_id, args.child_id))
        conn.commit()
    print(f"Привязка: user '{args.user_id}' → ребёнок {args.child_id}")


def cmd_add_interest(args):
    """Добавить интерес ребёнку."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO child_interests (child_id, tag)
                VALUES (%s, %s)
                ON CONFLICT (child_id, tag) DO NOTHING
            """, (args.child_id, args.tag))
        conn.commit()
    print(f"Интерес добавлен: {args.tag}")


def cmd_remove_interest(args):
    """Удалить интерес ребёнка."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM child_interests
                WHERE child_id = %s AND tag = %s
            """, (args.child_id, args.tag))
            if cur.rowcount == 0:
                print(f"Не найдено: {args.tag}")
                return
        conn.commit()
    print(f"Интерес удалён: {args.tag}")


def cmd_delete(args):
    """Удалить профиль (с подтверждением)."""
    profile = get_child_full_profile(args.child_id)
    if not profile:
        print(f"Профиль не найден: {args.child_id}")
        return

    if not args.yes:
        answer = input(f"Удалить профиль {profile['name']}? (да/нет): ")
        if answer.strip().lower() not in ("да", "yes", "y"):
            print("Отменено.")
            return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM children WHERE id = %s", (args.child_id,))
        conn.commit()
    print(f"Удалён: {profile['name']} ({args.child_id})")


def cmd_events(args):
    """Показать события ребёнка."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_type, payload, created_at
                FROM events WHERE child_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (args.child_id, args.last))
            events = cur.fetchall()
    if not events:
        print("Нет событий.")
        return
    print(f"Последние {len(events)} событий:")
    for e in events:
        print(f"  [{e['created_at']}] {e['event_type']}")
        if e.get('payload'):
            import json
            payload = e['payload'] if isinstance(e['payload'], dict) else json.loads(e['payload'])
            for k, v in payload.items():
                print(f"    {k}: {v}")


def main():
    parser = argparse.ArgumentParser(
        description="TeachCopilot — управление профилями детей"
    )
    sub = parser.add_subparsers(dest="command")

    # list
    sub.add_parser("list", help="Список всех профилей")

    # show
    p = sub.add_parser("show", help="Полный профиль ребёнка")
    p.add_argument("child_id", help="UUID ребёнка")

    # create
    p = sub.add_parser("create", help="Создать профиль")
    p.add_argument("--name", required=True, help="Имя ребёнка")
    p.add_argument("--age-group", required=True, help="Возрастная группа (напр. 9-11)")
    p.add_argument("--grade", required=True, help="Класс (напр. 3-4)")
    p.add_argument("--language", default="russian", help="Язык (default: russian)")
    p.add_argument("--style", help="Стиль объяснения")
    p.add_argument("--pace", help="Темп")
    p.add_argument("--autonomy", help="Самостоятельность")
    p.add_argument("--motivation", help="Мотивация")
    p.add_argument("--visual", action="store_true", help="Предпочитает визуальное")

    # add-knowledge
    p = sub.add_parser("add-knowledge", help="Добавить знание")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--subject", required=True, help="Предмет")
    p.add_argument("--topic", required=True, help="Тема")
    p.add_argument("--status", required=True, choices=["knows", "learning", "struggling"])
    p.add_argument("--score", type=int, required=True, help="Оценка 0-100")
    p.add_argument("--notes", help="Заметки")

    # update-knowledge
    p = sub.add_parser("update-knowledge", help="Обновить знание")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--topic", required=True, help="Тема")
    p.add_argument("--score", type=int, help="Новая оценка")
    p.add_argument("--status", choices=["knows", "learning", "struggling"])
    p.add_argument("--notes", help="Заметки")

    # link-user
    p = sub.add_parser("link-user", help="Привязать Open WebUI user_id")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--user-id", required=True, help="Open WebUI user_id")

    # add-interest
    p = sub.add_parser("add-interest", help="Добавить интерес")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--tag", required=True, help="Тег интереса (напр. космос)")

    # remove-interest
    p = sub.add_parser("remove-interest", help="Удалить интерес")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--tag", required=True, help="Тег интереса")

    # delete
    p = sub.add_parser("delete", help="Удалить профиль")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--yes", "-y", action="store_true", help="Без подтверждения")

    # events
    p = sub.add_parser("events", help="Показать события")
    p.add_argument("child_id", help="UUID ребёнка")
    p.add_argument("--last", type=int, default=10, help="Количество (default: 10)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "list": cmd_list,
        "show": cmd_show,
        "create": cmd_create,
        "add-knowledge": cmd_add_knowledge,
        "update-knowledge": cmd_update_knowledge,
        "link-user": cmd_link_user,
        "add-interest": cmd_add_interest,
        "remove-interest": cmd_remove_interest,
        "delete": cmd_delete,
        "events": cmd_events,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
