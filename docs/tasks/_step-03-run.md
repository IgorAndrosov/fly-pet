Готово.

**Проверки**
- `pytest -q` → **27 passed**
- `QT_QPA_PLATFORM=offscreen … --gui` (timeout 5 с) → строка `питомец запущен, данные: D:\Projects\fly-pet\_data`, процесс жил до таймаута (exit 124), не падал

**Файлы**
- новые: `fly_pet/animation.py`, `fly_pet/window.py`, `fly_pet/app.py`, `tests/test_animation.py`, `tests/test_window_headless.py`
- изменён: `fly_pet/__main__.py` (`--gui`)
- `config/default.yaml` не трогал — ключи `window.*` / `tick.animation_fps` уже были
