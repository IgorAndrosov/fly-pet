**Готово.** Локомоция работает: стол → взлёт → посадка на полосу заголовка → ходьба / улёт.

### Сделано
- `fly_pet/locomotion.py` — EnumWindows, полоса заголовка, z-order `attach_above`/`detach_to_desktop`, автомат состояний
- Интеграция в `window.py` / `app.py`, кадры `fly`/`land` в `animation.py`, блок `walk:` в конфиге
- Тесты + `LocomotionDriver.set_velocity` (заглушка)

### Проверки
```
..........................................................               [100%]
58 passed in 0.87s
```
Offscreen `--gui` ~3с — без падения.

**Живой замер** (короткие `desktop_stay_sec: [1.5, 2]`): муха стабильно села на верх `Chrome_WidgetWin_1 | Hermes` (`fly_top≈-42` при `window.top=-8`, `dx` менялся, `dy` нет — ходьба по полосе). `_data/settings.yaml` и `state.json` удалены.
