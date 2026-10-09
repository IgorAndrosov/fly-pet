**Готово.** Дефолт-поза больше не маскируется под (0,0).

**Правки**
- `fly_pet/state.py` — `default_pose()` → `x/y: None`
- `tests/test_window_headless.py` — 3 теста: дефолт→угол, сохранённая поза, out-of-bounds
- `window.py` не трогал: `_resolve_start_pos` уже игнорит non-число, `_persist_pose` пишет int

**Проверки**
```
30 passed in 0.61s
pos= PyQt6.QtCore.QPoint(680, 680)
ожидалось= 680 680
```

Коммит не делал.
