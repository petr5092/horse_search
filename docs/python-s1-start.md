# Совместный локальный стенд Python S1 + Go

S1 даёт FastAPI auth, проекты/членство, каталог ImageVersion, шлюз разметки и
локальный seed трёх готовых PNG. Нормализация, worker, dataset/export pipeline
ещё не реализованы. Go запускается из проекта коллеги; здесь нет его исходников
или готовой команды запуска. Общие DTO — в [OpenAPI](contracts/annotation-service.openapi.json),
автономный сценарий Go — в [стартовом комплекте](go-service-start.md).

Все команды ниже выполняются из корня этого репозитория. Нужны Python 3.12+
и Docker Compose. FastAPI и Go работают на host, Compose запускает только
PostgreSQL и MinIO. Профиль полностью локальный, с примерными credentials.

## Подготовка Python и инфраструктуры

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
# При первом запуске создать отдельный профиль; существующий .env не менять:
cp .env.s1.example .env.s1
chmod 600 .env.s1
docker compose -f compose.s1.yaml up -d
docker compose -f compose.s1.yaml ps -a
docker compose -f compose.s1.yaml logs postgres minio-init
```

Дождитесь healthy у PostgreSQL и завершения `minio-init` с кодом 0.
`minio-init` создаёт приватный `horse-s1-fixtures` и очистку `staging/` через сутки.
Консоль MinIO — <http://127.0.0.1:19001>, логин `horse-s1-local`, пароль
`horse-s1-local-secret-change-me`. S3 endpoint — <http://127.0.0.1:19000>;
его hostname входит в подпись и должен быть доступен браузеру.

Compose использует project name `horse-s1`, отдельные volumes
`horse-s1_postgres-data` и `horse-s1_minio-data`. Порты привязаны к loopback:
15432 PostgreSQL, 19000 S3, 19001 MinIO console. Обычный `compose.yaml` не меняется.
Для PostgreSQL 18 volume подключён к `/var/lib/postgresql`, как требует
[официальный образ](https://hub.docker.com/_/postgres).

## Миграции, seed и FastAPI

```sh
HORSE_ENV_FILE=.env.s1 .venv/bin/alembic upgrade head
HORSE_ENV_FILE=.env.s1 .venv/bin/python -m scripts.seed_s1 --run
HORSE_ENV_FILE=.env.s1 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

`HORSE_ENV_FILE` выбирает файл настроек. Без него приложение читает `.env`.
`AUTO_CREATE_TABLES=false`, `ENFORCE_MIGRATIONS=true`: схема применяется отдельной
командой Alembic. `DATABASE_URL` — рабочая роль `platform_app`,
`MIGRATION_DATABASE_URL` — `platform_owner` исключительно для Alembic/bootstrap.
Выполняйте seed с рабочим URI из профиля.

Seed разрешён только с `--run`, `S1_DEV_MODE=true` и локальными БД/S3.
Он использует [синтетический manifest](contracts/fixtures/annotation-v0.1/manifest.json):
проект и ImageVersion UUID стабильны, пользователи имеют адреса `admin@s1.example`,
`manager@s1.example`, `annotator@s1.example`, `reviewer@s1.example` и общий пароль
из `S1_SEED_PASSWORD`. Seed печатает project ID и ImageVersion IDs.
В приватном бакете создаются отдельные source-объекты `originals/s1/` и
working-объекты `working/s1/`; это готовые синтетические PNG, без нормализации.
Повторный seed предназначен для этого же локального набора.

Swagger — <http://127.0.0.1:8000/docs>. Без Go продолжают работать auth, проекты и
каталог изображений; обращения к разметке отвечают 502 при недоступном upstream
или 504 при тайм-ауте. `GO_SERVICE_URL` в примере — <http://127.0.0.1:8081>.

## Подключение Go

Сначала примените Python migrations и seed, затем передайте коллеге локальные
URI. Это обычные PostgreSQL URI для Go-драйвера, без `+asyncpg`:

| Назначение | URI |
| --- | --- |
| Миграции Go | `postgresql://annotation_migrator:local-annotation-migrator-password@127.0.0.1:15432/horse_s1?search_path=annotation` |
| Go runtime | `postgresql://annotation_app:local-annotation-password@127.0.0.1:15432/horse_s1?search_path=annotation` |

Если драйвер не поддерживает query-параметр `search_path`, используйте URI без
него: bootstrap уже задаёт `search_path=annotation` обеим ролям в этой БД.
Коллега применяет собственные миграции от `annotation_migrator`, выдаёт
`annotation_app` точные table/sequence grants и запускает Go от рабочей роли
на `127.0.0.1:8081`. Команды и имена переменных задаёт реализация Go.
Служебный Bearer token должен совпадать с `GO_SERVICE_TOKEN` из `.env.s1`;
это отдельный секрет от `SECRET_KEY` платформы.

