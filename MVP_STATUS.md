# MVP_STATUS.md — TeachCopilot Pipeline (Phase 2)

## Что реализовано

| Файл | Назначение |
|---|---|
| `pipeline/__init__.py` | Package marker |
| `pipeline/config.py` | Все настройки из `.env` — единая точка конфигурации |
| `pipeline/db.py` | PostgreSQL: загрузка профиля ребёнка + тестовые данные (ON CONFLICT) |
| `pipeline/rag.py` | pgvector: эмбеддинг запроса + cosine similarity поиск |
| `pipeline/prompt_builder.py` | Загрузка промпта из файла + профиль в XML-тегах + RAG + speaker_type |
| `pipeline/filter_function.py` | Open WebUI Filter: inlet (персонализация) + outlet (обновление профиля) |
| `pipeline/profile_agent.py` | Динамическое обновление профиля через LLM-оценку каждые 5 сообщений |
| `pipeline/event_logger.py` | Логирование событий в PostgreSQL (таблица events) |
| `scripts/apply_schema.py` | Применение schema.sql через psycopg2 (кроссплатформенно) |
| `scripts/ingest.py` | Инжест документов: --mode text (дедуп) + --mode pdf (qwen3-vl) |
| `scripts/manage_profiles.py` | CLI: list/show/create/add-knowledge/update-knowledge/link-user/delete/events |
| `scripts/test_pipeline.py` | Интеграционный тест (23+ проверок) |
| `db/schema.sql` | Схема: children, profiles, knowledge, knowledge_base, user_mappings, events |
| `prompts/system_prompt.txt` | Педагогический промпт (коллега редактирует независимо) |
| `prompts/adult_prompt.txt` | Промпт для режима педагога/взрослого |
| `data/` | Учебные текстовые файлы (геометрия, 5 тем) |
| `data/images/` | Сохранённые изображения страниц PDF (для показа ребёнку) |
| `FILTER_INSTALL.md` | Инструкция по установке в Open WebUI |
| `MVP_STATUS.md` | Этот документ |

## Что умеет система

1. **Персонализация**: знает имя, возраст, класс, уровень, трудности ребёнка из БД → подставляет в промпт через XML-теги
2. **RAG**: ищет релевантный учебный материал по вопросу ребёнка (pgvector cosine similarity)
3. **Педагогический промпт**: 18000+ символов методики (от коллеги), загружается из `prompts/system_prompt.txt`
4. **Мультимодальный инжест**: PDF учебника → картинки страниц + описания (qwen3-vl через LM Studio) + эмбеддинги
5. **Динамическое обновление профиля**: каждые 5 сообщений LLM оценивает понимание → score обновляется (±10/+15, clamped 0–100)
6. **Детект спикера**: user_id → user_mappings → ребёнок/взрослый → разные режимы промпта
7. **Управление профилями**: CLI для создания/просмотра/обновления без UI
8. **Дедупликация**: повторный инжест не создаёт дубликатов (ON CONFLICT upsert)
9. **Логирование событий**: все обновления профиля записываются в таблицу events с payload

## Test results

```
============================================================
TeachCopilot Pipeline — Integration Test (Phase 2)
============================================================

--- Step 1: config ---
  [OK]   DATABASE_URL set
  [OK]   EMBEDDING_MODEL set
  [OK]   PROFILE_LLM_API_URL set
  [OK]   PROFILE_UPDATE_INTERVAL is int

--- Step 2: database ---
  [OK]   Profile loaded (Миша)
  [OK]   Has 3 knowledge records
  [OK]   Knowledge ON CONFLICT works (no duplicates)

--- Step 3: RAG search ---
  [OK]   RAG returns >=1 result
  [OK]   Top result relevant

--- Step 4: prompt builder ---
  [OK]   Prompt loaded from file (not stub)
  [OK]   Prompt contains <system_initialization>
  [OK]   Prompt contains <child_safe_profile>
  [OK]   Prompt mentions Миша
  [OK]   Prompt has Типичные_трудности section
  [OK]   Adult prompt different from child prompt

--- Step 5: filter function ---
  [OK]   filter_function imports OK
  [OK]   inlet hook works
  [OK]   outlet hook works

--- Step 6: dedup ---
  [OK]   Double ingest: row count unchanged

--- Step 7: user_mappings ---
  [OK]   Known user -> child mode
  [OK]   Unknown user -> adult mode

--- Step 8: profile agent ---
  [OK]   should_update(5) == True
  [OK]   should_update(3) == False

--- Step 9: multimodal ---
  [SKIP] No PDF pages ingested yet — run: python scripts/ingest.py --mode pdf ...
         These checks will pass after PDF ingestion.

============================================================
Results: 23/23 passed, 0/23 failed
ALL STEPS PASSED
============================================================
```

