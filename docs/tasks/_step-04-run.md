**Готово.** `never_minimize` + `tool_window` в конфиге, `FlyWindow` с `Qt.Tool` и отложенным откатом из minimize, тесты + README.

**Проверки:**
- `pytest -q` → **33 passed**
- `diag_minimize.py`:
  - `exstyle=0x80080` → `WS_EX_TOOLWINDOW есть`
  - после `SC_MINIMIZE`: `isMinimized = False`, позиция `(620, 420)`

Коммита нет (по задаче).
