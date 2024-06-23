#!/usr/bin/python3
"""
Frida auto-packager, see README.md for details.
"""

import argparse
import logging
from pathlib import Path

import frido.config
import frido.state
from frido.builds import build_all
from frido.git import refresh_git
from frido.reference import refresh_reference


CONFIG_FILE = Path('config.yaml')
STATE_FILE = Path('state.yaml')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='Frida auto-packager')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--refresh-git', action='store_true')
    parser.add_argument('--refresh-reference', action='store_true')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--cheat', action='store_true')
    parser.add_argument('--only-one', action='store_true')
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--no-fetch', action='store_true')
    args = parser.parse_args()

    # We have 3 options for granularity:
    if args.refresh:
        args.refresh_git = args.refresh_reference = True

    FC = frido.config.init(CONFIG_FILE)
    FS = frido.state.init(STATE_FILE)

    if args.refresh_git:
        refresh_git(FC, FS, args)
    if args.refresh_reference:
        refresh_reference(FC, FS, args)
    if args.build:
        build_all(FC, FS, args)
