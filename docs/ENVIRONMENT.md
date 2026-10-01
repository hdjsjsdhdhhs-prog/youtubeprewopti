# ENVIRONMENT

Состояние среды разработки для проекта в `C:\youtubesistemprew`.
Дата проверки: 2026-09-24.

## 1. Что было найдено

| Параметр | Значение |
|---|---|
| ОС | Microsoft Windows Server 2022 Standard, build 10.0.20348 |
| Платформа | Виртуальная машина (QEMU/KVM, SeaBIOS), гипервизор присутствует |
| CPU | 2 × vCPU Intel Xeon E312xx (Sandy Bridge) |
| Аппаратная виртуализация внутри ВМ | **Нет** — `VirtualizationFirmwareEnabled=False`, `SLAT=False` |
| RAM | 4 ГБ |
| Диск C: | ~22 ГБ свободно |
| Права | `administrator`, elevated shell (UAC не требовался) |
| Менеджер пакетов | Chocolatey 2.7.4 (winget / scoop отсутствуют) |
| Сеть | Системный HTTP(S) прокси `http://127.0.0.1:10809` (`HTTP_PROXY`/`HTTPS_PROXY` + WinINET) |
| Изначально отсутствовали | Python, Node.js, npm, Git, Docker, PostgreSQL |

## 2. Что было установлено

| Компонент | Версия | Источник | Путь |
|---|---|---|---|
| Git (+ Git LFS) | 2.55.0.windows.5 / LFS 3.7.1 | choco `git` → официальный установщик Git for Windows | `C:\Program Files\Git` |
| Python | 3.12.10 (pip 25.0.1) | choco `python312` → установщик python.org | `C:\Python312` |
| VC++ Redistributable | 14.51 (x86/x64) | зависимость Python, download.visualstudio.microsoft.com, hash проверен | системно |
| Node.js LTS | 24.21.0 (npm 11.19.0) | choco `nodejs-lts` → MSI nodejs.org | `C:\Program Files\nodejs` |
| PostgreSQL | 16.15 | choco `postgresql16` → установщик EnterpriseDB (get.enterprisedb.com), hash проверен | `C:\Program Files\PostgreSQL\16` |
| Docker CLI (+ dockerd.exe) | 29.8.1 | официальный static zip `download.docker.com/win/static/stable/x86_64/` (Docker не публикует checksum для этих архивов; загружено по HTTPS) | `C:\Program Files\docker` |
| Docker Compose plugin | v5.5.1 | официальный релиз GitHub `docker/compose`, **SHA-256 сверен с `checksums.txt`** | `C:\Program Files\docker\cli-plugins` |

Выбор версий:
- **Python 3.12** — зрелая ветка с полной поддержкой wheel-пакетов на Windows (psycopg, Pillow, numpy, opencv и т. д.); 3.13 даёт риск отсутствия бинарных колёс у части зависимостей.
- **Node 24 LTS** — актуальная LTS-линия, поддерживается Next.js.
- **PostgreSQL 16** — стабильная, широко поддерживаемая версия.

## 3. Что настроено

- **PATH (Machine)**: добавлены Git, Python, Node.js, `C:\Program Files\PostgreSQL\16\bin`, `C:\Program Files\docker`.
- **PostgreSQL**: служба `postgresql-x64-16`, автозапуск, состояние `RUNNING`, порт `5432`, кодировка `UTF8`.
- **PostgreSQL `lc_messages='C'`** (`ALTER SYSTEM`, 2026-09-24; было `Russian_Russia.1251`): при локализованных сообщениях сервера Procrastinate не распознаёт unique violation (`AssertionError` вместо `AlreadyEnqueued` — выявлено spike-тестом), а psycopg показывает ошибки нечитаемыми символами. Проверено: `SHOW lc_messages` → `C`, spike-тест очереди проходит. См. ADR-0003.
- **Пароль суперпользователя `postgres`**: сгенерирован криптографически случайно, сохранён в
  `C:\Users\Administrator\.ytlead-secrets\postgres_superuser.txt` — **вне репозитория**, ACL: только `Administrators` и `SYSTEM`.
  В документацию и репозиторий пароль не попадает. Для приложения будет создан отдельный пользователь БД с минимальными правами (Phase 1).
- **Прокси**: pip и npm работают через существующие переменные `HTTP_PROXY`/`HTTPS_PROXY` без дополнительной настройки.

## 4. Что успешно проверено (реальные команды)

