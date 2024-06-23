"""
State management
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

# Class names are self-explanatory, hush!
# pylint: disable=missing-class-docstring


class FridoStateReference(BaseModel):
    version: Optional[str]
    debs: dict[str, str]


class FridoState(BaseModel):
    reference: FridoStateReference

    @classmethod
    def register_path(cls, path: Path):
        """Remember where the state file is to be written"""
        cls._path = path  # type: ignore[attr-defined]

    def sync(self):
        """Sync state to disk"""
        # Future migration: dict() -> model_dump()
        self._path.write_text(yaml.dump(self.dict(), sort_keys=False))


def init(state_path: Path) -> FridoState:
    """
    Turn a frido state file into a FridoState object.
    """
    if not state_path.exists():
        state_content = DEFAULT_STATE_CONTENT
    else:
        state_content = state_path.read_text()

    # Sync-ing the state can happen from various places, so let's resolve the
    # path before registering it:
    FridoState.register_path(state_path.resolve())
    obj = yaml.safe_load(state_content)
    return FridoState(**obj)


DEFAULT_STATE_CONTENT = """---
reference:
  version: null
  debs: {}
"""
