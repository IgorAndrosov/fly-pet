"""Тесты цепочки LLM-провайдеров и парсинга ответа."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from fly_pet.brain import BrainClient
from fly_pet.config import LlmConfig, LlmProviderConfig
from fly_pet.envload import load_env_file


def _llm(providers: list[LlmProviderConfig]) -> LlmConfig:
    first = providers[0]
    return LlmConfig(
        enabled=True,
        providers=providers,
        max_tokens=64,
        temperature=0.1,
        phrase_ttl_sec=5.0,
        base_url=first.base_url,
        model=first.model,
        timeout_sec=first.timeout_sec,
    )


def _provider(
    name: str, base_url: str, *, timeout_sec: int = 2, api_key_env: str | None = None
) -> LlmProviderConfig:
    return LlmProviderConfig(
        name=name,
        base_url=base_url,
        model="test-model",
        timeout_sec=timeout_sec,
        api_key_env=api_key_env,
    )


class _FakeHandler(BaseHTTPRequestHandler):
    responses: dict[str, Any] = {}
    hits: list[str] = []

    def log_message(self, *args: Any) -> None:  # noqa: ANN401
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        key = self.path
        _FakeHandler.hits.append(key)
        reply = _FakeHandler.responses.get(key)
        if reply is None:
            self.send_response(500)
            self.end_headers()
            return
        payload = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def fake_server() -> tuple[str, ThreadingHTTPServer]:
    _FakeHandler.responses = {}
    _FakeHandler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}/v1", server
    server.shutdown()


def _chat_ok(text: str, action: str | None = None) -> dict[str, Any]:
    content = json.dumps(
        {"say": text, "action": action, "reason": "test"}, ensure_ascii=False
    )
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }


def test_fallback_to_second_provider(fake_server: tuple[str, ThreadingHTTPServer]) -> None:
    base, _ = fake_server
    _FakeHandler.responses["/v1/chat/completions"] = _chat_ok("Резерв ответил", "report")
    dead = _provider("local", "http://127.0.0.1:9/v1", timeout_sec=1)
    live = _provider("deepseek", base, timeout_sec=3)
    client = BrainClient(_llm([dead, live]), frozenset({"report", "sleep"}))
    result = client.ask(needs={"hunger": 10}, mode="idle")
    assert result.ok
    assert result.provider == "deepseek"
    assert result.say == "Резерв ответил"
    assert result.action == "report"
    assert result.failed_providers == ("local",)


def test_first_provider_wins(fake_server: tuple[str, ThreadingHTTPServer]) -> None:
    base, _ = fake_server
    _FakeHandler.responses["/v1/chat/completions"] = _chat_ok("Локальная реплика")
    local = _provider("local", base, timeout_sec=3)
    # мёртвый резерв — не должен вызываться
    dead = _provider("deepseek", "http://127.0.0.1:9/v1", timeout_sec=1)
    client = BrainClient(_llm([local, dead]), frozenset({"report"}))
    result = client.ask(needs={"hunger": 10}, mode="idle")
    assert result.ok
    assert result.provider == "local"
    assert result.failed_providers == ()
    assert len(_FakeHandler.hits) == 1


def test_all_dead_uses_template() -> None:
    dead1 = _provider("local", "http://127.0.0.1:9/v1", timeout_sec=1)
    dead2 = _provider("deepseek", "http://127.0.0.1:9/v1", timeout_sec=1)
    client = BrainClient(_llm([dead1, dead2]), frozenset({"report"}))
    result = client.ask(needs={"hunger": 10}, mode="idle")
    assert not result.ok
    assert result.from_template
    assert result.say
    assert result.provider is None


def test_unknown_action_dropped(fake_server: tuple[str, ThreadingHTTPServer]) -> None:
    base, _ = fake_server
    _FakeHandler.responses["/v1/chat/completions"] = _chat_ok("Ок", "hack_system")
    client = BrainClient(
        _llm([_provider("local", base)]), frozenset({"report", "sleep"})
    )
    result = client.ask(needs={}, mode="idle")
    assert result.ok
    assert result.action is None


def test_load_env_file_no_overwrite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "t.env"
    path.write_text("FOO=fromfile\nBAR=keep\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "existing")
    monkeypatch.delenv("BAR", raising=False)
    load_env_file(path)
    import os

    assert os.environ["FOO"] == "existing"
    assert os.environ["BAR"] == "keep"


def test_providers_from_config(tmp_path) -> None:
    from pathlib import Path

    import yaml

    from fly_pet.config import load_config

    repo = Path(__file__).resolve().parents[1]
    settings = {
        "llm": {
            "enabled": True,
            "providers": [
                {
                    "name": "local",
                    "base_url": "http://127.0.0.1:1/v1",
                    "model": "x",
                    "timeout_sec": 2,
                    "api_key_env": None,
                },
                {
                    "name": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "timeout_sec": 30,
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
            ],
            "max_tokens": 128,
            "temperature": 0.2,
            "phrase_ttl_sec": 4,
        }
    }
    (tmp_path / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )
    cfg = load_config(repo_root=repo, data_dir=tmp_path)
    assert cfg.llm.enabled is True
    assert len(cfg.llm.providers) == 2
    assert cfg.llm.providers[0].name == "local"
    assert cfg.llm.providers[0].api_key_env is None
    assert cfg.llm.phrase_ttl_sec == 4.0
