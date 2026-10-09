**Готово.** `81 passed`, GUI offscreen 2–3 с не падает, `--dry-run` ок.

### Что сделано
- **Scare:** `GetCursorPos` в такте локомоции; рывок / паника; кулдаун; при drag не пугается
- **Трей:** `fly_pet/tray.py` — меню по ТЗ, выход с сохранением
- **Пути:** `paths.py` → `resource_dir` / `app_dir` / `default_data_dir`
- **Сборка:** `tools/build_exe.py` (+ `--dry-run`), `.gitignore`, README

### Конфиг (`walk`)
`scare_cursor`, `scare_radius_px: 90`, `panic_radius_px: 40`, `scare_burst_px: [110, 240]`, `scare_speed_px_s: 340`, `scare_cooldown_sec: 1.2`

### Исключения PyInstaller
WebEngine*, Bluetooth/Nfc/Positioning/Location/Serial/Sensors, Multimedia*, SpatialAudio, Qt3D*, Sql/Test/Xml/Designer/Help/Pdf*, Quick/Qml/RemoteObjects — не используются, режут размер.

### Команды сборки
`--onedir --name fly-pet` → `dist/fly-pet/`  
`--onefile --name fly-pet-onefile` → `dist/fly-pet-onefile.exe`  
Общее: `--noconsole`, icon, `assets`+`config`, `--noconfirm --clean`, excludes выше.
