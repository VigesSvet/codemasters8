# shared/testdata — только для тестов

Файлы этого каталога приложение не засевает, не отдаёт через API и не показывает во фронтенде. В Docker-образ API они
не попадают (`.dockerignore`). Их читают только тесты бэкенда и фронтенда:

| Файл | Что это | Кто читает |
|---|---|---|
| `demo_seed.json` | прежний демо-набор: 14 вымышленных ЖК, 7 застройщиков, 12 публикаций, 5 обращений, 3 аналитика и исходный журнал | `backend/tests/demo_seed.py` (пустая БД тестов), `frontend/src/lib/risk.test.ts`, `frontend/src/lib/find.test.ts`, `backend/app/golden.py` |
| `risk_fixtures.json` | синтетические случаи модели риска (границы зон, факторы, «риск не оценивается») | `backend/tests/test_risk.py`, `frontend/src/lib/risk.test.ts`, `backend/app/golden.py` |
| `risk_golden.json` | эталон Python-движка риска по `demo_seed.json` и `risk_fixtures.json` — с ним сверяется TS-порт | `backend/tests/test_risk.py`, `frontend/src/lib/risk.test.ts` |

Эталон пересчитывается так: `cd backend && .venv/bin/python -m app.golden`.

Стартовые данные приложения — `shared/snapshot/` (реальные данные мониторинга, см. README «Развёртывание со стартовым
набором данных»).
