# План: десктопный питомец «живая муха» (этап 1)

---

## 1. Структура проекта

```
fly-pet/
├── README.md                 # запуск, env, dry-run, ограничения
├── requirements.txt          # PyQt6, PyYAML (или orjson), httpx/openai-compat, python-dotenv
├── .gitignore                # .env, __pycache__, *.pyc, state/, logs/, quarantine/
├── config/
│   └── default.yaml          # белый список, dry-run, тики, окно, пути
├── assets/
│   ├── fly/                  # PNG-спрайты / спрайтшиты
│   │   ├── idle.png
│   │   ├── walk_*.png
│   │   └── sleep_*.png
│   └── ui/
│       └── bubble_tail.png   # опционально, хвост облачка
├── src/
│   └── fly_pet/
│       ├── __init__.py
│       ├── __main__.py       # python -m fly_pet
│       ├── app.py            # сборка QApplication, DI модулей, главный цикл
│       ├── config.py         # загрузка YAML + override путей
│       ├── state.py          # загрузка/сохранение JSON состояния
│       ├── needs.py          # петля потребностей (тик decay + apply effects)
│       ├── window.py         # прозрачное always-on-top окно + спрайт
│       ├── animation.py      # кадры ходьбы/сна/idle
│       ├── speech_bubble.py  # короткое облачко реплики
│       ├── brain/
│       │   ├── __init__.py
│       │   ├── client.py     # HTTP к DeepSeek (OpenAI-compatible)
│       │   ├── prompts.py    # системный + user шаблоны
│       │   └── parser.py     # строгий JSON → Decision
│       ├── actions/
│       │   ├── __init__.py
│       │   ├── registry.py   # белый список → callable
│       │   ├── base.py       # ActionContext, Result, dry-run обёртка
│       │   ├── summarize_tasks.py
│       │   ├── tidy_junk.py  # перенос в quarantine, не delete
│       │   ├── remind.py
│       │   ├── idle_nudge.py
│       │   └── sleep.py
│       ├── sensors/
│       │   ├── __init__.py
│       │   └── activity.py   # простой idle-детектор (last input / фокус)
│       └── logging_setup.py  # файл логов + ротация, без секретов
├── scripts/
│   └── run.ps1 / run.sh      # опционально: подтянуть .env и запустить
└── docs/
    ├── plan-brief.md         # уже есть
    └── architecture.md       # после реализации (или кратко из этого плана)
```

