**Готово.** Муха садится на настоящее окно и видна на нём.

### Что починил
1. **Жёсткий фильтр** кандидатов (owner, toolwindow, caption/sysmenu, чёрный список классов в т.ч. ASUS/`_q_titlebar`, экран, заголовок).
2. **Баг z-порядка:** `SetWindowPos(our, target)` ставил муху *под* цель → теперь встаём после `GW_HWNDPREV` цели.
3. **Самопроверка** посадки + `max_attach_attempts: 3`.

### Проверки
- `pytest -q` → **63 passed**
- Живой замер на полосе Hermes (`y=−42`):

| | |
|---|---|
| в точке | `Qt6112QWindowToolSaveBits \| fly-pet` |
| ниже | `Chrome_WidgetWin_1 \| Hermes` |
| выше | `IME \| Default IME` |

Скрин: `…/hermes/cache/scratch/diag_on_window.png` (муха видна на рамке окна).  
`_data/settings.yaml` / `state.json` убраны. Отчёт: `docs/tasks/_step-08-run.md`. Коммитов нет.
�амер (ходьба по полосе Hermes, y=−42)
| | |
|---|---|
| в точке центра спрайта | `Qt6112QWindowToolSaveBits \| fly-pet` |
| ниже (z-порядок) | `Chrome_WidgetWin_1 \| Hermes` |
| выше | `IME \| Default IME` |

Скрин: `C:\Users\igora\AppData\Local\hermes\cache\scratch\diag_on_window.png` (t≈4.4 с, x=1791, y=−42).

`_data/settings.yaml` и `_data/state.json` удалены. Коммитов нет.
