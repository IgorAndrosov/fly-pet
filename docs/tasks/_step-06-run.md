**Готово.** Сторож `desktop_reassert` поднимает муху только над рабочим столом после Win+D, обычные окна не трогает.

**Сделано:** ключи в `config/default.yaml` + `WindowConfig`; таймер и `_is_desktop_on_top()` / `_raise_without_activate()` в `window.py`; тесты; README.

**Проверки:**
- `pytest -q` → **49 passed**
- живой прогон: после `ToggleDesktop` в точке мухи снова `fly-pet` (`Qt6112QWindowToolSaveBits`), не `SysListView32`; окна вернул вторым `ToggleDesktop`, процесс убит, `_data/settings.yaml` и `state.json` убраны

Коммит не делал.
