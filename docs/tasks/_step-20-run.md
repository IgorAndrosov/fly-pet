Монитор по номеру теперь ищется так: сначала имя Qt `\\.\DISPLAY<n>`, если его нет — прямоугольник Win32 (`EnumDisplayMonitors` + `GetMonitorInfoW`) против `geometry() × devicePixelRatio` (допуск 2 px, иначе экран, в котором лежит центр), иначе один WARNING и основной экран. До создания `QApplication` включается per-monitor DPI v2.

В логе две строки: список Win32 (`\\.\DISPLAY1 … work=… primary`) и как нашли экран (`монитор 1: C30SK PRO (по Win32 \\.\DISPLAY1), область ходьбы …`). Если сырой Qt-прямоугольник и `rcMonitor` разъехались больше чем на 2 px — WARNING с обоими.

**Тесты:** `pytest -q` — **113 passed**. Фейки Qt `C30SK PRO` / `DELL U2412` и Win32 `DISPLAY1/2`: номера 1 и 2 берутся по прямоугольнику, `primary` — основной, `all` — объединение. Нет ни имени, ни прямоугольника — `fallback=True` на основной. Qt 1280×720 при dpr=2 и Win32 2560×1440 — экран найден, в логе WARNING.

**Живой вызов** (здесь один монитор, имя Qt как раз `\\.\DISPLAY1`):

```
[MonitorDesc(device='\\\\.\\DISPLAY1', monitor=Rect(left=0, top=0, right=1920, bottom=1080), work=Rect(left=0, top=0, right=1920, bottom=1032), primary=True)]
Rect(left=0, top=0, right=1920, bottom=1032)
```

**Неуверенно.** Связку EDID-имени с `\\.\DISPLAY<n>` на двух мониторах вживую не гонял — только фейки. При разном масштабе на двух экранах `geometry × dpr` может не совпасть с физическим `rcMonitor`; тогда остаётся центр прямоугольника или откат на основной.
�дну логическую сетку, простой `geometry × dpr` может не совпасть с физическим `rcMonitor`; тогда сработает центр или откат на основной.
