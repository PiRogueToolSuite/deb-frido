"""
State management
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel

# Class names are self-explanatory, hush!
# pylint: disable=missing-class-docstring


# Avoid hardcoding emojis all the time, and make them easier to adjust if we
# wanted to:
SUCCESS = '✅'
FAILURE = '❌'
WARNING = '❗'


class FridoStateGitDebian(BaseModel):
    """
    Debian is tricky:
     - tag: debian/16.3.3_pirogue1 (git tag -l)
     - dversion: 16.3.3~pirogue1   (debian/changelog)
     - uversion: 16.3.3            (git tag -l + git.debian_tags config)
    """
    tag: Optional[str]
    dversion: Optional[str]
    uversion: Optional[str]


class FridoStateGitUpstream(BaseModel):
    """
    Upstream is easy: tag == version.
    """
    tag: Optional[str]


class FridoStateGit(BaseModel):
    debian: FridoStateGitDebian
    upstream: FridoStateGitUpstream


class FridoStateReference(BaseModel):
    version: Optional[str]
    debs: dict[str, str]


class FridoStateResult(BaseModel):
    steps: Dict[str, str]
    success: bool


class FridoState(BaseModel):
    git: FridoStateGit
    reference: FridoStateReference
    results: Dict[str, FridoStateResult]
    todo: List[str]

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

    In addition to what's getting read from the state file, the path to that
    file is remembered so that it can be updated from various places. An
    official private attribute could be used instead of a class attribute,
    but making it work didn't seem trivial.
    """
    if not state_path.exists():
        state_path.write_text(DEFAULT_STATE_CONTENT)

    # Sync-ing the state can happen from various places, so let's resolve the
    # path before registering it:
    FridoState.register_path(state_path.resolve())
    obj = yaml.safe_load(state_path.read_text())
    return FridoState(**obj)


DEFAULT_STATE_CONTENT = """---
git:
  debian:
    tag: null
    dversion: null
    uversion: null
  upstream:
    tag: null
reference:
  version: null
  debs: {}
results: {}
todo: []
"""
