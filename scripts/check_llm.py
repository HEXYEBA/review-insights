"""Make one minimal real Codex subscription smoke request."""

import time
import os

from review_insights.config import DEFAULT_MODEL
from review_insights.llm import run_codex_json


_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
_PROMPT = "Верни только JSON с ok=true. Не используй инструменты."


def main() -> int:
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    started = time.perf_counter()
    ok = False
    try:
        response = run_codex_json(_PROMPT, _SCHEMA)
        ok = type(response.get("ok")) is bool and response["ok"] is True
    except Exception:
        # The smoke command prints status only; private CLI diagnostics stay in temp files.
        ok = False
    duration = time.perf_counter() - started
    print(f"model_name: {model_name}")
    print(f"ok: {str(ok).lower()}")
    print(f"duration_seconds: {duration:.1f}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