## How to run locally (Windows 11)

```powershell
# 1. Клонировать и войти в проект
cd C:\Users\name\PycharmProjects\MachineLearning

# 2. Создать и активировать виртуальное окружение
python -m venv .venv
.venv\Scripts\activate

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Создать .env файл
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/teachcopilot
# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
# RAG_TOP_K=3
# RAG_MIN_SCORE=0.3
# LOG_LEVEL=DEBUG
# DEFAULT_CHILD_ID=00000000-0000-0000-0000-000000000001
# PROFILE_LLM_API_URL=http://127.0.0.1:1234/v1
# PROFILE_LLM_MODEL=qwen/qwen3-vl-4b
# PROFILE_UPDATE_INTERVAL=5

# 5. Убедиться, что PostgreSQL запущен с pgvector
# CREATE EXTENSION IF NOT EXISTS vector;

# 6. Применить схему
python scripts/apply_schema.py

# 7. Вставить тестового ребёнка
python -c "from pipeline.db import insert_test_child; insert_test_child()"

# 8. Инжест учебных материалов (текст)
python scripts/ingest.py --dir data

# 9. Инжест PDF (опционально, требует LM Studio с qwen3-vl)
python scripts/ingest.py --mode pdf --file "data/3 класс 1 часть_Математика.pdf" --subject math --topic "математика 3 класс" --pages 1-50

# 10. Запустить тесты
python scripts/test_pipeline.py

# 11. Создать профиль и привязать пользователя
python scripts/manage_profiles.py create --name "Миша" --age-group "9-11" --grade "3-4"
python scripts/manage_profiles.py link-user <child_uuid> --user-id <open_webui_user_id>
```

## Server migration checklist

### Переменные окружения (.env)

| Переменная | Dev | Production | Комментарий |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost/teachcopilot` | `postgresql://<user>:<pass>@<host>/teachcopilot` | Перенести схему: `apply_schema.py` |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | `paraphrase-multilingual-MiniLM-L12-v2` | Лучше для русского; **пере-инжест обязателен** |
| `RAG_TOP_K` | `3` | `3` | — |
| `RAG_MIN_SCORE` | `0.3` | `0.5` | Повысить после смены модели |
| `LOG_LEVEL` | `DEBUG` | `INFO` | — |
| `DEFAULT_CHILD_ID` | тестовый UUID | убрать или задать реальный | — |
| `PROFILE_LLM_API_URL` | `http://127.0.0.1:1234/v1` | `http://127.0.0.1:1234/v1` | LM Studio на том же сервере |
| `PROFILE_LLM_MODEL` | `qwen/qwen3-vl-4b` | `qwen/qwen3-vl-4b` | Или другая модель в LM Studio |
| `PROFILE_UPDATE_INTERVAL` | `5` | `5` | Каждые N сообщений |

### Шаги миграции

