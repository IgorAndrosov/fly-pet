**Выбрал `SetParent(Progman)`** — по замеру на живом `fly-pet`.

| Кандидат | Факт |
|---|---|
| `HWND_TOP` | остаётся под Progman |
| `SetWindowBand(ZBID_DESKTOP)` | **err=5** ACCESS_DENIED |
| `SetParent(Progman)` | `WindowFromPoint` → муха; после возврата окон Hermes её перекрывает |

**Код:** `fly_pet/window.py` — `_raise_without_activate` → adopt/release; переходы только в `_on_desktop_reassert`; облачко тоже усыновляется. `locomotion.py` — `release_desktop_parent` перед посадкой. Абзац в `docs/decisions.md`. Отчёт: `docs/tasks/_step-13-run.md`.

**Проверил:** `pytest tests/test_window_headless.py` 19 ok; `verify_step13.py` PASS (point+adopt ≤1.5с, release, overlap). Scare/two_modes на новом exe — нет (сборку задача запретила; onefile в памяти старый).

**Неуверенно:** эвристика «Show Desktop активен» по z-order/cloaked; Qt-`pos` vs client после `SetParent` на мультимониторе.
ess.py` — 19 passed
- `verify_step13.py` (код из репо) — PASS: point+adopt ≤1.5с, release, Hermes выше мухи
- облачко при Show Desktop — hit после sync/таймера сторожа
- `verify_scare` / `verify_two_modes` на новой сборке exe не гонял (сборка запрещена задачей; onefile в памяти — старый)

**Неуверенно:** детектор «Show Desktop активен» по z-order (skip-классы оболочки + cloaked) — на экзотической оболочке/мультимониторе может держать усыновление дольше нужного; координаты Qt vs client после `SetParent` опираются на то, что Qt-`pos` остаётся экранным.
