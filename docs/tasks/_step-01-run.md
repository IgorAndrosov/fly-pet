**Готово.** Скелет шага 1 собран, проверка прошла.

### Проверка
| Команда | Вывод |
|---|---|
| `python -m fly_pet` | `fly-pet ok, версия 0.1.0, Python 3.13.14` |
| `python -m fly_pet --version` | `0.1.0` |
| `pytest -q` | `....` → **4 passed** |

### Созданные файлы
- `fly_pet/__init__.py`, `fly_pet/__main__.py`
- `requirements.txt`, `.gitignore`, `README.md`
- `tests/test_smoke.py`

### Заметка
`.venv` был пустой (только pip), хотя в ТЗ «уже всё есть». Поставил зависимости в проектную venv из `requirements.txt`, чтобы прогнать проверку.
