"""Session persistence.

State lives in one JSON file so a session can be started by one person, handed
over, and continued by another without losing the baselines.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .measure import Sample

DEFAULT_PATH = Path("cpbond-session.json")
SCHEMA = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Session:
    server: str = ""
    duration: float = 60
    notes: str = ""
    samples: list[Sample] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    created: str = field(default_factory=utcnow)

    # -- queries --------------------------------------------------------------

    def samples_for(self, label: str) -> list[Sample]:
        return [s for s in self.samples if s.label == label]

    def links(self) -> list[str]:
        seen: list[str] = []
        for s in self.samples:
            if s.kind == "link" and s.label not in seen:
                seen.append(s.label)
        return seen

    def bonded_samples(self) -> list[Sample]:
        return [s for s in self.samples if s.kind == "bonded"]

    def mark(self, step: str) -> None:
        if step not in self.completed:
            self.completed.append(step)

    def done(self, step: str) -> bool:
        return step in self.completed

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "created": self.created,
            "server": self.server,
            "duration": self.duration,
            "notes": self.notes,
            "completed": self.completed,
            "samples": [s.to_dict() for s in self.samples],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        schema = d.get("schema", SCHEMA)
        if schema > SCHEMA:
            raise ValueError(
                f"session file schema {schema} is newer than this cpbond "
                f"(supports {SCHEMA}); upgrade cpbond"
            )
        return cls(
            server=d.get("server", ""),
            duration=d.get("duration", 60),
            notes=d.get("notes", ""),
            samples=[Sample.from_dict(s) for s in d.get("samples", [])],
            completed=list(d.get("completed", [])),
            created=d.get("created", utcnow()),
        )


def load(path: Path = DEFAULT_PATH) -> Session:
    if not path.exists():
        return Session()
    with path.open() as fh:
        return Session.from_dict(json.load(fh))


def save(session: Session, path: Path = DEFAULT_PATH) -> None:
    """Write atomically -- a half-written session file loses the baselines,
    which are the one thing in here that cannot be re-measured after the
    overlay is up."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent or "."), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(session.to_dict(), fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
