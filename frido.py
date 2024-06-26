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
from frido.refresh import refresh_all


CONFIG_FILE = Path('config.yaml')
STATE_FILE = Path('state.yaml')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='Frida auto-packager')

    actions = parser.add_argument_group('main actions')
    actions.add_argument('--refresh', action='store_true',
                         help='refresh both git data and reference files')
    actions.add_argument('--refresh-git', action='store_true',
                         help='refresh git data')
    actions.add_argument('--refresh-reference', action='store_true',
                         help='refresh reference files')
    actions.add_argument('--build', action='store_true',
                         help='build versions listed as todo')

    options = parser.add_argument_group('fine-tuning options')
    options.add_argument('--only-one', action='store_true',
                         help='restrict building to a single version')
    options.add_argument('--cheat', action='store_true',
                         help='skip building, reuse existing build results instead')
    options.add_argument('--no-fetch', action='store_true',
                         help='skip "git fetch" when refreshing git data')
    options.add_argument('--no-notify', action='store_true',
                         help='skip notifying the Discord channel')
    args = parser.parse_args()

    # We have 3 options for granularity:
    if args.refresh:
        args.refresh_git = args.refresh_reference = True

    FC = frido.config.init(CONFIG_FILE)
    FS = frido.state.init(STATE_FILE)

    # Two actions are possible, each of them might send one notification:
    if args.refresh_git or args.refresh_reference:
        refresh_all(FC, FS, args)
    if args.build:
        build_all(FC, FS, args)
