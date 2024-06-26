"""
Refresh management.

Two things need to be kept up-to-date:
 - the git repository, with both upstream and packaging remotes;
 - the reference files, i.e. frida *.deb referenced in the official PPA.
"""

import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from subprocess import PIPE, Popen, check_call, check_output

import requests

from debian.deb822 import Deb822
from debian.debian_support import Version as DVersion
from packaging.version import Version as UVersion

from .checks import check_git_consistency
from .config import FridoConfig
from .state import FridoState


def refresh_git(fc: FridoConfig, fs: FridoState):
    """
    Refresh from remotes and analyze the state of the git branches and tags:
    upstream, debian, and locally.
    """
    # Sync remotes and detect tags:
    os.chdir(fc.git.work_dir.expanduser())
    if fc.args.no_fetch:
        logging.debug('skipping git fetch as requested')
    else:
        check_call(['git', 'fetch', fc.git.debian_remote])
        check_call(['git', 'fetch', fc.git.upstream_remote])
    check_git_consistency(fc, fs)
    tags = check_output(['git', 'tag', '-l']).decode().splitlines()

    # Extract upstream information (easy):
    upstream_tags = [tag
                     for tag in tags
                     if re.match(fc.git.upstream_tags, tag)]
    fs.git.upstream.tag = sorted(upstream_tags, key=UVersion)[-1]

    # Extract Debian information (tricky):
    #  - we might have several tags sharing the same upstream version, and we
    #    cannot just sort them alphabetically or numerically;
    #  - we need to extract the full version from debian/changelog (or rely on
    #    being able to revert gbp tag's substitutions (~ → _ notably).
    #
    # 1. Start with detecting the last upstream version that was packaged:
    debian_uversions = [version_match.group(1)
                        for tag in tags
                        if (version_match := re.match(fc.git.debian_tags, tag))]
    fs.git.debian.uversion = sorted(debian_uversions, key=UVersion)[-1]

    # 2. Collect the matching tags for that upstream version (one or more):
    debian_tags = [tag
                   for tag in tags
                   if (version_match := re.match(fc.git.debian_tags, tag))
                   and version_match.group(1) == fs.git.debian.uversion]

    # 3. Read (don't guess) the exact version based on their debian/changelog file:
    debian_versions = {}
    for tag in debian_tags:
        # pylint: disable=consider-using-with
        proc1 = Popen(['git', 'show', f'{tag}:debian/changelog'], stdout=PIPE)
        proc2 = Popen(['dpkg-parsechangelog', '-SVersion', '-l-'], stdin=proc1.stdout, stdout=PIPE)
        debian_versions[tag] = proc2.communicate()[0].decode().rstrip()

    # 4. Sort by values (Debian version), then extract tag and Debian version
    # from the last one:
    sorted_debian_versions = sorted(debian_versions.items(), key=lambda item: DVersion(item[1]))
    fs.git.debian.tag = sorted_debian_versions[-1][0]
    fs.git.debian.dversion = sorted_debian_versions[-1][1]

    # Compute what needs to be done:
    todo = [tag for tag in upstream_tags if UVersion(tag) > UVersion(fs.git.debian.uversion)]
    fs.todo = sorted(todo, key=UVersion)

    # Sync to disk:
    fs.sync()


def check_file_metadata(file_path: Path, size: int, sha256: str):
    """
    Check size and sha256sum for a given file.
    """
    local_size = file_path.stat().st_size
    local_sha256 = hashlib.file_digest(file_path.open('rb'), 'sha256')
    return str(local_size) == size or local_sha256.hexdigest() == sha256


def download_deb(deb_url: str, deb_path: Path, size: int, sha256: str):
    """
    Make sure the specified deb is available locally, with the right size
    and checksum.
    """
    # There might be no file, or a file that doesn't match:
    if not deb_path.exists() or not check_file_metadata(deb_path, size, sha256):
        reply = requests.get(deb_url, timeout=30)
        reply.raise_for_status()
        deb_path.write_bytes(reply.content)

        if not check_file_metadata(deb_path, size, sha256):
            logging.error('size or sha256 mismatch for the local file %s', deb_path)
            sys.exit(1)


def refresh_reference(fc: FridoConfig, fs: FridoState):
    """
    Check the state of the PTS PPA, and make sure reference files are
    present (to diff against).
    """
    packages_path = fc.reference.work_dir.expanduser() / 'Packages'
    packages_path.parent.mkdir(parents=True, exist_ok=True)

    reply = requests.get(f'{fc.reference.pts_ppa_url}/Packages', timeout=30)
    reply.raise_for_status()
    packages_path.write_bytes(reply.content)

    # Extract stanzas for the last version of frida:
    archs = [build.arch for build in fc.builds]
    frida_stanzas = {arch: Deb822('Version: 0') for arch in archs}
    for stanza in Deb822.iter_paragraphs(packages_path.read_text()):
        if stanza['Package'] != 'frida':
            continue

        arch = stanza['Architecture']
        if DVersion(stanza['Version']) > DVersion(frida_stanzas[arch]['Version']):
            frida_stanzas[arch] = stanza

    # Consistency check:
    frida_versions = list({stanza['Version'] for stanza in frida_stanzas.values()})
    if len(frida_versions) != 1:
        logging.error('unexpected number of versions: %s', frida_versions)
        sys.exit(1)

    reference_debs = {}
    for arch, stanza in frida_stanzas.items():
        if stanza['Version'] == '0':
            logging.error('no frida stanza for architecture %s', arch)
            sys.exit(1)

        # Make sure any intermediate subdirectory is created if needed:
        deb_path = fc.reference.work_dir.expanduser() / stanza['Filename']
        deb_path.parent.mkdir(parents=True, exist_ok=True)
        download_deb(f'{fc.reference.pts_ppa_url}/{stanza["Filename"]}', deb_path,
                     stanza['Size'], stanza['SHA256'])

        reference_debs[arch] = stanza['Filename']

    # Sync to disk:
    fs.reference.version = frida_versions[0]
    fs.reference.debs = reference_debs
    fs.sync()


def refresh_all(fc: FridoConfig, fs: FridoState):
    """
    Entry point for this module: refresh one or both data sources.
    """
    if fc.args.refresh_git:
        refresh_git(fc, fs)
    if fc.args.refresh_reference:
        refresh_reference(fc, fs)
