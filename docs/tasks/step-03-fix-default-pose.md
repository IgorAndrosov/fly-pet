# Правка: стартовая позиция окна берётся из дефолтного состояния (0,0)

## Диагноз (проверен владельцем вживую)
Окно питомца открывалось в левом верхнем углу экрана: `hwnd=2558788 rect=(0,0)-(96,96)`, поверх иконок
рабочего стола. Причина:

- `fly_pet/state.py`, функция `default_pose()` возвращает `{"x": 0, "y": 0, "facing": "left"}`;
- `fly_pet/window.py`, `_resolve_start_pos()` видит числовые `x`/`y` (0 и 0), считает их сохранённой
  позицией и возвращает `QPoint(0, 0)`, не доходя до расчёта «правый нижний угол».

То есть дефолт состояния маскируется под сохранённую позу. Ожидаемое поведение: если позиция никогда
не сохранялась — окно встаёт в правый нижний угол рабочей области с отступом 24 px.

## Что сделать
1. `default_pose()` → `{"x": None, "y": None, "facing": "left"}` (поза «неизвестна», а не «ноль»).
2. Убедиться, что `_resolve_start_pos()` при `None` уходит в ветку расчёта правого нижнего угла, а
   `_persist_pose()` при сохранении перезаписывает `x`/`y` числами (не превращает их в `None`).
3. Совместимость: если в существующем `state.json` поза уже сохранена числами — вести себя как раньше
   (уважать её, если точка внутри рабочей области).
4. Тесты (`tests/test_window_headless.py`, платформа `offscreen`):
   - состояние по умолчанию (без файла) → окно НЕ в точке (0,0) и совпадает с правым нижним углом
     `availableGeometry()` минус 24 px (ожидание считать в тесте из `availableGeometry()`, не хардкодить);
   - сохранённая поза (например (300, 200)) → окно встаёт туда;
   - сохранённая поза за пределами экрана (например (-5000, -5000)) → игнорируется, берётся угол;
   - если существующий тест закреплял старое поведение (позиция 0,0) — исправить его, а не подгонять код.
5. Проверка по умолчанию (дефолт-состояние) не должна требовать существования `_data/state.json`.

## Чего делать нельзя
- Не менять формат `state.json`, спрайты, конфиг, `docs/`.
- Не реализовывать еду, меню, LLM, потребности.
- Не коммитить (git делает владелец). Ничего не устанавливать.

## Как проверить свою работу (обязательно выполни сам)
```
.venv/Scripts/python.exe -m pytest -q
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "import os,sys;sys.path.insert(0,r'D:\Projects\fly-pet');from PyQt6.QtWidgets import QApplication;from fly_pet.config import load_config;from fly_pet.state import StateStore;from fly_pet.animation import load_frames;from fly_pet.window import FlyWindow;from pathlib import Path;app=QApplication([]);cfg=load_config(repo_root=Path(r'D:\Projects\fly-pet'));w=FlyWindow(cfg, StateStore(Path(os.environ['TEMP'])/'fly_fix_diag'), load_frames(Path(r'D:\Projects\fly-pet')));print('pos=',w.pos());g=app.primaryScreen().availableGeometry();print('ожидалось=',g.width()-cfg.window.width-24, g.height()-cfg.window.height-24)"
```
Если сигнатура `FlyWindow` другая — поправь однострочник под неё, суть проверки: напечатать `pos=` и
ожидаемые координаты. В отчёте приведи реальный вывод обеих команд и правленые файлы.
