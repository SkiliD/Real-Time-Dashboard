# any local CLI AI over stdin/stdout

from __future__ import annotations

import json
import shlex
import subprocess

from ..provider import AIError, AIProvider


class CliProvider(AIProvider):
    name = "cli"

    def __init__(self, command: str, timeout: int = 300):
        self.command = (command or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.command)

    def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        if not self.is_available():
            raise AIError(
                'cli provider: set ai_cli_command in config.json '
                '(e.g. "llama-cli -m model.gguf")'
            )
        try:
            argv = shlex.split(self.command)
        except ValueError as e:
            raise AIError(f"cli provider: cannot parse ai_cli_command: {e}") from e
        if not argv:
            raise AIError("cli provider: ai_cli_command is empty")
        payload = json.dumps({"messages": messages, "temperature": temperature})
        try:
            proc = subprocess.run(
                argv, input=payload.encode("utf-8"),
                capture_output=True, timeout=self.timeout, shell=False,
            )
        except FileNotFoundError as e:
            raise AIError(f"cli provider: executable not found: {argv[0]!r}") from e
        except subprocess.TimeoutExpired as e:
            raise AIError(f"cli provider: {argv[0]!r} timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")[:300]
            raise AIError(f"cli provider: {argv[0]!r} exited {proc.returncode}: {err}")
        out = proc.stdout.decode("utf-8", "replace").strip()
        if not out:
            raise AIError(f"cli provider: {argv[0]!r} returned an empty answer")
        return out
