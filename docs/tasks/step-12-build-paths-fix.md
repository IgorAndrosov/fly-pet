# Правка: сборка падает на относительных путях в --add-data

## Что произошло (проверено владельцем)
`python tools/build_exe.py` падает на первой же сборке:

```
210 INFO: wrote D:\Projects\fly-pet\build\fly-pet.spec
ERROR: Unable to find 'D:\Projects\fly-pet\build\assets' when adding binary and data files.
Ошибка сборки (onedir → dist/fly-pet/), код 1
```

Причина: пути в `--add-data` заданы относительными (`assets;assets`, `config;config`), а в скрипте
указан `--specpath build`, поэтому PyInstaller ищет данные относительно каталога спецификации
(`build\assets`), а не корня репозитория.

## Что сделать
В `tools/build_exe.py`, функция `build_commands`, задавать **абсолютные** исходные пути:
`--add-data=f"{root / 'assets'}{sep}assets"` и `--add-data=f"{root / 'config'}{sep}config"`
(`root` уже есть в функции — использовать его, а не пересчитывать). Целевые имена внутри сборки
(`assets`, `config`) оставить как есть: от них зависят `paths.resource_dir()` и загрузка спрайтов.

Больше ничего в скрипте не менять: состав исключаемых модулей, `--noconsole`, `--icon`, `--name`,
`--distpath`, `--workpath`, `--specpath`, `--paths`, `--hidden-import`, вывод итогов и код возврата
должны остаться прежними.

## Проверка (обязательно выполни сам)
```
.venv/Scripts/python.exe tools/build_exe.py --dry-run
```
В выводе команды обеих сборок должны содержать **полные** пути к `assets` и `config`
(начинающиеся с `D:\Projects\fly-pet\`), а не `assets;assets`.

Плюс подтвердить, что проблема именно в этом: запусти
```
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, r'D:\Projects\fly-pet\tools'); from build_exe import build_commands; c=build_commands(); print([a for a in c[0][1] if 'add-data' in a])"
```
и покажи результат. Собирать exe полностью **не нужно** — сборку владелец запускает сам.

## Чего нельзя делать
- Менять что-либо ещё в проекте (спрайты, локомоцию, трей, конфиг, docs).
- Коммитить, ставить пакеты, запускать полную сборку.