**Вне репозитория (профиль пользователя):**
- `%LOCALAPPDATA%\fly-pet\state.json` — состояние питомца  
- `%LOCALAPPDATA%\fly-pet\logs\*.log` — действия и решения  
- `%LOCALAPPDATA%\fly-pet\quarantine\` — «уборка» файлов  
- Ключ: только `DEEPSEEK_API_KEY` из `hermes\.env` (или системного env), не копировать в проект  

---

## 2. Модули

| Модуль | Файлы | Ответственность |
|--------|--------|-----------------|
| **Окно / спрайты** | `window.py`, `animation.py`, `speech_bubble.py` | Frameless прозрачное окно, always-on-top, отрисовка спрайта, смена анимации по состоянию, облачко 2–5 с |
| **Петля потребностей** | `needs.py` | Тик: decay hunger/energy/mood/attention; пороги → bias в промпт и локальные fallback (сон при низком energy) |
| **LLM-мозг** | `brain/*` | Периодический вызов API; строгий JSON `{action, say, reason}`; валидация по whitelist |
| **Реестр действий** | `actions/*` | Регистрация по имени из конфига; dry-run по умолчанию; лог до/после |
| **Конфиг** | `config.py` + `config/default.yaml` | dry_run, whitelist, пути, интервалы тика/мозга, геометрия окна |
| **Логирование** | `logging_setup.py` | decision / action / error; redact API key; без тела промпта с секретами |
| **Состояние** | `state.py` | needs, position, last_action, last_user_activity, mood flags; atomic write |

**Связка в `app.py` (псевдо):**  
`timer_needs` → `needs.tick()` → `state.save()`  
`timer_brain` → `brain.decide(state, sensors)` → `registry.run(decision)` → `bubble.show(say)` → `animation.set(mode)`  

Локальные правила (без LLM): если energy < порог → `sleep`; если dry_run и action запрещён → только `say`.

---

## 3. Порядок работ (пустой репо → MVP)

1. **Скелет пакета**  
   Каталоги, `requirements.txt`, `.gitignore`, `__main__.py` печатает «fly-pet ok».  
   *Готово:* `python -m fly_pet` без Qt.

2. **Конфиг + state**  
   Загрузка YAML, дефолтный `state.json` в AppData, atomic save.  
   *Готово:* два запуска подряд — state сохраняется и читается.

3. **Логирование**  
   Файл в AppData, уровни, scrub env-подобных строк.  
   *Готово:* тестовый лог без ключа в тексте.

4. **Окно-спрайт**  
   Прозрачное frameless, always-on-top, PNG idle, клик/перетаскивание (решить: click-through или drag-handle).  
   *Готово:* муха видна поверх Explorer/браузера, не крашится.

5. **Анимации + облачко**  
   walk/sleep циклы, `SpeechBubble.show(text, ttl)`.  
   *Готово:* ручной вызов смены анимации и реплики из тестового хука.

6. **Needs loop**  
   Decay по интервалу, clamp 0–100, влияние на «режим» (sleep/idle).  
   *Готово:* ускоренный тик за минуту — hunger падает, state обновлён.

7. **Registry + dry-run действия**  
   3–5 действий из whitelist; каждое логируется; tidy = move в quarantine.  
   *Готово:* CLI/dev-кнопка `run action=tidy_junk` в dry-run пишет «would move X», файлы на месте.

8. **DeepSeek brain**  
   Загрузка ключа из env/hermes `.env`; prompt + JSON parser; reject unknown actions.  
   *Готово:* offline mock + один живой вызов → валидный Decision или graceful fallback.

9. **Склейка MVP**  
   Таймеры needs/brain/sensors; действие → анимация/реплика/needs delta.  
   *Готово:* 10–15 мин работы: муха говорит, «спит», в dry-run докладывает действия, RAM ~200–300 МБ.

10. **Жёсткая проверка безопасности**  
    Нет delete/sys settings; dry_run default; реальный флаг явно.  
    *Готово:* чеклист в README пройден вручную.

---

## 4. Форматы конфига и состояния

### `config/default.yaml` (минимум)

```yaml
app:
  locale: ru
  data_dir: null          # null → %LOCALAPPDATA%/fly-pet

window:
  width: 96
  height: 96
  always_on_top: true
  click_through: false    # true = мышь проходит сквозь; false = можно таскать
  start_pos: { x: null, y: null }  # null → правый низ экрана

tick:
  needs_interval_sec: 30
  brain_interval_sec: 120
  animation_fps: 8

needs:
  decay_per_minute:
    hunger: 0.8
    energy: 0.5
    mood: 0.3
    attention: 1.0
  thresholds:
    sleep_energy_below: 20
    complain_hunger_above: 70

llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  timeout_sec: 30
  max_tokens: 256
  temperature: 0.4

safety:
  dry_run: true
  allow_real_actions: false   # оба флага: реальные действия только если dry_run=false И allow_real_actions=true

actions:
  whitelist:
    - name: summarize_tasks
      enabled: true
      params: { source: "local_notes" }  # узкий источник, не весь диск
    - name: tidy_junk
      enabled: true
      params:
        watch_dir: "%USERPROFILE%/Downloads/_fly_junk"
        quarantine_subdir: quarantine
    - name: remind
      enabled: true
      params: { max_text_len: 120 }
    - name: idle_nudge
      enabled: true
      params: { idle_after_sec: 900 }
    - name: sleep
      enabled: true
      params: {}

paths:
  hermes_env: "C:/Users/igora/AppData/Local/hermes/.env"  # только чтение ключа
```

### `state.json` (AppData)

```json
{
  "version": 1,
  "needs": {
    "hunger": 40,
    "energy": 70,
    "mood": 60,
    "attention": 50
  },
  "pose": { "x": 1400, "y": 800, "facing": "left" },
  "mode": "idle",
  "last_brain_at": null,
  "last_action": { "name": null, "at": null, "dry_run": true, "ok": null },
  "last_user_activity_at": null,
  "pending_reminder": null,
  "stats": { "actions_run": 0, "llm_calls": 0, "llm_failures": 0 }
}
```

---

## 5. Вызов DeepSeek

**Один модуль-мозг:** `brain/client.py` + `prompts.py` + `parser.py`.

**Поток:**
1. Собрать контекст: needs, mode, idle_sec, whitelist names+короткие описания, dry_run.
2. System prompt (RU): «ты питомец-муха; отвечай ТОЛЬКО JSON; action ∈ whitelist ∪ null; say ≤ N символов; не выдумывай действия».
3. User message: снимок state + sensor.
4. HTTP chat.completions (DeepSeek OpenAI-compatible), ключ из env.
5. `parser.parse`: extract JSON (допуск на ```json), schema validate:
   - `action`: string|null ∈ whitelist или null  
   - `say`: string, max len  
   - `reason`: optional, только в лог  
6. Если parse/HTTP fail → fallback: локальное правило (sleep / idle_nudge / null + шаблонная реплика).
7. Registry: если action not in enabled whitelist → reject + лог; не выполнять.
8. После успеха: обновить needs (например tidy → −hunger), сохранить state.

**Ограничение фантазии:**  
temperature низкая; max_tokens маленький; schema жёсткая; whitelist в промпте **и** в коде; dry_run не отключается моделью; никаких «свободных» shell/path от модели — только params из конфига.

**Псевдо Decision:**
```
Decision(action: str|None, say: str, reason: str|None)
```

---

## 6. Риски PyQt6 на Windows и обходы

| Проблема | Симптом | Обход |
|----------|---------|--------|
| **Прозрачность** | Чёрный фон вместо alpha | `WA_TranslucentBackground` + frameless; рисовать в `paintEvent` с QPixmap alpha; не смешивать с непрозрачным QWidget-фоном |
| **Click-through** | Мышь не доходит до окон под мухой / наоборот нельзя схватить | Win32 `WS_EX_TRANSPARENT` + `WS_EX_LAYERED` только в режиме click-through; иначе окно маленькое hit-box + drag; переключатель в конфиге |
| **Always-on-top** | Уходит под другие topmost / тулбары | `WindowStaysOnTopHint`; периодический re-raise осторожно (не спамить); не бороться с Task Manager / некоторых overlay |
| **Полноэкранные игры/видео** | Муха поверх или наоборот пропадает; exclusive fullscreen часто «съедает» overlay | Документировать: поверх borderless/windowed ок; exclusive FS — ожидаемо ненадёжно; опция «пауза на fullscreen» через простой эвристический сенсор (опционально этап 1.1) |
| **DPI / multi-monitor** | Координаты мимо экрана | `AA_EnableHighDpiScaling` (если ещё актуально для 3.13/Qt6), позиции в logical coords, clamp к `availableGeometry` |
| **Фокус / Alt-Tab** | Муха крадёт фокус | `WA_ShowWithoutActivating`, не активировать окно при тике/облачке |
| **Память / утечки Pixmap** | Рост RAM | один набор кадров в памяти, не грузить PNG каждый кадр; один QTimer на анимацию |
| **Трей / закрытие** | Закрыл — state не сохранился | `aboutToQuit` → `state.save()`; опционально иконка в трее без автозапуска |

---

## 7. Этап 1 vs этап 2

### Этап 1 (входит)
- Прозрачный спрайт + idle/walk/sleep + русские облачка  
- Needs loop + JSON state в AppData  
- DeepSeek API brain, строгий JSON, whitelist  
- 3–5 безопасных действий, dry-run по умолчанию, quarantine вместо delete  
- Логи решений/действий, без секретов  
- Без автозапуска, без глобальных install, RAM-бюджет  
- Задел: интерфейс `LocomotionDriver` / stub `set_velocity(vx, turn)` в animation/window — пустая реализация  

### Этап 2 (осознанно отложено)
- Connectome / LIF / веса FlyWire (аналог webgpu-fly)  
- Честная локомоция от спайковой модели  
- Локальные LLM  
- Удаление файлов, смена системных настроек, широкий доступ к диску  
- Electron, автозапуск, системные хуки «глубокого» мониторинга  
- Богатый UI (панель настроек, магазин «еды» и т.п.) — только если понадобится после приёмки MVP  

---

**DoD этапа 1 (предложение):** 15 минут на Windows 11: муха на столе, needs меняются, раз в ~2 мин реплика/решение от DeepSeek (или fallback), действия только whitelist + dry-run логируют «would …», quarantine-move работает при явном флаге, пик RAM ≤ ~300 МБ, ключ нигде в репо/логах.

Код не писал, файлы не менял — только план по брифу.
