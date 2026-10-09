**Готово.** В `build_commands` для `--add-data` теперь абсолютные пути через `root`.

**Проверено:**
- `--dry-run`: обе сборки с `--add-data=D:\Projects\fly-pet\assets;assets` и `...\config;config`
- `build_commands()`: `['--add-data=D:\\Projects\\fly-pet\\assets;assets', '--add-data=D:\\Projects\\fly-pet\\config;config']`

Полную сборку не гонял — как в задаче.
