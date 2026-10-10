**Готово.** Детектор «стол над мухой» без обхода верха z-порядка; муха стабильно выходит из-под стола после клика по уголку панели задач.

**Сделано**
- `fly_pet/window.py`: `_is_desktop_on_top` = `WindowFromPoint` + `GetAncestor∈{Progman,WorkerW}` + `GW_HWNDPREV`; убран обход с `_ZORDER_SKIP` / cloaked.
- Hysteresis: adopt после 2 тиков, release после 3; INFO на каждое усыновление/отпускание.
- Своё окно/облачко в точке при уже усыновлённом — держим (не ложный отпуск).
- Локомоция больше не срывает `SetParent` в полёте; `detach_to_desktop` — no-op если parent уже Progman.
- Ловим пересоздание hwnd / сброс parent → повторное усыновление в INFO.

**Проверки**
- `pytest tests/test_window_headless.py tests/test_locomotion.py` — 49 passed.
- `taskbar_showdesktop.py 12 2`: после клика уголка EnumWindows не видит муху (= child Progman) все 12 с; после возврата `родитель=#32769`, перекрыта Chrome.
- `fly_live_monitor.py 25`: `НЕВИДИМА` → `не найдено` за ~1,8 с, дальше стабильно усыновлена.
- `verify_desktop_fix.py` (ToggleDesktop): `родитель=Progman`, в точке своё Qt-окно; после возврата `topmost=False`.
- Лог цикла уголка: одна `усыновил` + одна `отпустил` (без дребезга).

**Неуверенно**
- Скрипты hermes ищут только через `EnumWindows` — при успехе пишут «не найдено», а не `родитель=Progman` (для Progman нужен `EnumChildWindows`).
- Hysteresis 2×800 мс даёт ~1,6 с до adopt — на грани критерия «~1,5 с».
