# Horse Dataset Studio API

FastAPI backend с JWT-аутентификацией и приватными оригиналами в S3-совместимом хранилище.

## Локальный запуск

Рекомендуется Python 3.12+ и Docker Compose для MinIO.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.minio.example .env
docker compose up -d
# Дождаться успешного завершения minio-init:
docker compose logs minio-init
alembic upgrade head
uvicorn app.main:app --reload
```

API: <http://localhost:8000/docs>. MinIO: <http://localhost:9001>, логин
`horse-local`, пароль `horse-local-secret-change-me` (только локальная разработка).
S3 endpoint <http://localhost:9000> должен быть доступен и API, и браузеру:
адрес входит в подпись, переписывать hostname после генерации нельзя.
Compose публикует MinIO только на localhost и хранит данные в named volume.
Закреплённые образы взяты из официальных релизов [MinIO](https://github.com/minio/minio/releases/tag/RELEASE.2025-04-22T22-12-26Z)
и [mc](https://github.com/minio/mc/releases/tag/RELEASE.2025-04-16T18-13-26Z).

Существующее приложение создаёт таблицы на старте через `create_all`; для управляемых
изменений схемы запускайте Alembic до API. Если база уже создана предыдущим
приложением и содержит только таблицу `users`, сначала сделайте резервную копию,
затем `alembic stamp 7b655d5c4ae0` и `alembic upgrade head`.
Не запускайте `verify_auth.py` на рабочей базе: этот старый скрипт удаляет SQLite-файл.

## Настройка S3

Основной профиль `.env.example` настроен на Yandex Object Storage:
`https://storage.yandexcloud.net`, регион `ru-central1`, бакет `horse-dataset-vim`
([консоль](https://console.yandex.cloud/folders/b1gt0ga0pl88tm6utarv/storage/buckets/horse-dataset-vim)).
Для облачного запуска скопируйте `.env.example` в `.env` вместо MinIO-профиля,
задайте оба поля `S3_ACCESS_KEY_ID` и `S3_SECRET_ACCESS_KEY` статического ключа
сервисного аккаунта, замените `SECRET_KEY` случайным JWT-секретом и ограничьте
права файла: `chmod 600 .env`. Ключи не добавляйте в Git и не передавайте браузеру.
IAM/OAuth token не является секретом статического S3-ключа.
Для сервиса достаточно сервисного аккаунта с `storage.editor` на этом бакете;
права на весь каталог не требуются.
При пустом или неполном ключе файловые методы отвечают 503, auth остаётся доступным.
Приложение использует только явные `S3_*` credentials и не обращается к AWS metadata,
профилям или `AWS_*` переменным. Для MinIO используйте `.env.minio.example`.

Клиент использует Signature V4 и `request_checksum_calculation=when_required`,
`response_checksum_validation=when_required` для совместимости S3-провайдера;
проверка фактического SHA-256 остаётся в complete. Требуется boto3 >=1.36.0.
Поддержка подтверждена официальными документами:
[boto3](https://yandex.cloud/en/docs/storage/tools/boto),
[POST policy и SigV4](https://yandex.cloud/en/docs/storage/concepts/presigned-post-forms),
[CopyObject и If-Match](https://yandex.cloud/en/docs/storage/s3/api-ref/object/copy),
[GetObject](https://yandex.cloud/en/docs/storage/s3/api-ref/object/get).

Для облака используйте отдельные сервисные права на нужный бакет/префиксы:
GetObject, PutObject и DeleteObject для `staging/*` и `originals/*` (CopyObject
использует GetObject источника и PutObject назначения). Не делайте бакет публичным;
для AWS включите Block Public Access. Приложение не создаёт бакет и не меняет ACL.
Compose создаёт приватный бакет и lifecycle удаления `staging/` через сутки.
Для другого провайдера настройте такую же lifecycle policy: незавершённые или
повторённые POST-загрузки могут оставлять временные объекты после удаления записи.
Если включено версионирование, отдельно настройте очистку неактуальных версий.

По умолчанию лимит файла — 100 MiB, POST живёт 15 минут, ссылка скачивания — 5 минут.
Максимальный настраиваемый лимит — 5 GiB (один CopyObject); multipart upload нет.
Ссылки и POST-поля дают временный доступ: не публикуйте их в логах и переписке.
Уже выданную ссылку нельзя отозвать отдельно; удаление объекта прекращает скачивание.
Замените JWT-секрет и локальные credentials перед любым внешним развёртыванием.

## Проверка облачного подключения

До загрузки оригиналов отключите анонимное чтение объектов, просмотр списка
и чтение настроек бакета в консоли YC. Проверьте также ACL и bucket policy.
Приложение не меняет существующие настройки доступа.

```sh
# Только чтение текущих ACL/CORS/lifecycle и план дополнений:
python -m scripts.s3_inspect
# Явное разрешение на маленькую тестовую загрузку (64 байта) и её удаление:
python -m scripts.s3_smoke --run
```

Smoke не использует рабочую БД и удаляет только два собственных случайных ключа
`staging/smoke/<uuid>` и `originals/smoke/<uuid>`, в том числе при ошибке.
Он проверяет настоящую multipart POST, условное копирование, SHA-256,
подписанную GET-ссылку и отказ анонимного GET (403). Подписи, ключи и URL не печатаются.
Нужны также права чтения настроек versioning; smoke отказывается от записей в
бакет с включённым или приостановленным версионированием, чтобы не оставлять версии.
Не меняйте versioning во время проверки. Без `--run` скрипт ничего не отправляет.
Если очистка сообщила ошибку, проверьте префиксы smoke и удалите тестовые объекты
с правами оператора. Для неизвестного результата сети это остаётся необходимой
операционной проверкой.

Inspector не имеет apply-режима и не перезаписывает существующие настройки.
В консоли добавьте CORS для точного origin фронтенда с POST/GET/HEAD (при необходимости
разрешите заголовки `*`) и lifecycle для `staging/` с Expiration Days=1,
сохраняя остальные правила. Для API из Swagger/HTTP smoke CORS не требуется;
он нужен браузерному фронтенду. Включение lifecycle и анонимные настройки
проверяются отдельно от успешного smoke.

### Проверенное подключение — 2026-11-05

В этом рабочем окружении `.env` уже настроен для `horse-dataset-vim` в Yandex
Object Storage и защищён правами `0600`; ключи в Git не входят. Используется
сервисный аккаунт `horse-storage-bot` с доступом к этому бакету.

На реальном бакете прошли smoke-проверка и полный API-сценарий с отдельной
временной SQLite: регистрация/вход, presigned POST, завершение и повторное
завершение, список, скачивание с проверкой байтов и удаление. Анонимный GET
оригинала возвращает 403; тестовые объекты удалены.

Настройки бакета записаны и проверены чтением:

- Lifecycle `horse-staging-expiration`: префикс `staging/`, удаление через 1 день;
  остальные правила сохранены.
- CORS `horse-local-api`: origins `http://localhost:8000` и `http://127.0.0.1:8000`,
  методы POST/GET/HEAD, разрешённые заголовки `*`, exposed header `ETag`, max age 3600.
  Реальный OPTIONS preflight для POST с `http://localhost:8000` вернул 200 и ожидаемый origin.

Для фронтенда на другом адресе добавьте его точный origin явно, сохраняя
существующие CORS-правила. Это результаты проверки на указанную дату;
после смены прав, ключей или настроек повторите проверки.

## API файлов

Все методы требуют текущего активного пользователя (Bearer JWT или cookie).
Файл доступен владельцу и администратору, чужой UUID возвращает 404.
`GET /files` показывает только собственные файлы, включая pending, с `offset` и `limit` (1–100).

1. `POST /files/uploads`: `{"original_name":"horse.jpg","size":12345,"content_type":"image/jpeg","sha256":"..."}`.
   `sha256` необязателен; если задан, это 64 hex-символа. Ответ 201 содержит `file.id`,
   `url`, `fields`, `expires_in`. Размер положительный, не превышает лимит.
2. Отправьте файл **на URL S3** через multipart/form-data: сначала все полученные
   `fields` без изменений, последним поле `file`. Не задавайте вручную HTTP
   Content-Type запроса — браузер сам добавляет boundary. POST policy ограничивает
   точный object key, MIME и размер файла. Результат S3 — HTTP 204.
3. `POST /files/{id}/complete`: API проверяет размер и заявленный MIME в S3,
   условно копирует staging-объект в закрытый ключ оригинала по ETag источника,
   читает скопированные байты потоком и сохраняет вычисленный SHA-256. Если клиент
   передал ожидаемый SHA-256, значения должны совпасть. Готовый файл имеет `status=ready`.
   Повторный complete готового файла возвращает тот же результат. Повтор POST в
   staging не меняет принятый оригинал.
4. `GET /files/{id}/download`: временная GET-ссылка с attachment disposition;
   `DELETE /files/{id}`: удаление объектов и записи, ответ 204.

Пример загрузки из браузера (upload — JSON ответа первого метода):

```js
const form = new FormData();
for (const [key, value] of Object.entries(upload.fields)) form.append(key, value);
form.append('file', selectedFile);
const result = await fetch(upload.url, { method: 'POST', body: form });
if (!result.ok) throw new Error(`S3 upload failed: ${result.status}`);
await fetch(`/files/${upload.file.id}/complete`, {
  method: 'POST', headers: { Authorization: `Bearer ${token}` }
});
```

При размещении фронтенда на другом origin настройте CORS бакета у S3-провайдера
для точного origin фронтенда и POST/GET; CORS API и CORS S3 настраиваются отдельно.
Для MinIO задайте origin через `MINIO_API_CORS_ALLOW_ORIGIN`, если это требуется
для вашего фронтенда. В MVP фронтенд не добавлен.

Ошибки: 401 — нет авторизации; 404 — нет файла/прав; 409 — файл не загружен,
не совпали размер/MIME/checksum, файл изменился при копировании или операция занята;
413 — превышен лимит; 422 — неверные параметры; 503 — ошибка S3/credentials.
MIME проверяется как метаданные загрузки, содержимое изображения не декодируется.
ETag используется для условного копирования, а не как доказательство SHA-256.

## Конкурентность и восстановление

`pending → finalizing → ready` и `pending/ready → deleting` захватываются
условным UPDATE в БД. Удаление и завершение одного файла не выполняются одновременно.
Timestamp операции служит fencing token: поздний worker не может принять файл после
перехвата операции. Ключ кандидата оригинала записывается до S3-операции.
После остановки процесса повторите тот же complete/delete по истечении
`S3_OPERATION_LEASE_SECONDS` (по умолчанию 600 секунд). Просроченный complete создаёт
новый ключ кандидата и очищает старый; delete повторяет идемпотентное удаление.
При ошибке удаления файл остаётся `deleting`, повтор DELETE разрешён сразу.
Отдельного фонового worker восстановления нет. Если процесс упал после копирования,
кандидат очищается при следующем complete; невостребованные записи требуют
операционной уборки. Ошибка БД и S3 не образуют распределённую транзакцию.
Проверка SHA-256 требует прочесть весь файл сервером при complete; обычная загрузка
и скачивание идут напрямую между клиентом и S3.

## Проверки

```sh
source .venv/bin/activate
python -m pytest -q
```

Тесты создают временную SQLite-базу и S3-эмулятор moto; реальные облачные ключи
не нужны и рабочий `horse_dataset.db` не изменяется. Проверяются политика POST,
JWT, права владельца/admin, жизненный цикл, checksum, повтор загрузки, ошибки S3,
конкурентные операции и восстановление просроченных claims.
Docker/MinIO необходимо отдельно проверить командой `docker compose up -d` и
реальной multipart POST-загрузкой: moto не заменяет проверку CORS/POST-policy
провайдера.
