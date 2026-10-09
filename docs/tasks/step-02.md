# Задача 2: конфиг, состояние, логирование (шаги 2–3 плана)

Работай только внутри `D:\Projects\fly-pet`. Ничего не устанавливай: нужные пакеты уже стоят в
проектной venv `.venv` (PyQt6, Pillow, PyYAML, httpx, pytest). Другие проекты и `D:\hermes` не трогай.
PyQt6 в этом шаге **не использовать** — он понадобится на шаге 4 (окно).

Обязательный контекст: `docs/decisions.md` (согласованные решения — код не должен им противоречить) и
`docs/plan-stage1.md` (общий план). Уже существует `fly_pet/__init__.py` и `fly_pet/__main__.py` —
их структуру сохрани, `__main__.py` менять не нужно.

## 1. `config/default.yaml` — дефолты в репозитории

Значения должны точно соответствовать `docs/decisions.md`:

- `app`: `locale: ru`, `data_dir: null` (null → каталог `_data` рядом с корнем репозитория).
- `window`: `width: 96`, `height: 96`, `always_on_top: false`, `click_through: false`,
  `feeding_point: {x: null, y: null}` (null → рассчитать как правый нижний угол рабочей области),
  заголовок/имя окна на будущее.
- `tick`: `needs_interval_sec: 30`, `brain_interval_sec: 120`, `animation_fps: 8`.
- `needs`: стартовые значения (hunger 40, energy 70, mood 60, attention 50),
  `decay_per_minute` для каждого, пороги `sleep_energy_below: 20`, `complain_hunger_above: 70`.
- `llm`: `base_url: https://api.deepseek.com`, `model: deepseek-chat`, `timeout_sec: 30`,
  `max_tokens: 256`, `temperature: 0.4`.
- `safety`: `dry_run: true`, `allow_real_actions: false`.
- `desktop`: правила еды с рабочего стола:
  - `use_registry_path: true` (путь рабочего стола из реестра, а не хардкод),
  - `allowed_extensions: []` — **пустой белый список по умолчанию**,
  - `blocked_extensions: [".lnk", ".url", ".ini"]`,
  - `blocked_names: ["desktop.ini"]`,
  - `exclude_globs: []`,
  - `min_age_days: 7`,
  - `skip_hidden: true`, `skip_system: true`, `files_only: true`,
  - `never_dirs: ["C:/Users/Public/Desktop"]` (общий рабочий стол не трогаем).
- `actions`: список из четырёх действий с `enabled: true` — `eat_desktop_file`, `report`,
  `idle_nudge`, `sleep`; у `idle_nudge` параметр `idle_after_sec: 900`.
- `paths`: `hermes_env: "C:/Users/igora/AppData/Local/hermes/.env"` (только путь, ключ оттуда читает
  мозг на шаге 8; в этом шаге ключ нигде не читать и не логировать).

## 2. `fly_pet/config.py`

- Почти-иммутабельные dataclass-ы или pydantic-подобная валидация без внешних зависимостей (pydantic
  не добавлять): `Config`, `WindowConfig`, `TickConfig`, `NeedsConfig`, `LlmConfig`, `SafetyConfig`,
  `DesktopConfig`, `ActionConfig`.
- `load_config(repo_root: Path | None = None, data_dir: Path | None = None) -> Config`:
  читает `config/default.yaml`, затем, если есть, `_data/settings.yaml` и **сливает** (правки
  пользователя побеждают; слияние словарей рекурсивное, списки заменяются целиком).
- Каталог данных: аргумент `data_dir` → переменная окружения `FLY_PET_DATA_DIR` → `_data` в корне
  репозитория. Корень репозитория определять от `__file__`, не по текущему каталогу.
- `Config.data_path(name)` — путь внутри каталога данных; каталог данных при загрузке **не создавать**
  (создаётся при первой записи).
- Ошибки: понятные исключения по-русски (`ConfigError`), если YAML битый или значение неверного типа.

## 3. `fly_pet/state.py`

- Dataclass `PetState` с полями: `version` (1), `needs` (словарь hunger/energy/mood/attention),
  `pose` (`x`, `y`, `facing`), `mode` (`idle`/`walk`/`eat`/`sleep`), `last_brain_at`,
  `last_action` (`name`, `at`, `dry_run`, `ok`), `last_user_activity_at`, `pending_reminder`, `stats`
  (`actions_run`, `llm_calls`, `llm_failures`), `eaten` (сколько файлов унесено за всё время).
- `StateStore(data_dir: Path)`: `load()` — из `state.json`, при отсутствии/битом файле возвращает
  дефолтное состояние и пишет предупреждение в лог (битый файл **не** удалять, а переименовать в
  `state.json.broken-<дата>`); `save()` — атомарно (временный файл + `os.replace`), UTF-8,
  `ensure_ascii=False`, отступ 2.
- Валидация: значения потребностей клампятся в 0–100, неизвестные поля не теряются при сохранении
  (хранить их как есть), версия схемы пишется в файл.

## 4. `fly_pet/logging_setup.py`

- `setup_logging(data_dir: Path, level: int = logging.INFO) -> logging.Logger` — логгер `fly_pet`,
  `RotatingFileHandler` на `_data/logs/fly-pet.log` (UTF-8, ротация, например 1 МБ × 5 файлов),
  формат с датой-временем, уровнем и именем функции; повторный вызов не должен плодить хендлеры.
- `SecretFilter` — фильтр, который вырезает похожее на секреты из сообщений: значения переменных
  окружения `DEEPSEEK_API_KEY`, `TELEGRAM_BOT_TOKEN`, любые токены вида `sk-…`, `Bearer …`
  заменяются на `***`. Подключить к хендлерам.
- Функция `redact(text: str) -> str` — та же логика отдельно, с тестами.

## 5. Тесты (pytest, без Qt)

`tests/test_config.py`, `tests/test_state.py`, `tests/test_logging.py`:

- config: загрузка дефолта; `allowed_extensions` пустой; `always_on_top is False`; `dry_run is True`;
  слияние с `settings.yaml` (пользователь расширил `allowed_extensions` и поменял `min_age_days`) —
  проверь, что остальные дефолты сохранились; битый YAML → `ConfigError`; `FLY_PET_DATA_DIR` через
  `monkeypatch` меняет каталог данных.
- state: дефолт при отсутствии файла; save → load возвращает то же; клампы 0–100; битый JSON
  переименовывается и возвращается дефолт; сохранение идёт атомарно (после save в каталоге нет
  временных файлов).
- logging: строка с `sk-` и с ключом из переменной окружения после фильтра не содержит секрета;
  повторный `setup_logging` не добавляет второй хендлер на тот же файл.

Все тесты — на `tmp_path`, ничего не пишут в реальный `_data`.

## Чего делать нельзя
- Не импортировать PyQt6/PIL. Не читать и не логировать реальный API-ключ.
- Не создавать окно, спрайты, LLM-клиент, действия, меню — это следующие шаги.
- Не менять `fly_pet/__main__.py`, `docs/plan-stage1.md`, `docs/decisions.md`.
- Не создавать реальный `_data/` в репозитории как часть работы.
- Не коммитить ничего самому (git-операции делает владелец).

## Как проверить свою работу (обязательно выполни сам)
```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -c "from fly_pet.config import load_config; c=load_config(); print(c.safety.dry_run, c.desktop.allowed_extensions, c.window.always_on_top)"
```
Вторая команда должна напечатать `True [] False`. В ответе перечисли созданные/изменённые файлы и
реальный вывод обеих команд.
