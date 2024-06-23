"""
Reference management.
"""

import hashlib
import logging
import sys
from pathlib import Path

import requests

from debian.deb822 import Deb822
from debian.debian_support import Version as DebianVersion

from .config import FridoConfig
from .state import FridoState


def download_deb(deb_url: str, deb_path: Path, size: int, sha256: str):
    """
    Make sure the specified deb is available locally, with the right size
    and checksum.
    """
    download = False
    if not deb_path.exists():
        # First download:
        download = True
    else:
        # File doesn't match (truncated download, rebuild, etc.):
        local_size = deb_path.stat().st_size
        local_sha256 = hashlib.file_digest(deb_path.open('rb'), 'sha256')
        if str(local_size) != size or local_sha256.hexdigest() != sha256:
            download = True

    if download:
        reply = requests.get(deb_url, timeout=30)
        reply.raise_for_status()
        deb_path.write_bytes(reply.content)

        # FIXME: duplicates earlier check.
        local_size = deb_path.stat().st_size
        local_sha256 = hashlib.file_digest(deb_path.open('rb'), 'sha256')
        if str(local_size) != size or local_sha256.hexdigest() != sha256:
            logging.error('size or sha256 mismatch for the local file %s', deb_path)
            sys.exit(1)


def sync_reference(fc: FridoConfig, fs: FridoState):
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
        if DebianVersion(stanza['Version']) > DebianVersion(frida_stanzas[arch]['Version']):
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

    # Remember the reference files:
    fs.reference.version = frida_versions[0]
    fs.reference.debs = reference_debs
    fs.sync()
