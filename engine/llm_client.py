import json
import re
from pathlib import Path
from typing import List, Dict, Optional
from urllib import request, error


class LLMClient:
    """Simple connector to an OpenAI-compatible endpoint (e.g., OpenRouter)."""

    def __init__(self, config_path: Optional[Path] = None, path: Optional[Path] = None):
        # Support both param names for convenience
        config_path = config_path or path or Path("config/llm.json")
        cfg = {}
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            # Provide a clearer message and safe defaults for offline/dev runs
            print(f"[LLMClient] Config file not found at '{config_path}'. Using safe defaults.")
            cfg = {
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "model": "openai/gpt-4o-mini",
                "max_output_tokens": 256,
                "extra_headers": {},
            }
        except Exception as e:
            print(f"[LLMClient] Failed to load config '{config_path}': {e}. Using safe defaults.")
            cfg = {
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "model": "openai/gpt-4o-mini",
                "max_output_tokens": 256,
                "extra_headers": {},
            }
        self.endpoint = cfg.get("endpoint")
        self.model = cfg.get("model")
        # Deprecated: max_context (was incorrectly used for completion length)
        # New: max_output_tokens controls completion length only.
        self.max_output_tokens = cfg.get("max_output_tokens", cfg.get("max_context", -1))
        self.api_key = cfg.get("api_key")
        self.extra_headers = cfg.get("extra_headers", {})
        # Optional debug flag to control verbose logging and request/response dumps
        self.debug = bool(cfg.get("debug", False))

    def chat(self, messages: List[Dict[str, str]]) -> str:
        if isinstance(self.endpoint, str) and "openrouter.ai" in self.endpoint:
            if not self.api_key:
                raise RuntimeError("OpenRouter requires an api_key in config/llm.json.")
        # Request the model to ONLY return a JSON object; no prose.
        # Add an assistant-side system instruction to enforce JSON output.
        sys_guard = {
            "role": "system",
            "content": "Output must be ONLY a single JSON object, no prose, no code fences. If you produce hidden reasoning, wrap it in <think>...</think> BEFORE the JSON."
        }
        # Prepend guard if not already present
        msgs = [sys_guard] + messages

        payload = {
            "model": self.model,
            "messages": msgs,
        }
        # Only use OpenAI-style response_format when talking to providers that support it (e.g., OpenRouter)
        if isinstance(self.endpoint, str) and "openrouter.ai" in self.endpoint:
            payload["response_format"] = {"type": "json_object"}
        # Enable debug logging of raw requests/responses
        debug = getattr(self, "debug", False)
        if self.max_output_tokens != -1:
            # Limit completion length only (does not affect prompt size)
            payload["max_tokens"] = self.max_output_tokens

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for k, v in (self.extra_headers or {}).items():
            headers[k] = v

        req = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            if debug:
                # Print outbound request (truncated) for troubleshooting
                print("[LLMClient] Request payload:", json.dumps(payload)[:500])
                # Persist last request for external log readers (e.g., CLI)
                try:
                    with open("llm_last_request.json", "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                # Removed no-op legacy checks for 'max_context'.
            # Allow long-thinking local models: increase timeout substantially
            with request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode()
                if debug:
                    # Print raw response length and first chars; also dump to a file for full inspection
                    print("[LLMClient] Raw response length:", len(raw))
                    print("[LLMClient] Raw response head:", raw[:200].replace("\n","\\n"))
                    # Write the raw response to a temp file for user inspection
                    try:
                        with open("llm_last_response.txt", "w", encoding="utf-8") as f:
                            f.write(raw)
                        print("[LLMClient] Full raw response saved to llm_last_response.txt")
                    except Exception as _e:
                        print("[LLMClient] Failed to write llm_last_response.txt:", _e)
                if not raw or not raw.strip():
                    raise RuntimeError("Empty response from LLM")
                # Some providers (including OpenRouter) support a beta response_format and may still return JSON content in choices.
                data = json.loads(raw)
                if debug:
                    # After successful parse, store structured JSON response for downstream tools
                    try:
                        with open("llm_last_full.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
        except error.HTTPError as e:
            try:
                body = e.read().decode()
            except Exception:
                body = ""
            if debug:
                print("[LLMClient] HTTPError:", e.code, e.reason, body[:1000])
            # Return empty JSON string to keep caller stable but expose details in console
            return "{}"
        except error.URLError as e:
            if debug:
                print("[LLMClient] URLError:", e.reason)
            return "{}"
        except json.JSONDecodeError as e:
            if debug:
                print("[LLMClient] JSONDecodeError on response")
            # As a fallback for non-JSON or HTML error bodies, return a minimal empty JSON command string
            return "{}"

        # Try OpenAI-compatible chat format
        content = None
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            # Fallback: some providers may return a 'content' at top-level or a 'text' key
            if isinstance(data, dict):
                if "content" in data and isinstance(data["content"], str):
                    content = data["content"]
                elif "text" in data and isinstance(data["text"], str):
                    content = data["text"]

        if not isinstance(content, str):
            # Final fallback to empty JSON to keep engine stable
            return "{}"
        return content

    def extract_think(self, text: str) -> Optional[str]:
        """
        Extract the FIRST hidden reasoning block wrapped in <think>...</think> (or <thought>/<reasoning>).
        Returns the inner text trimmed, or None if not present.
        Non-destructive: does not modify any state or files.
        """
        if not isinstance(text, str):
            return None
        m = re.search(r"(?is)<\s*(think|thought|reasoning)\s*>(.*?)<\s*/\s*\1\s*>", text)
        if not m:
            return None
        return (m.group(2) or "").strip()

    def _strip_think_and_extract_json(self, text: str) -> Optional[dict]:
        """
        Remove any <think>...</think> or similar hidden reasoning tags and try to parse JSON.
        If direct parse fails, extract the last JSON object block and parse it.
        More robust to multiple think blocks and surrounding whitespace.
        """
        if not isinstance(text, str):
            return None
        # Normalize newlines/whitespace a bit
        txt = text.strip()
        # Remove ALL hidden reasoning blocks, case-insensitive, including nested appearances
        # Use a loop to remove repeatedly in case multiple blocks occur
        pattern = re.compile(r"(?is)<\s*(think|thought|reasoning)\s*>.*?<\s*/\s*\1\s*>")
        prev = None
        while prev != txt:
            prev = txt
            txt = pattern.sub("", txt)
        cleaned = txt.strip()
        # Try direct JSON first
        try:
            return json.loads(cleaned)
        except Exception:
            pass
        # Fallback: find the last balanced {...} block
        brace_stack: list = []
        start_idx = -1
        last_json = None
        for i, ch in enumerate(cleaned):
            if ch == "{":
                if not brace_stack:
                    start_idx = i
                brace_stack.append("{")
            elif ch == "}":
                if brace_stack:
                    brace_stack.pop()
                    if not brace_stack and start_idx != -1:
                        candidate = cleaned[start_idx:i+1]
                        last_json = candidate
                        start_idx = -1
        if last_json:
            try:
                return json.loads(last_json)
            except Exception:
                return None
        return None

    def parse_command(self, user_input: str, system_prompt: str, system_prompt_override: Optional[str] = None, additional_context: Optional[dict] = None) -> Dict[str, str]:
        sys_prompt = system_prompt_override or system_prompt
        user_payload = user_input
        if additional_context is not None:
            # Provide additional context as a JSON block preceding the user text
            user_payload = json.dumps({"context": additional_context, "input": user_input})

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_payload},
        ]
        reply = self.chat(messages)
        parsed = self._strip_think_and_extract_json(reply)
        if isinstance(parsed, dict):
            return parsed
        # Final fallback to empty dict to keep engine stable
        return {}
