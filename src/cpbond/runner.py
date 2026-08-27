"""Subprocess boundary.

Everything that shells out goes through a Runner, so tests can substitute
recorded output instead of needing iperf3, a network, or two carriers.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner(Protocol):
    def run(self, argv: Sequence[str], timeout: float | None = None) -> Completed: ...

    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    """Real execution."""

    def run(self, argv: Sequence[str], timeout: float | None = None) -> Completed:
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return Completed(124, exc.stdout or "", exc.stderr or "timed out")
        except FileNotFoundError as exc:
            return Completed(127, "", str(exc))
        return Completed(proc.returncode, proc.stdout, proc.stderr)

    def which(self, name: str) -> str | None:
        return shutil.which(name)


class FakeRunner:
    """Test double. Maps a matching token in argv to canned output."""

    def __init__(self, responses: dict[str, Completed], available: Sequence[str] = ()):
        self._responses = responses
        self._available = set(available)
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], timeout: float | None = None) -> Completed:
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        for key, resp in self._responses.items():
            if key in joined:
                return resp
        return Completed(127, "", f"FakeRunner: no response registered for {joined!r}")

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}" if name in self._available else None
