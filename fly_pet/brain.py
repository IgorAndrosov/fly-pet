"""Мозг питомца: OpenAI-совместимый chat/completions с цепочкой провайдеров."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from fly_pet.config import LlmConfig, LlmProviderConfig

logger = logging.getLogger("fly_pet")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM = (
    "Ты — муха-питомец на рабочем столе Windows. "
    "Ответь строго одним JSON-объектом без markdown: "
    '{"say":"...","action":null|"имя","reason":"..."}. '
    "say — одна короткая русская фраза, не больше 12 слов. "
    "action — только из белого списка или null. "
    "Никакого другого текста."
)


@dataclass(frozen=True)
class BrainResult:
    """Итог одного запроса к мозгу."""

    ok: bool
    say: str
    action: str | None
    provider: str | None
    elapsed_ms: int
    failed_providers: tuple[str, ...]
    from_template: bool = False


class BrainClient:
    """Клиент цепочки провайдеров; потокобезопасен для одного вызова ask()."""

    def __init__(self, llm: LlmConfig, allowed_actions: frozenset[str]) -> None:
        self._llm = llm
        self._allowed = allowed_actions
        self._fallback_warned = False

    def ask(self, *, needs: dict[str, float], mode: str) -> BrainResult:
        """Проходит провайдеров по порядку; исключения наружу не выпускает."""
        if not self._llm.enabled or not self._llm.providers:
            return self._template("мозг выключен")

        failed: list[str] = []
        user = (
            f"Состояние: mode={mode}, "
            f"hunger={needs.get('hunger', 0):.0f}, "
            f"energy={needs.get('energy', 0):.0f}, "
            f"mood={needs.get('mood', 0):.0f}, "
            f"attention={needs.get('attention', 0):.0f}. "
            f"Белый список действий: {sorted(self._allowed)}. "
            "Скажи реплику и при желании выбери действие."
        )

        for provider in self._llm.providers:
            t0 = time.perf_counter()
            try:
                raw = self._call_provider(provider, user)
                parsed = self._parse_reply(raw)
            except Exception as exc:  # noqa: BLE001 — любой сбой → следующий
                failed.append(provider.name)
                logger.debug(
                    "провайдер %s не ответил: %s: %s",
                    provider.name,
                    type(exc).__name__,
                    exc,
                )
                self._maybe_warn_fallback(failed)
                continue

            elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
            say = (parsed.get("say") or "").strip()
            if not say:
                failed.append(provider.name)
                self._maybe_warn_fallback(failed)
                continue

            action = parsed.get("action")
            if action is not None:
                action_str = str(action).strip()
                if action_str in {"", "null", "None"}:
                    action = None
                elif action_str not in self._allowed:
                    logger.info(
                        "действие «%s» отброшено (нет в белом списке)", action_str
                    )
                    action = None
                else:
                    action = action_str

            if not failed:
                self._fallback_warned = False
            else:
                self._maybe_warn_fallback(failed)

            logger.info(
                "ответил провайдер %s (%s мс)", provider.name, elapsed_ms
            )
            return BrainResult(
                ok=True,
                say=say,
                action=action if isinstance(action, str) or action is None else None,
                provider=provider.name,
                elapsed_ms=elapsed_ms,
                failed_providers=tuple(failed),
            )

        logger.warning("ни один LLM-провайдер не ответил, шаблонная реплика")
        return self._template("все провайдеры молчат", failed=tuple(failed))

    def _maybe_warn_fallback(self, failed: list[str]) -> None:
        if self._fallback_warned or not failed:
            return
        if len(failed) < 1:
            return
        # Предупреждаем один раз на серию, когда уходим с первого провайдера.
        if failed[0] == self._llm.providers[0].name:
            logger.warning(
                "провайдер %s не ответил, переходим к резерву",
                failed[0],
            )
            self._fallback_warned = True

    def _template(
        self, reason: str, failed: tuple[str, ...] = ()
    ) -> BrainResult:
        from fly_pet.phrases import pick

        phrase = pick("brain_fail") or pick("report") or "Жужжу без облака."
        logger.debug("шаблон мозга (%s): %s", reason, phrase)
        return BrainResult(
            ok=False,
            say=phrase,
            action=None,
            provider=None,
            elapsed_ms=0,
            failed_providers=failed,
            from_template=True,
        )

    def _call_provider(self, provider: LlmProviderConfig, user: str) -> str:
        base = provider.base_url.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if provider.api_key_env:
            import os

            key = os.environ.get(provider.api_key_env) or ""
            if key:
                headers["Authorization"] = f"Bearer {key}"
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._llm.max_tokens,
            "temperature": self._llm.temperature,
        }
        with httpx.Client(timeout=provider.timeout_sec) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("пустой choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        # tool_calls: взять text из say_phrase / arguments
        tool_calls = message.get("tool_calls") or []
        for call in tool_calls:
            fn = (call or {}).get("function") or {}
            args_raw = fn.get("arguments") or ""
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                continue
            if isinstance(args, dict):
                text = args.get("text") or args.get("say")
                if text:
                    return json.dumps(
                        {"say": str(text), "action": args.get("action"), "reason": "tool"},
                        ensure_ascii=False,
                    )
        raise RuntimeError("пустой/неразборный ответ")

    def _parse_reply(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        match = _JSON_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        # голый текст = реплика
        return {"say": text.splitlines()[0].strip()[:200], "action": None, "reason": "plain"}
