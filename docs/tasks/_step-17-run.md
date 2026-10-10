Готово. Демо-поедание работает end-to-end.

**Файлы:** `desktop_icons.py`, `eat.py`; правки `locomotion.py` (`fly_to`), `tray.py`, `__main__.py` (`--demo-eat`), `app.py`, `phrases.py`, `state.py` (`stats.eaten`).

**Прогон** `FLY_PET_DATA_DIR=.../_data_check --demo-eat --gui`:

1. создал `…\Рабочий стол\муха-демо.txt`
2. нашёл иконку экран=`(14, 702)`, центр=`(52, 752)`
3. полетел `(1713, 912) → (4, 692)`
4. прилетел → chew → перенёс в `_data_check/trash/муха-демо.txt`
5. `eaten=1`, `stats.eaten=1`, `mode=desktop`

`pytest -q`: **94 passed**. Отчёт: `docs/tasks/_step-17-run.md`.Step 17 закрыт: `--demo-eat` отработал (файл → иконка → подлёт → chew → карантин), `stats.eaten=1`, pytest 94 green. Процессы демо после проверки убиты (exit 127 — это Stop-Process, не ошибка демо).
ететь в точку (1713, 912) → (4, 692)
INFO прилетел в (4, 692)
INFO прожевываю демо-файл
INFO перенёс в карантин: D:\Projects\fly-pet\_data_check\trash\муха-демо.txt
INFO демо съедено: eaten=1 stats.eaten=1
```

ФС: файла на столе нет; есть `_data_check/trash/муха-демо.txt`; `state.json`: `eaten=1`, `stats.eaten=1`, `mode=desktop`.

`pytest -q`: **94 passed**.

## Неуверенно
- Имя в ListView иногда без расширения — матчим stem и полное имя; на другой политике HideFileExt ок.
- После `Restart-Explorer` HWND ListView устаревает — каждый поиск переищет окно заново.
