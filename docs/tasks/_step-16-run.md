**Готово.** Мозг с цепочкой local→DeepSeek; GUI не блокируется.

### Добавлено
- `fly_pet/brain.py` — httpx `/v1/chat/completions`, fallback по провайдерам
- `fly_pet/envload.py` — `hermes\.env` без перезаписи env
- Конфиг: `providers[]`, `enabled`, `phrase_ttl_sec`; старые `base_url`/`model` живут
- Таймер в `app.py` + поток + Qt-сигнал → `say`
- Статы: `llm_calls` / `llm_failures` / `llm_last_provider`

### Проверки

**1) мёртвый `:9` → DeepSeek**
```
WARNING провайдер local не ответил, переходим к резерву
INFO ответил провайдер deepseek (1175 мс)
provider='deepseek' failed=('local',)
```

**2) fakeserve:8123 → local, DeepSeek не трогали**
```
INFO ответил провайдер local (192 мс)
say='Подставная локальная реплика.'
```

**3) оба мертвы → шаблон**
```
WARNING ни один LLM-провайдер не ответил, шаблонная реплика
from_template=True say='Без облака, но на месте.'
```

`pytest -q` → **90 passed**. GUI offscreen с `_data_check` (local мёртв): `llm_last_provider=deepseek`, `llm_failures=1`.

### Владельцу
LM Studio на ИИшнице: `192.168.0.100:1234`, модель `fly-brain`. Пока выключена — работает DeepSeek из `hermes\.env`.

**Неуверенно:** модель иногда нарушает лимит 12 слов; длину не режем в коде.Фоновый `fakeserve` на `:8123` завершился с кодом выхода после остановки — это ожидаемо: сервер уже отработал сценарий 2 и был убит.

Шаг 16 по проверкам закрыт, доделывать по fakeserve нечего.
�р не ответил, шаблонная реплика
ok=False from_template=True say='Без облака, но на месте.'
```

`pytest`: **90 passed**.

## Владельцу для локали
На ИИшнице: LM Studio (или аналог) слушает `192.168.0.100:1234`, модель `fly-brain`, OpenAI-compatible.
Пока выключена — срабатывает DeepSeek (ключ в `hermes\.env`).

## Неуверенно
- Модель иногда даёт say >12 слов / action из whitelist — режем только неизвестные action.
- WARNING «к резерву» один раз на серию; после успешного local сбрасывается — при флапе сети может шуметь снова.
