"""
Configuration management
"""

import argparse
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, validator

# Class names and validator methods are self-explanatory, hush! Also, validators
# take a class as first parameter:
#   pylint: disable=missing-class-docstring
#   pylint: disable=missing-function-docstring
#   pylint: disable=no-self-argument

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

    @validator('work_dir')
    def auto_expanduser(cls, path: Path):
        return path.expanduser()


class FridoConfigBuild(BaseModel):
    arch: str
    wrapper: Optional[str]


class FridoConfigPpa(BaseModel):
    work_dir: Path
    suite: Path
    signing_key: str
    publish_url: str
    publish_wrapper: Optional[str]

    @validator('work_dir')
    def auto_expanduser(cls, path: Path):
        return path.expanduser()


class FridoConfigDiscord(BaseModel):
    webhook_url_file: Path


class FridoConfigReference(BaseModel):
    work_dir: Path
    pts_ppa_url: str

    @validator('work_dir')
    def auto_expanduser(cls, path: Path):
        return path.expanduser()


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

    The static config read from the configuration file is augmented with an
    empty args, which the caller can filled to keep track of the dynamic config
    (based on CLI options).
    """
    obj = yaml.safe_load(config_path.read_text())
    obj |= {'args': argparse.Namespace()}
    return FridoConfig(**obj)