[Bootstrap](../dev/s1/postgres-init.sql) создаёт только роли и schema `annotation`,
владелец которой — `annotation_migrator`. Доменные таблицы создают миграции
каждого сервиса. Python работает в `public`, Go — в `annotation`; между схемами
нет FK или разрешений на данные. `platform_app` получает DML на будущие Python
таблицы и USAGE/SELECT/UPDATE sequences (UPDATE нужен seed для `setval`).
Go migrations отвечают за точные права на изменяемые таблицы и запреты изменения
истории/снимков; широких default DML grants для Go bootstrap не выдаёт.

Браузер обращается к FastAPI со своим JWT. Служебный token и actor-заголовки
формирует шлюз; браузеру их передавать не нужно. Python выдаёт приватные ссылки
на изображения разметки только после проверки доступа через Go.

## Platform smoke после запуска Go

Офлайн-проверка fixtures не обращается к сервисам:

```sh
.venv/bin/python scripts/platform_annotation_smoke.py --check-fixtures
```

Для одного независимого сценария в запущенном локальном стенде:

```sh
.venv/bin/python scripts/platform_annotation_smoke.py --run \
  --base-url http://127.0.0.1:8000 \
  --run-id 52994413-9a8d-4dc2-bbe6-75af6916eb10 \
  --state-dir .s1-smoke-runs
```

По умолчанию используется репозиторный manifest из `annotation-v0.1` и соседний
`create-task.json`; `--manifest` позволяет выбрать другой совместимый набор.
Клиент входит в FastAPI seed-пользователями и проходит сценарий через публичный
шлюз: создание задания, контекст, сохранение двух рамок и negative, submit,
review/accept и snapshot. Служебный Go token smoke-клиенту не нужен.
Если изменён seed-пароль, передайте его через окружение `S1_SEED_PASSWORD`:
клиент не читает `.env.s1` автоматически.

При неизвестном результате повторите команду с тем же run ID, журналом и
fixtures; не запускайте два клиента с одним ID одновременно. Для нового
сценария выберите новый UUID. Журнал `.s1-smoke-runs/` исключён из Git.
Без Go доступна только офлайн-проверка; полный smoke требует реализации сервиса
коллеги и не доказывается успешными unit-тестами Python.

## Проверки Python

Изолированные проверки без Go и без облачных ключей:

```sh
HORSE_ENV_FILE=/dev/null .venv/bin/python -m pytest -q
```

Для проверки блокировок нужны отдельные пустые тестовые базы PostgreSQL.
Их имена зафиксированы в защитных проверках тестов; основную `horse_s1` не используйте:

```sh
docker compose -f compose.s1.yaml exec postgres createdb -U platform_owner horse_s1_test_core
docker compose -f compose.s1.yaml exec postgres createdb -U platform_owner horse_s1_test_gateway
HORSE_ENV_FILE=/dev/null \
S1_TEST_CORE_DATABASE_URL=postgresql+asyncpg://platform_owner:local-owner-password@127.0.0.1:15432/horse_s1_test_core \
S1_TEST_GATEWAY_DATABASE_URL=postgresql+asyncpg://platform_owner:local-owner-password@127.0.0.1:15432/horse_s1_test_gateway \
.venv/bin/python -m pytest -q
```

Если базы уже существуют, повторять `createdb` не нужно. Эти opt-in тесты
сбрасывают свои тестовые таблицы/схему; они не предназначены для базы с нужными
данными. Без переменных `S1_TEST_*` проверки, требующие PostgreSQL, пропускаются.

При подготовке S1 8 ноября 2026 года прошли **127 тестов** на Python 3.9.6
с настоящим PostgreSQL 18.6. Дополнительно проверены миграции и разделение
рабочих ролей, повтор seed, вход через FastAPI и скачивание подписанного PNG
через HTTP-эмулятор S3 с проверкой исходного SHA-256. Ответы Go в этих проверках
задавались тестовым HTTP-транспортом; это не проверка настоящего Go-сервиса.

## Диагностика и остановка

```sh
docker compose -f compose.s1.yaml logs postgres minio-init
# Посмотреть роли и владельцев схем без локального psql:
docker compose -f compose.s1.yaml exec postgres psql -U platform_owner -d horse_s1 -c '\du'
docker compose -f compose.s1.yaml exec postgres psql -U platform_owner -d horse_s1 -c '\dn+'
# Остановка с сохранением локальных БД и S3:
docker compose -f compose.s1.yaml down
```

Bootstrap исполняется только на пустом PostgreSQL volume. Изменение SQL не
перенастраивает уже созданные роли. Для намеренного полного сброса **только S1**
остановите host-сервисы и выполните `docker compose -f compose.s1.yaml down -v`;
это удалит локальные данные PostgreSQL и MinIO данного стенда. После сброса
повторите bootstrap/migrations/seed и начните smoke с новым run ID/журналом.

В окружении подготовки файлов Docker отсутствовал: `compose up` и взаимодействие
с реальным контейнером MinIO не проверены. Проверки PostgreSQL выполнялись на
временном сервере вне Docker, S3 — на HTTP-эмуляторе. Эти проверки и статическая
проверка конфигурации не заменяют запуск Compose и smoke с реализацией Go.
