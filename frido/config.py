"""
Configuration management
"""

from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel

# Class names are self-explanatory, hush!
# pylint: disable=missing-class-docstring

class FridoConfigGit(BaseModel):
    work_dir: Path
    upstream_remote: str
    upstream_tags: str
    debian_remote: str
    debian_branch: str
    debian_tags: str
    debian_auto_branch: str
    debian_auto_tag_format: str
    debian_suffix: str


class FridoConfigBuild(BaseModel):
    arch: str
    wrapper: Optional[str]


class FridoConfigPpa(BaseModel):
    work_dir: Path
    suite: Path
    signing_key: str
    publish_url: str
    publish_wrapper: Optional[str]


class FridoConfigDiscord(BaseModel):
    webhook_url_file: Path


class FridoConfigReference(BaseModel):
    work_dir: Path
    pts_ppa_url: str


class FridoConfig(BaseModel):
    git: FridoConfigGit
    builds: List[FridoConfigBuild]
    ppa: FridoConfigPpa
    discord: FridoConfigDiscord
    reference: FridoConfigReference


def load_config(config_file) -> FridoConfig:
    """
    Turn a frido configuration file into a FridoConfig object.

    It could probably call expanduser() on a selection of Path objects (all but
    ppa.suite, which is meant to be a subdirectory of ppa.work_dir).
    """
    obj = yaml.safe_load(Path(config_file).read_text())
    return FridoConfig(**obj)