1. **pgvector**: `CREATE EXTENSION IF NOT EXISTS vector;` на серверной БД
2. **Схема**: `python scripts/apply_schema.py`
3. **GPU для эмбеддингов**: в `pipeline/rag.py` изменить `SentenceTransformer(EMBEDDING_MODEL)` → `SentenceTransformer(EMBEDDING_MODEL, device='cuda')`
4. **Проверить PyTorch CUDA**: `python -c "import torch; print(torch.cuda.is_available())"`
5. **Инжест**: `python scripts/ingest.py --dir data` + PDF-инжест
6. **Тестовый ребёнок**: `python -c "from pipeline.db import insert_test_child; insert_test_child()"`
7. **Filter Function**: см. `FILTER_INSTALL.md` — вставить в Open WebUI
8. **Привязать пользователей**: `python scripts/manage_profiles.py link-user <child_id> --user-id <owui_id>`

## Conference demo script

### Подготовка
- LM Studio запущен с моделью qwen3-vl-4b
- Open WebUI запущен, Filter Function установлен
- Миша (тестовый ребёнок) есть в БД, привязан к user_id в Open WebUI
- Текстовые материалы проинжестены

### Демо (5 минут)

1. **Терминал**: `python scripts/manage_profiles.py show 00000000-0000-0000-0000-000000000001`
   → Показать: score отрезков = 30 (struggling)

2. **Open WebUI** (залогинен как Миша): «Привет!»
   → AI отвечает по имени, предлагает поработать с отрезками

3. **5 обменов** про отрезки — AI использует lesson_flow из педагогического промпта
   → AI задаёт проверочные вопросы, хвалит правильные ответы

4. **Терминал**: `python scripts/manage_profiles.py show 00000000-0000-0000-0000-000000000001`
   → Score изменился (~50), статус → learning

5. **Терминал**: `python scripts/manage_profiles.py events 00000000-0000-0000-0000-000000000001`
   → Показать событие profile_update с assessments

6. **Open WebUI** (незнакомый пользователь): «Как дела у Миши?»
   → AI переключается в режим педагога, даёт аналитику прогресса

### Ключевые моменты для презентации
- Система **знает** ребёнка (имя, уровень, трудности)
- Промпт **адаптируется** к профилю в реальном времени
- Score **обновляется** автоматически (proof of adaptivity)
- **Два режима**: ребёнок (педагогика) vs взрослый (аналитика)
- RAG подтягивает **релевантный учебный материал**

## Phase 3 — что дальше

- **CLIP**: поиск по фото тетрадки → найти страницу учебника (уже подготовлено в skills)
- **Orchestrator**: маршрутизация между агентами (Interaction, Profile, Task)
- **Task/Narrative Agent**: миссии, генерация заданий в контексте истории
- **Multiple subjects**: русский язык, английский
- **Group dynamics**: несколько учеников, командные миссии
- **Stable Diffusion**: генерация иллюстраций к заданиям
- **Attempt Analyzer**: отдельный агент для оценки ответов
- **UI для педагога**: веб-интерфейс вместо CLI (сейчас только `manage_profiles.py`)
- **Voice age detection**: определение возраста по голосу (сейчас по user_id)

## Known limitations / TODOs

- **RAG_MIN_SCORE = 0.3**: `all-MiniLM-L6-v2` даёт низкие score для русского текста. На продакшене рекомендуется `paraphrase-multilingual-MiniLM-L12-v2` + повысить порог до 0.5.
- **IVFFlat index**: `WITH (lists = 100)` — для маленьких датасетов (<100 строк) PostgreSQL может возвращать меньше результатов. Для MVP (5 текстов + PDF) это ок, но следить при росте.
- **Filter Function в Open WebUI**: при вставке в Open WebUI пакет `pipeline` должен быть доступен через PYTHONPATH. Если Open WebUI в отдельном окружении, нужна адаптация.
- **Profile Agent timeout**: qwen3-vl может отвечать медленно (30s timeout). На слабом железе увеличить `timeout` в `profile_agent.py`.
- **PDF ingest**: ~4-6 сек на страницу. 200 страниц ≈ 15-20 мин. Одноразовая операция, но можно разбить на батчи (`--pages 1-50`).
- **Multimodal тесты (Step 9)**: пропускаются если PDF не проинжестен. Запустить `ingest.py --mode pdf` для полного покрытия.