| Проверка | Результат |
|---|---|
| `python --version` | Python 3.12.10 |
| `pip --version` | pip 25.0.1 (python 3.12) |
| `node --version` | v24.21.0 |
| `npm --version` / `npx --version` | 11.19.0 |
| `git --version` | git version 2.55.0.windows.5 |
| `git lfs version` | git-lfs/3.7.1 |
| `docker --version` | Docker version 29.8.1, build 4a63305 |
| `docker compose version` | Docker Compose version v5.5.1 |
| `psql --version` | psql (PostgreSQL) 16.15 |
| `psql -h 127.0.0.1 -U postgres -c "select version()"` | PostgreSQL 16.15, 64-bit; `server_encoding=UTF8` |
| `npm ping` | PONG (registry.npmjs.org доступен через прокси) |
| `python -m venv` + `pip install fastapi` + `import fastapi` | успешно (fastapi 0.141.1), тестовый venv удалён |

## 5. Ограничения

### BLOCKED — MANUAL ACTION REQUIRED: Docker Engine (Linux-контейнеры)

> Перепроверено 2026-10-01 (`Win32_Processor`: `VirtualizationFirmwareEnabled=False`,
> `SecondLevelAddressTranslationExtensions=False`, `VMMonitorModeExtensions=False`) — без изменений, установить
> Docker Engine для Linux-контейнеров на этой ВМ по-прежнему невозможно. Сборка образов проверяется job'ом
> `build-images` в GitHub Actions (на hosted Ubuntu-раннере Docker есть).

- **Причина:** ВМ не пробрасывает аппаратную виртуализацию гостю (`VirtualizationFirmwareEnabled=False`, `SecondLevelAddressTranslationExtensions=False`). Без VT-x/SLAT невозможны Hyper-V и WSL2, а значит, Linux-контейнеры (PostgreSQL, Redis, образы backend/frontend) запустить нельзя. Docker Desktop, кроме того, официально не поддерживает Windows Server.
- **Изнутри ВМ это исправить невозможно.** Установлены только `docker` CLI и `docker compose` — они работают как клиент (`docker info` → нет доступного сервера).
- **Что нужно сделать вручную (один из вариантов):**
  1. На хосте включить nested virtualization для этой ВМ (QEMU/KVM: `-cpu host` или `+vmx`; хост: `kvm_intel nested=1`), затем в ВМ: `Install-WindowsFeature Hyper-V, Microsoft-Windows-Subsystem-Linux` + WSL2 + Docker Engine, перезагрузка. Рекомендуется также увеличить RAM до ≥ 8 ГБ.
  2. Или использовать удалённый Docker-хост (Linux-сервер) через `DOCKER_HOST=ssh://user@host` — установленный CLI это поддерживает.
- **Как продолжаем без Docker:** PostgreSQL установлен нативно как Windows-служба; backend/worker запускаются из Python venv, frontend — через Node. `docker-compose.yml` всё равно будет поддерживаться для production/Linux-деплоя и валидироваться через `docker compose config` (без запуска). Запуск контейнеров на этой машине — **UNVERIFIED**.

### Redis — не установлен (сознательно)

- Официальных сборок Redis для Windows не существует (архивный порт Microsoft — Redis 3.x, устарел; Memurai — сторонний продукт). Без Docker нативный Redis недоступен.
- Это прямо влияет на выбор очереди задач: будет рассмотрен вариант очереди на PostgreSQL (без Redis). Решение фиксируется в `docs/decisions/ADR-0003-job-queue.md`.

### Прочие ограничения

- **Ресурсы:** 2 vCPU / 4 ГБ RAM — достаточно для разработки (PostgreSQL + FastAPI + Next.js dev), но `next build` и параллельные воркеры будут медленными; для массовой обработки изображений ресурсов мало.
- **Git identity** (`user.name` / `user.email`) не настроена — не задаётся от имени пользователя; потребуется перед первым коммитом.
- **Shell агента:** уже запущенные процессы не видят новый PATH; в текущих сессиях перед командами выполняется `C:\ProgramData\chocolatey\bin\RefreshEnv.cmd`. Новые окна терминала видят PATH сразу.
- **Прокси** `127.0.0.1:10809` обязателен для внешней сети; если он перестанет работать, установка пакетов и внешние API (YouTube, OpenAI) будут недоступны.
- **Внешние ключи** (YouTube Data API, OpenAI, Telegram, SMTP) не предоставлены — соответствующие интеграции будут в статусе `Not configured`.
