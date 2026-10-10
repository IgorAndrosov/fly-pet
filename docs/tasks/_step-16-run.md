# Step 16 — отчёт

## Сделано
- `fly_pet/brain.py` — OpenAI `/v1/chat/completions`, цепочка провайдеров, JSON/`plain`/`tool_calls`
- `fly_pet/envload.py` — загрузка `hermes\.env` без перезаписи env
- Конфиг: `llm.enabled`, `providers[]`, `phrase_ttl_sec`; старые `base_url`/`model`/`timeout_sec` — синонимы
- `app.py`: таймер мозга, запрос в daemon-thread, результат через Qt-сигнал → `window.say`
- Статы: `llm_calls` / `llm_failures` / `llm_last_provider`
- Фразы `brain_fail`, `decisions.md` обновлён под local→deepseek

## Проверки (подставные провайдеры)

```bash
C="C:/Users/igora/AppData/Local/hermes/cache/scratch"
.venv/Scripts/python.exe "$C/fakeserve.py" 8123 "$C/fakeserve_step16.log" &
.venv/Scripts/python.exe "$C/step16_scenarios.py" 1
.venv/Scripts/python.exe "$C/step16_scenarios.py" 2 8123
.venv/Scripts/python.exe "$C/step16_scenarios.py" 3
.venv/Scripts/python.exe -m pytest -q
```

### 1) local мёртв (`127.0.0.1:9`) → deepseek
```
WARNING провайдер local не ответил, переходим к резерву
INFO ответил провайдер deepseek (1175 мс)
ok=True provider='deepseek' failed=('local',)
say='Проголодалась и устала, может, перекушу чем-нибудь?'
```

### 2) local = fakeserve:8123 → deepseek не вызывался
```
INFO ответил провайдер local (192 мс)
ok=True provider='local' failed=()
say='Подставная локальная реплика.'
```
В логе fakeserve — один POST; deepseek на `:9` не трогали.

### 3) оба мертвы → шаблон
```
WARNING провайдер local не ответил, переходим к резерву
WARNING ни один LLM-провайдер не ответил, шаблонная реплика
ok=False from_template=True say='Без облака, но на месте.'
```

`pytest`: **90 passed**.

## Владельцу для локали
На ИИшнице: LM Studio (или аналог) слушает `192.168.0.100:1234`, модель `fly-brain`, OpenAI-compatible.
Пока выключена — срабатывает DeepSeek (ключ в `hermes\.env`).

## Неуверенно
- Модель иногда даёт say >12 слов / action из whitelist — режем только неизвестные action.
- WARNING «к резерву» один раз на серию; после успешного local сбрасывается — при флапе сети может шуметь снова.
