#!/usr/bin/python3
"""
Frida auto-packager, see README.md for details.
"""

import argparse
import logging
from pathlib import Path

import frido.config
import frido.state
from frido.builds import process
from frido.git import refresh_git
from frido.reference import refresh_reference


CONFIG_FILE = Path('config.yaml')
STATE_FILE = Path('state.yaml')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='Frida auto-packager')
    parser.add_argument('--refresh-git', action='store_true')
    parser.add_argument('--refresh-reference', action='store_true')
    parser.add_argument('--process', action='store_true')
    parser.add_argument('--cheat', action='store_true')
    parser.add_argument('--only-one', action='store_true')
    parser.add_argument('--no-notify', action='store_true')
    args = parser.parse_args()

    FC = frido.config.init(CONFIG_FILE)
    FS = frido.state.init(STATE_FILE)

    if args.refresh_git:
        refresh_git(FC, FS)
    if args.refresh_reference:
        refresh_reference(FC, FS)
    if args.process:
        process(FC, FS, args)
