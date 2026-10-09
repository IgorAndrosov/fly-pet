**Готово.** Конфиг, state, логирование и тесты на месте.

### Проверки
| Команда | Факт |
|---|---|
| `pytest -q` | `17 passed` |
| `load_config()` | `True [] False` |

### Файлы
- `config/default.yaml`
- `fly_pet/config.py`, `fly_pet/state.py`, `fly_pet/logging_setup.py`
- `tests/test_config.py`, `tests/test_state.py`, `tests/test_logging.py`

`__main__.py` / decisions / plan не трогал, `_data/` не создавал, коммитов нет.
y`, `tests/test_logging.py`
