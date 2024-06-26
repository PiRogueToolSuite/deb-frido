"""
Configuration management
"""

import argparse
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
    args: argparse.Namespace

    # This allows argparse.Namespace even if there are no validators for it:
    class Config:  # pylint: disable=too-few-public-methods
        arbitrary_types_allowed = True


def init(config_path: Path) -> FridoConfig:
    """
    Turn a frido configuration file into a FridoConfig object.

    It could probably call expanduser() on a selection of Path objects (all but
    ppa.suite, which is meant to be a subdirectory of ppa.work_dir).
    """
    obj = yaml.safe_load(config_path.read_text())
    # The static config doesn't know (or at least shouldn't know) about args:
    obj |= {'args': argparse.Namespace()}
    return FridoConfig(**obj)
