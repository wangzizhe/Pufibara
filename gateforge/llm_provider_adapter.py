"""LLM provider adapter protocol and concrete implementations.

Abstracts the transport layer (HTTP requests, auth, response parsing) for
different LLM providers behind a unified interface. Provider-specific wire
format details are encapsulated in each adapter, keeping the agent core
provider-agnostic.

Provider-specific wire formats are kept behind this adapter boundary.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


# ---- tool-use types ----

@dataclass
class ToolCall:
    """A single tool-call request from the LLM."""
    id: str
    name: str
    arguments: dict
    extra: dict = field(default_factory=dict)


@dataclass
class ToolResponse:
    """Result of a tool-request turn."""
    text: str
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: dict
    reasoning: str = ""



# ---- env bootstrap helpers ----

ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPENAI_MODEL_HINT_PATTERN = re.compile(r"^(gpt|o[0-9]|chatgpt|gpt-5)", re.IGNORECASE)
ANTHROPIC_MODEL_HINT_PATTERN = re.compile(r"^(claude)", re.IGNORECASE)
MINIMAX_MODEL_HINT_PATTERN = re.compile(r"^(minimax)", re.IGNORECASE)
QWEN_MODEL_HINT_PATTERN = re.compile(r"^(qwen|qwq)", re.IGNORECASE)
DEEPSEEK_MODEL_HINT_PATTERN = re.compile(r"^(deepseek)", re.IGNORECASE)
KIMI_MODEL_HINT_PATTERN = re.compile(r"^(kimi|moonshot)", re.IGNORECASE)
GLM_MODEL_HINT_PATTERN = re.compile(r"^(glm|chatglm|zhipu)", re.IGNORECASE)


def _parse_env_assignment(line: str) -> tuple[str, str] | tuple[None, None]:
    text = str(line or "").strip()
    if not text or text.startswith("#"):
        return None, None
    if text.startswith("export "):
        text = text[len("export ") :].strip()
    if "=" not in text:
        return None, None
    key, raw_value = text.split("=", 1)
    key = key.strip()
    if not ENV_KEY_PATTERN.match(key):
        return None, None
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_env_file(path: Path, allowed_keys: set[str] | None = None) -> int:
    if not path.exists():
        return 0
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    loaded = 0
    for line in content.splitlines():
        key, value = _parse_env_assignment(line)
        if not key:
            continue
        if isinstance(allowed_keys, set) and key not in allowed_keys:
            continue
        if str(os.getenv(key) or "").strip():
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _bootstrap_env_from_repo(allowed_keys: set[str] | None = None) -> int:
    if str(os.getenv("GATEFORGE_DISABLE_ENV_BOOTSTRAP") or "").strip() == "1":
        return 0
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [Path.cwd() / ".env", repo_root / ".env"]
    loaded = 0
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        loaded += _load_env_file(path, allowed_keys=allowed_keys)
    return loaded


# ---- provider config ----

@dataclass
class LLMProviderConfig:
    """Configuration for an LLM provider connection."""
    provider_name: str
    model: str
    api_key: str
    temperature: float = 0.1
    timeout_sec: float = 120.0
    extra: dict = field(default_factory=dict)


QWEN_REPAIR_PROFILE_PROMPT = (
    "Return the requested response using the configured schema.\n"
)


# ---- provider adapter protocol ----

class LLMProviderAdapter(Protocol):
    """Protocol for LLM provider transport adapters.

    Each adapter encapsulates the wire format (endpoint URL, request payload,
    auth mechanism, response parsing) for a specific LLM provider. The agent
    core calls send_text_request with a prompt and gets back response text.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'gemini', 'openai')."""
        ...

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        """Send a text prompt to the LLM and return (response_text, error).

        Returns:
            Tuple of (response_text, error_string).
            Empty error string means success.
        """
        ...

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        """Send a conversation with tool definitions and return result.

        Implementations should parse either a text-only response or tool-call
        response from the provider and populate ToolResponse accordingly.

        Returns:
            Tuple of (ToolResponse | None, error_string).
            Empty error string means success.
        """
        ...


# ---- Gemini adapter ----

class GeminiProviderAdapter:
    """Transport adapter for the Google Gemini API."""

    @property
    def provider_name(self) -> str:
        return "gemini"

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.model}:generateContent?key={urllib.parse.quote(config.api_key)}"
        )
        req_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": config.temperature,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "gemini_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"gemini_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"gemini_service_unavailable:{code}:{body[:180]}"
            return "", f"gemini_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"gemini_url_error:{exc.reason}"

        candidates = response_payload.get("candidates", [])
        if not candidates:
            return "", "gemini_no_candidates"
        parts = candidates[0].get("content", {}).get("parts", [])
        # Gemini 2.5 Flash thinking mode: skip thought=true parts, take first non-thought part
        text = ""
        for part in parts:
            if not part.get("thought", False):
                text = part.get("text", "")
                break
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.model}:generateContent?key={urllib.parse.quote(config.api_key)}"
        )
        req_payload: dict = {
            "contents": _messages_to_gemini_contents(messages),
            "tools": _tools_to_gemini(tools),
            "generationConfig": {
                "temperature": config.temperature,
            },
        }
        system_prompt = _extract_system_from_messages(messages)
        if system_prompt:
            req_payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        req = urllib.request.Request(
            url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "gemini_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"gemini_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"gemini_service_unavailable:{code}:{body[:180]}"
            return None, f"gemini_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"gemini_url_error:{exc.reason}"

        return _parse_gemini_tool_response(response_payload)


# ---- OpenAI adapter ----

class OpenAIProviderAdapter:
    """Transport adapter for the OpenAI API."""

    @property
    def provider_name(self) -> str:
        return "openai"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        """Extract text content from an OpenAI API response."""
        if isinstance(payload.get("output_text"), str) and str(payload.get("output_text")).strip():
            return str(payload.get("output_text"))
        output = payload.get("output") if isinstance(payload.get("output"), list) else []
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content") if isinstance(item.get("content"), list) else []
            for row in content:
                if not isinstance(row, dict):
                    continue
                if isinstance(row.get("text"), str):
                    parts.append(str(row.get("text")))
                elif isinstance(row.get("value"), str):
                    parts.append(str(row.get("value")))
        return "\n".join([x for x in parts if x.strip()]).strip()

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        req_payload = {
            "model": config.model,
            "input": prompt,
            "temperature": config.temperature,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "openai_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"openai_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"openai_service_unavailable:{code}:{body[:180]}"
            return "", f"openai_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"openai_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        req_payload: dict = {
            "model": config.model,
            "input": list(messages),
            "temperature": config.temperature,
            "tools": list(tools),
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "openai_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"openai_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"openai_service_unavailable:{code}:{body[:180]}"
            return None, f"openai_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"openai_url_error:{exc.reason}"

        return _parse_openai_tool_response(response_payload)


class QwenProviderAdapter:
    """Transport adapter for Alibaba Bailian OpenAI-compatible Responses API."""

    @property
    def provider_name(self) -> str:
        return "qwen"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        return OpenAIProviderAdapter._extract_response_text(payload)

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        base_url = str(
            config.extra.get("dashscope_base_url")
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip().rstrip("/")
        req_payload = {
            "model": config.model,
            "input": prompt,
            "temperature": config.temperature,
            "enable_thinking": bool(config.extra.get("enable_thinking", False)),
        }
        thinking_budget = config.extra.get("thinking_budget")
        if thinking_budget not in {None, ""}:
            try:
                req_payload["thinking_budget"] = int(thinking_budget)
            except Exception:
                pass
        req = urllib.request.Request(
            f"{base_url}/responses",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "qwen_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"qwen_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"qwen_service_unavailable:{code}:{body[:180]}"
            return "", f"qwen_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"qwen_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""


class DeepSeekProviderAdapter:
    """Transport adapter for the DeepSeek OpenAI-compatible Chat API."""

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices:
            return ""
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message.get("content") or "")
        return ""

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        base_url = str(
            config.extra.get("deepseek_base_url")
            or os.getenv("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        ).strip().rstrip("/")
        req_payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "stream": False,
        }
        max_tokens = config.extra.get("max_tokens")
        if max_tokens not in {None, ""}:
            try:
                req_payload["max_tokens"] = int(max_tokens)
            except Exception:
                pass
        thinking = str(config.extra.get("thinking") or "").strip().lower()
        if thinking in {"enabled", "disabled"}:
            req_payload["thinking"] = {"type": thinking}
        response_format = config.extra.get("response_format")
        if isinstance(response_format, dict):
            req_payload["response_format"] = response_format
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "deepseek_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"deepseek_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"deepseek_service_unavailable:{code}:{body[:180]}"
            return "", f"deepseek_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"deepseek_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        base_url = str(
            config.extra.get("deepseek_base_url")
            or os.getenv("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        ).strip().rstrip("/")
        openai_tools = _tools_to_openai_chat(tools)
        req_payload = {
            "model": config.model,
            "messages": list(messages),
            "tools": openai_tools,
            "tool_choice": "auto",
            "temperature": config.temperature,
            "stream": False,
        }
        max_tokens = config.extra.get("max_tokens")
        if max_tokens not in {None, ""}:
            try:
                req_payload["max_tokens"] = int(max_tokens)
            except Exception:
                pass
        thinking = str(config.extra.get("thinking") or "").strip().lower()
        if thinking in {"enabled", "disabled"}:
            req_payload["thinking"] = {"type": thinking}
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "deepseek_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"deepseek_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"deepseek_service_unavailable:{code}:{body[:180]}"
            return None, f"deepseek_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"deepseek_url_error:{exc.reason}"

        return _parse_chat_tool_response(response_payload)


# ---- Anthropic adapter ----

class AnthropicProviderAdapter:
    """Transport adapter for the Anthropic Messages API."""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @staticmethod
    def _apply_sampling_config(payload: dict, config: LLMProviderConfig) -> None:
        """Project temperature only when the selected model accepts it."""
        if not bool(config.extra.get("omit_temperature", False)):
            payload["temperature"] = config.temperature

    @staticmethod
    def _apply_prompt_caching(payload: dict, config: LLMProviderConfig) -> None:
        """Enable provider-scoped caching for stable request prefixes."""
        mode = str(config.extra.get("prompt_cache_mode") or "").strip().lower()
        if not mode:
            return
        if mode != "automatic_ephemeral_5m":
            raise ValueError(f"anthropic_prompt_cache_mode_invalid:{mode}")
        payload["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
        tools = payload.get("tools")
        if isinstance(tools, list) and tools and isinstance(tools[-1], dict):
            tools[-1]["cache_control"] = {"type": "ephemeral", "ttl": "5m"}

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "text" and isinstance(item.get("text"), str):
                parts.append(str(item.get("text")))
        return "\n".join([x for x in parts if x.strip()]).strip()

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        req_payload = {
            "model": config.model,
            "max_tokens": int(config.extra.get("max_tokens") or 4096),
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        self._apply_sampling_config(req_payload, config)
        self._apply_prompt_caching(req_payload, config)
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "anthropic_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"anthropic_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"anthropic_service_unavailable:{code}:{body[:180]}"
            return "", f"anthropic_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"anthropic_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        anthropic_tools = _tools_to_anthropic(tools)
        anthropic_messages = _messages_to_anthropic(messages)
        system_prompt = _extract_system_from_messages(messages)
        req_payload: dict = {
            "model": config.model,
            "max_tokens": int(config.extra.get("max_tokens") or 4096),
            "messages": anthropic_messages,
            "tools": anthropic_tools,
        }
        self._apply_sampling_config(req_payload, config)
        self._apply_prompt_caching(req_payload, config)
        if system_prompt:
            req_payload["system"] = system_prompt
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "anthropic_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"anthropic_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"anthropic_service_unavailable:{code}:{body[:180]}"
            return None, f"anthropic_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"anthropic_url_error:{exc.reason}"

        return _parse_anthropic_tool_response(response_payload)


class MiniMaxProviderAdapter:
    """Transport adapter for the MiniMax Anthropic-compatible Messages API."""

    @property
    def provider_name(self) -> str:
        return "minimax"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        return AnthropicProviderAdapter._extract_response_text(payload)

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        base_url = str(
            config.extra.get("anthropic_base_url")
            or os.getenv("ANTHROPIC_BASE_URL")
            or "https://api.minimaxi.com/anthropic"
        ).strip().rstrip("/")
        req_payload = {
            "model": config.model,
            "max_tokens": int(config.extra.get("max_tokens") or 8192),
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "temperature": config.temperature,
        }
        system_prompt = str(config.extra.get("system_prompt") or "").strip()
        if system_prompt:
            req_payload["system"] = system_prompt
        req = urllib.request.Request(
            f"{base_url}/v1/messages",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "minimax_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"minimax_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"minimax_service_unavailable:{code}:{body[:180]}"
            return "", f"minimax_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"minimax_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""


class KimiProviderAdapter:
    """Transport adapter for the Kimi (Moonshot) OpenAI-compatible Chat API."""

    @property
    def provider_name(self) -> str:
        return "kimi"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices:
            return ""
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message.get("content") or "")
        return ""

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        base_url = str(
            config.extra.get("kimi_base_url")
            or os.getenv("KIMI_BASE_URL")
            or "https://api.moonshot.cn/v1"
        ).strip().rstrip("/")
        req_payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "kimi_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"kimi_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"kimi_service_unavailable:{code}:{body[:180]}"
            return "", f"kimi_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"kimi_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        base_url = str(
            config.extra.get("kimi_base_url")
            or os.getenv("KIMI_BASE_URL")
            or "https://api.moonshot.cn/v1"
        ).strip().rstrip("/")
        openai_tools = _tools_to_openai_chat(tools)
        req_payload = {
            "model": config.model,
            "messages": list(messages),
            "tools": openai_tools,
            "tool_choice": "auto",
            "temperature": config.temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "kimi_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"kimi_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"kimi_service_unavailable:{code}:{body[:180]}"
            return None, f"kimi_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"kimi_url_error:{exc.reason}"

        return _parse_chat_tool_response(response_payload)


class GLMProviderAdapter:
    """Transport adapter for the Zhipu GLM OpenAI-compatible Chat API."""

    @property
    def provider_name(self) -> str:
        return "glm"

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices:
            return ""
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message.get("content") or "")
        return ""

    def send_text_request(
        self,
        prompt: str,
        config: LLMProviderConfig,
    ) -> tuple[str, str]:
        base_url = str(
            config.extra.get("glm_base_url")
            or os.getenv("GLM_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4"
        ).strip().rstrip("/")
        req_payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return "", "glm_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return "", f"glm_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return "", f"glm_service_unavailable:{code}:{body[:180]}"
            return "", f"glm_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return "", f"glm_url_error:{exc.reason}"

        text = self._extract_response_text(response_payload)
        return text, ""

    def send_tool_request(
        self,
        messages: list[dict],
        tools: list[dict],
        config: LLMProviderConfig,
    ) -> tuple[ToolResponse | None, str]:
        base_url = str(
            config.extra.get("glm_base_url")
            or os.getenv("GLM_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4"
        ).strip().rstrip("/")
        openai_tools = _tools_to_openai_chat(tools)
        req_payload = {
            "model": config.model,
            "messages": list(messages),
            "tools": openai_tools,
            "tool_choice": "auto",
            "temperature": config.temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError:
            return None, "glm_request_timeout"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            finally:
                exc.close()
            code = int(exc.code)
            if code == 429:
                return None, f"glm_rate_limited:{body[:180]}"
            if code in (502, 503, 504):
                return None, f"glm_service_unavailable:{code}:{body[:180]}"
            return None, f"glm_http_error:{code}:{body[:180]}"
        except urllib.error.URLError as exc:
            return None, f"glm_url_error:{exc.reason}"

        return _parse_chat_tool_response(response_payload)


# ---- adapter factory ----

def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in tools:
        out.append({
            "name": str(t.get("name") or ""),
            "description": str(t.get("description") or ""),
            "input_schema": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _tools_to_openai_chat(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in tools:
        item: dict = {
            "type": "function",
            "function": {
                "name": str(t.get("name") or ""),
                "description": str(t.get("description") or ""),
                "parameters": t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        out.append(item)
    return out


def _tools_to_gemini(tools: list[dict]) -> list[dict]:
    declarations: list[dict] = []
    for t in tools:
        declarations.append({
            "name": str(t.get("name") or ""),
            "description": str(t.get("description") or ""),
            "parameters": t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}},
        })
    return [{"functionDeclarations": declarations}]


def _messages_to_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content")
        tool_calls_list = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")
        if role == "tool" and tool_call_id:
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": str(tool_call_id),
                    "content": str(content or ""),
                }],
            })
        elif role == "assistant" and tool_calls_list:
            content_blocks: list[dict] = []
            if isinstance(content, str) and content.strip():
                content_blocks.append({"type": "text", "text": str(content)})
            for tc in tool_calls_list:
                tc_name, tc_args = _normalize_tool_call(tc)
                content_blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or ""),
                    "name": tc_name,
                    "input": tc_args,
                })
            out.append({"role": "assistant", "content": content_blocks})
        elif role == "system":
            continue
        else:
            out.append({"role": role, "content": [{"type": "text", "text": str(content or "")}]})
    return out


def _messages_to_gemini_contents(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    tool_call_names: dict[str, str] = {}
    tool_call_extras: dict[str, dict] = {}
    for msg in messages:
        role = str(msg.get("role") or "user")
        if role == "system":
            continue
        content = msg.get("content")
        tool_calls_list = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
        tool_call_id = str(msg.get("tool_call_id") or "")
        if role == "assistant":
            parts: list[dict] = []
            if isinstance(content, str) and content.strip():
                parts.append({"text": str(content)})
            for tc in tool_calls_list:
                if not isinstance(tc, dict):
                    continue
                tc_name, tc_args = _normalize_tool_call(tc)
                tc_id = str(tc.get("id") or "")
                if tc_id:
                    tool_call_names[tc_id] = tc_name
                    extra = tc.get("extra") if isinstance(tc.get("extra"), dict) else {}
                    if extra:
                        tool_call_extras[tc_id] = extra
                function_call_part = {"functionCall": {"name": tc_name, "args": tc_args}}
                thought_signature = tool_call_extras.get(tc_id, {}).get("thoughtSignature")
                if isinstance(thought_signature, str) and thought_signature:
                    function_call_part["thoughtSignature"] = thought_signature
                parts.append(function_call_part)
            if parts:
                out.append({"role": "model", "parts": parts})
        elif role == "tool" and tool_call_id:
            name = tool_call_names.get(tool_call_id, tool_call_id)
            thought_signature = tool_call_extras.get(tool_call_id, {}).get("thoughtSignature")
            part: dict = {
                "functionResponse": {
                    "name": name,
                    "response": {"result": str(content or "")},
                }
            }
            if isinstance(thought_signature, str) and thought_signature:
                part["thoughtSignature"] = thought_signature
            out.append({
                "role": "user",
                "parts": [part],
            })
        else:
            out.append({"role": "user", "parts": [{"text": str(content or "")}]})
    return out


def _extract_system_from_messages(messages: list[dict]) -> str:
    for msg in messages:
        if str(msg.get("role") or "") == "system":
            return str(msg.get("content") or "")
    return ""


def _normalize_tool_call(tc: dict) -> tuple[str, dict]:
    name = str(tc.get("name") or "")
    args: dict = {}
    if not name:
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str(fn.get("name") or "")
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            args = _safe_parse_json(raw_args)
    if isinstance(tc.get("arguments"), dict):
        args = tc.get("arguments")
    elif isinstance(tc.get("arguments"), str) and not args:
        args = _safe_parse_json(str(tc.get("arguments")))
    return name, args


def _parse_anthropic_tool_response(payload: dict) -> tuple[ToolResponse, str]:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    content = payload.get("content") if isinstance(payload.get("content"), list) else []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            text_parts.append(str(item.get("text")))
        elif item.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments=item.get("input") if isinstance(item.get("input"), dict) else {},
            ))
    text = "\n".join(text_parts).strip()
    finish_reason = str(payload.get("stop_reason") or "")
    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = {
        "input_tokens": int(usage_raw.get("input_tokens") or 0),
        "output_tokens": int(usage_raw.get("output_tokens") or 0),
    }
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return ToolResponse(text=text, tool_calls=tool_calls, finish_reason=finish_reason, usage=usage), ""


def _parse_chat_tool_response(payload: dict) -> tuple[ToolResponse, str]:
    choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    text = ""
    tool_calls: list[ToolCall] = []
    finish_reason = ""
    reasoning = ""
    if choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(msg.get("content"), str):
            text = str(msg.get("content") or "")
        if isinstance(msg.get("reasoning_content"), str):
            reasoning = str(msg.get("reasoning_content"))
        finish_reason = str(choices[0].get("finish_reason") or "")
        raw_tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            tool_calls.append(ToolCall(
                id=str(tc.get("id") or ""),
                name=str(fn.get("name") or ""),
                arguments=_safe_parse_json(str(fn.get("arguments") or "{}")),
            ))
    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = {
        "input_tokens": int(usage_raw.get("input_tokens") or int(usage_raw.get("prompt_tokens") or 0)),
        "output_tokens": int(usage_raw.get("output_tokens") or int(usage_raw.get("completion_tokens") or 0)),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    response = ToolResponse(text=text, tool_calls=tool_calls, finish_reason=finish_reason, usage=usage, reasoning=reasoning)
    return response, ""


def _parse_openai_tool_response(payload: dict) -> tuple[ToolResponse, str]:
    text = OpenAIProviderAdapter._extract_response_text(payload)
    tool_calls: list[ToolCall] = []
    output = payload.get("output") if isinstance(payload.get("output"), list) else []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            tool_calls.append(ToolCall(
                id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments=_safe_parse_json(str(item.get("arguments") or "{}")),
            ))
        elif item.get("type") == "message":
            pass
    finish_reason = str(payload.get("status") or "")
    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = {
        "input_tokens": int(usage_raw.get("input_tokens") or 0),
        "output_tokens": int(usage_raw.get("output_tokens") or 0),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    return ToolResponse(text=text, tool_calls=tool_calls, finish_reason=finish_reason, usage=usage), ""


def _parse_gemini_tool_response(payload: dict) -> tuple[ToolResponse, str]:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason = ""
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    if candidates:
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = str(first.get("finishReason") or "")
        content = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        for idx, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str) and not part.get("thought", False):
                text_parts.append(str(part.get("text") or ""))
            function_call = part.get("functionCall") if isinstance(part.get("functionCall"), dict) else None
            if isinstance(function_call, dict):
                name = str(function_call.get("name") or "")
                args = function_call.get("args") if isinstance(function_call.get("args"), dict) else {}
                extra: dict = {}
                if isinstance(part.get("thoughtSignature"), str):
                    extra["thoughtSignature"] = str(part.get("thoughtSignature") or "")
                tool_calls.append(ToolCall(
                    id=f"gemini_call_{idx}_{name}",
                    name=name,
                    arguments=args,
                    extra=extra,
                ))
    usage_raw = payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {}
    usage = {
        "input_tokens": int(usage_raw.get("promptTokenCount") or 0),
        "output_tokens": int(usage_raw.get("candidatesTokenCount") or 0),
        "total_tokens": int(usage_raw.get("totalTokenCount") or 0),
    }
    return ToolResponse(
        text="\n".join([x for x in text_parts if x.strip()]).strip(),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
    ), ""


def _safe_parse_json(raw: str) -> dict:
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _build_openai_tool_messages(
    system_prompt: str,
    user_messages: list[dict],
) -> list[dict]:
    items: list[dict] = []
    if system_prompt.strip():
        items.append({"role": "system", "content": system_prompt})
    for msg in user_messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content")
        tool_calls_list = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")
        if role == "tool" and tool_call_id:
            items.append({
                "type": "function_call_output",
                "call_id": str(tool_call_id),
                "output": str(content or ""),
            })
        elif role == "assistant" and tool_calls_list:
            for tc in tool_calls_list:
                items.append({
                    "type": "function_call",
                    "call_id": str(tc.get("id") or ""),
                    "name": str(tc.get("name") or ""),
                    "arguments": str(tc.get("arguments") or "{}"),
                })
        else:
            items.append({"role": role, "content": str(content or "")})
    return items


_ADAPTERS: dict[str, type] = {
    "gemini": GeminiProviderAdapter,
    "openai": OpenAIProviderAdapter,
    "qwen": QwenProviderAdapter,
    "deepseek": DeepSeekProviderAdapter,
    "anthropic": AnthropicProviderAdapter,
    "minimax": MiniMaxProviderAdapter,
    "kimi": KimiProviderAdapter,
    "glm": GLMProviderAdapter,
}


def resolve_provider_adapter(
    requested_backend: str,
) -> tuple[LLMProviderAdapter, LLMProviderConfig]:
    """Resolve an LLM provider adapter and its configuration.

    Handles env bootstrap, API key lookup, model name resolution, and
    provider auto-detection. Returns an adapter instance and its config.

    Args:
        requested_backend: Explicit backend name ('gemini', 'openai', 'rule')
            or empty string for auto-detection.

    Returns:
        Tuple of (adapter, config). For 'rule' backend, returns a config
        with empty model/api_key so the caller can fall back to rule-based
        logic.

    Raises:
        ValueError: If the requested backend is unknown.
    """
    _bootstrap_env_from_repo(
        allowed_keys={
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "MINIMAX_API_KEY",
            "DASHSCOPE_API_KEY",
            "QWEN_API_KEY",
            "DEEPSEEK_API_KEY",
            "KIMI_API_KEY",
            "GLM_API_KEY",
            "DASHSCOPE_BASE_URL",
            "DEEPSEEK_BASE_URL",
            "ANTHROPIC_BASE_URL",
            "KIMI_BASE_URL",
            "GLM_BASE_URL",
            "LLM_MODEL",
            "GATEFORGE_GEMINI_MODEL",
            "GEMINI_MODEL",
            "OPENAI_MODEL",
            "ANTHROPIC_MODEL",
            "MINIMAX_MODEL",
            "QWEN_MODEL",
            "DEEPSEEK_MODEL",
            "KIMI_MODEL",
            "KIMI_MODEL_NAME",
            "KIMI_MODEL_MAX_TOKENS",
            "LLM_PROVIDER",
            "GATEFORGE_LIVE_PLANNER_BACKEND",
        }
    )
    requested = str(requested_backend or "").strip().lower()
    if requested == "rule":
        return GeminiProviderAdapter(), LLMProviderConfig(
            provider_name="rule", model="", api_key=""
        )

    model = (
        str(os.getenv("LLM_MODEL") or "").strip()
        or str(os.getenv("OPENAI_MODEL") or "").strip()
        or str(os.getenv("QWEN_MODEL") or "").strip()
        or str(os.getenv("ANTHROPIC_MODEL") or "").strip()
        or str(os.getenv("MINIMAX_MODEL") or "").strip()
        or str(os.getenv("GATEFORGE_GEMINI_MODEL") or "").strip()
        or str(os.getenv("GEMINI_MODEL") or "").strip()
        or str(os.getenv("DEEPSEEK_MODEL") or "").strip()
        or str(os.getenv("KIMI_MODEL") or "").strip()
        or str(os.getenv("KIMI_MODEL_NAME") or "").strip()
        or str(os.getenv("GLM_MODEL") or "").strip()
    )
    if not model:
        raise ValueError("missing_llm_model")
    explicit = requested if requested in {"gemini", "openai", "anthropic", "qwen", "deepseek", "minimax", "kimi", "glm"} else ""
    if not explicit:
        explicit = str(
            os.getenv("LLM_PROVIDER")
            or os.getenv("GATEFORGE_LIVE_PLANNER_BACKEND")
            or ""
        ).strip().lower()
    if explicit not in {"gemini", "openai", "anthropic", "qwen", "deepseek", "minimax", "kimi", "glm"}:
        if OPENAI_MODEL_HINT_PATTERN.search(model):
            explicit = "openai"
        elif QWEN_MODEL_HINT_PATTERN.search(model):
            explicit = "qwen"
        elif DEEPSEEK_MODEL_HINT_PATTERN.search(model):
            explicit = "deepseek"
        elif ANTHROPIC_MODEL_HINT_PATTERN.search(model):
            explicit = "anthropic"
        elif MINIMAX_MODEL_HINT_PATTERN.search(model):
            explicit = "minimax"
        elif KIMI_MODEL_HINT_PATTERN.search(model):
            explicit = "kimi"
        elif GLM_MODEL_HINT_PATTERN.search(model):
            explicit = "glm"
        elif "gemini" in model.lower():
            explicit = "gemini"
        else:
            raise ValueError(f"unsupported_llm_model:{model}")

    if explicit == "openai":
        api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    elif explicit == "qwen":
        api_key = str(
            os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or ""
        ).strip()
    elif explicit == "deepseek":
        api_key = str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
    elif explicit == "anthropic":
        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
    elif explicit == "minimax":
        api_key = str(
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("MINIMAX_API_KEY")
            or ""
        ).strip()
    elif explicit == "kimi":
        api_key = str(os.getenv("KIMI_API_KEY") or "").strip()
    elif explicit == "glm":
        api_key = str(os.getenv("GLM_API_KEY") or "").strip()
    else:
        api_key = str(
            os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
        ).strip()
    if not api_key:
        raise ValueError(f"missing_{explicit}_api_key")

    adapter_cls = _ADAPTERS.get(explicit)
    if adapter_cls is None:
        raise ValueError(f"Unknown LLM provider: {explicit}")

    from .llm_budget import _to_float_env

    config = LLMProviderConfig(
        provider_name=explicit,
        model=model,
        api_key=api_key,
        timeout_sec=max(
            1.0, _to_float_env("GATEFORGE_AGENT_LIVE_LLM_REQUEST_TIMEOUT_SEC", 120.0)
        ),
        extra={
            "dashscope_base_url": str(os.getenv("DASHSCOPE_BASE_URL") or "").strip(),
            "enable_thinking": False if explicit == "qwen" else "",
            "prompt_prefix": QWEN_REPAIR_PROFILE_PROMPT if explicit == "qwen" else "",
            "deepseek_base_url": str(os.getenv("DEEPSEEK_BASE_URL") or "").strip(),
            "thinking": "",
            "response_format": {"type": "json_object"} if explicit == "deepseek" else "",
            "anthropic_base_url": str(os.getenv("ANTHROPIC_BASE_URL") or "").strip(),
            "omit_temperature": (
                explicit == "anthropic"
                and str(model).strip().lower() == "claude-sonnet-5"
            ),
            "prompt_cache_mode": (
                "automatic_ephemeral_5m" if explicit == "anthropic" else ""
            ),
            "kimi_base_url": str(os.getenv("KIMI_BASE_URL") or "").strip(),
            "glm_base_url": str(os.getenv("GLM_BASE_URL") or "").strip(),
            "max_tokens": (
                int(os.getenv("KIMI_MODEL_MAX_TOKENS") or "8192")
                if explicit == "kimi"
                else 8192 if explicit in {"minimax", "deepseek"} else 1024
            ),
            "system_prompt": (
                "You must return a final text block containing only one JSON object "
                "with keys patched_model_text and rationale."
                if explicit == "minimax"
                else ""
            ),
        },
    )
    return adapter_cls(), config
