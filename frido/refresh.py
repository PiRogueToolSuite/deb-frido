"""
Refresh management.

Two things need to be kept up-to-date:
 - the git repository, with both upstream and packaging remotes;
 - the reference files, i.e. frida *.deb referenced in the official PPA.

Additionally, we monitor some packages in some repositories.
"""

import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from subprocess import PIPE, Popen, check_call, check_output
from typing import List

import requests

from debian.deb822 import Deb822
from debian.debian_support import Version as DVersion
from packaging.version import Version as UVersion

from .checks import check_git_consistency, check_overall_consistency
from .config import FridoConfig
from .notifications import NotifRefresh, notify_refresh
from .notifications import NotifMonitoringPackage, NotifMonitoring, notify_monitoring
from .state import FridoState, SUCCESS, FAILURE


def refresh_git(fc: FridoConfig, fs: FridoState, notif: NotifRefresh):
    """
    Refresh from remotes and analyze the state of the git branches and tags:
    upstream, debian, and locally.
    """
    # Sync remotes and detect tags:
    os.chdir(fc.git.work_dir)
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
    new = sorted(upstream_tags, key=UVersion)[-1]
    notif.append_metadata('Git upstream version', fs.git.upstream.tag, new)
    fs.git.upstream.tag = new

    # Extract Debian information (tricky):
    #  - we might have several tags sharing the same upstream version, and we
    #    cannot just sort them alphabetically or numerically;
    #  - we need to extract the full version from debian/changelog (or rely on
    #    being able to revert gbp tag's substitutions (~ → _ notably).
    #
    # 1. Start with detecting the last upstream version that was packaged:
    fs.git.debian.uversion = sorted([version_match.group(1)
                                     for tag in tags
                                     if (version_match := re.match(fc.git.debian_tags, tag))],
                                    key=UVersion)[-1]

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
    # from the last one, only logging the Debian version:
    sorted_debian_versions = sorted(debian_versions.items(), key=lambda item: DVersion(item[1]))
    fs.git.debian.tag = sorted_debian_versions[-1][0]
    new = sorted_debian_versions[-1][1]
    notif.append_metadata('Git package version', fs.git.debian.dversion, new)
    fs.git.debian.dversion = new

    # Compute what needs to be done, and notify only about new items in the todo
    # list (we could store old/new and let the notification method sort it out):
    todo = [tag for tag in upstream_tags if UVersion(tag) > UVersion(fs.git.debian.uversion)]
    notif.todo = [x for x in sorted(todo, key=UVersion) if x not in fs.todo]
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


def refresh_reference(fc: FridoConfig, fs: FridoState, notif: NotifRefresh):
    """
    Check the state of the PTS PPA, and make sure reference files are
    present (to diff against).
    """
    packages_path = fc.reference.work_dir / 'Packages'
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
        deb_path = fc.reference.work_dir / stanza['Filename']
        deb_path.parent.mkdir(parents=True, exist_ok=True)
        download_deb(f'{fc.reference.pts_ppa_url}/{stanza["Filename"]}', deb_path,
                     stanza['Size'], stanza['SHA256'])

        reference_debs[arch] = stanza['Filename']

    # Sync to disk:
    notif.append_metadata('PPA package version', fs.reference.dversion, frida_versions[0])
    fs.reference.dversion = frida_versions[0]
    fs.reference.debs = reference_debs
    fs.sync()


def refresh_all(fc: FridoConfig, fs: FridoState):
    """
    Initial entry point for this module: refresh one or both data sources.
    """
    notif = NotifRefresh()
    if fc.args.refresh_git:
        refresh_git(fc, fs, notif)
    if fc.args.refresh_reference:
        refresh_reference(fc, fs, notif)

    # Specify the results twice, we only are about the current value, and don't
    # want to compare against the previous one:
    try:
        check_overall_consistency(fc, fs)
        notif.append_metadata('Overall consistency', SUCCESS, SUCCESS)
    except RuntimeError:
        notif.append_metadata('Overall consistency', FAILURE, FAILURE)

    notify_refresh(fc, notif, print_only=fc.args.no_notify)


def get_deb_depends(deb: Path) -> List[str]:
    """
    Extract Depends field from the specified Debian package.

    Return a list of packages, without any version information.
    """
    depends = check_output(['dpkg-deb', '--showformat=${Depends}', '-W', deb]).decode()
    if depends == '':
        return []
    return [re.sub(r' \(.+\)', '', x) for x in depends.split(', ')]


def get_deb_version(deb: Path) -> str:
    """
    Extract Version field from the specified Debian package.
    """
    return check_output(['dpkg-deb', '--showformat=${Version}', '-W', deb]).decode()


def get_packages_index(repo, this_dir, url):
    reply = requests.get(url, timeout=30)
    reply.raise_for_status()
    (this_dir / repo.packages_index).write_bytes(reply.content)

    if repo.packages_index == 'Packages.xz':
        check_call(['unxz', '-f', this_dir / repo.packages_index])
    elif repo.packages_index == 'Packages.gz':
        check_call(['gunzip', '-f', this_dir / repo.packages_index])
    else:
        assert repo.packages_index == 'Packages'


def refresh_monitoring_one(fc: FridoConfig, fs: FridoState, repo, suite, component, arch): \
    # pylint: disable=too-many-arguments
    """
    Check the state of the monitored repositories: one specific Packages file.
    """
    # Avoid weird characters in paths and in the state file:
    repo_name = repo.name.replace(' ', '-').lower()
    this_dir = fc.monitoring.work_dir / repo_name / suite / component / arch
    this_dir.mkdir(parents=True, exist_ok=True)

    # Download the right Packages* file and make it available as an uncompressed
    # Packages which is easier to manage than compressed versions:
    get_packages_index(
        repo,
        this_dir,
        f'{repo.url}/dists/{suite}/{component}/binary-{arch}/{repo.packages_index}',
    )

    package_to_filename = {}
    notif = NotifMonitoring(repo.name, suite, component, arch)
    for stanza in Deb822.iter_paragraphs((this_dir / 'Packages').read_text()):
        # Build a map so that we can process dependencies:
        package_to_filename[ stanza['Package'] ] = stanza['Filename']
        if stanza['Package'] not in repo.packages:
            continue

        # Shorten names for things we'll use several times:
        package = stanza['Package']
        filename = stanza['Filename']

        # Re-read data from the actual package if there's one:
        orig_deb = fs.monitoring.get(repo_name, {}) \
                                .get(suite, {}) \
                                .get(component, {}) \
                                .get(arch, {}) \
                                .get(package, None)

        this_deb = this_dir / Path(filename).name

        # Skip package if we already know about it:
        if orig_deb and orig_deb == Path(filename).name:
            continue

        # Must download if we don't have the file locally:
        this_url = f'{repo.url}/{filename}'
        download_deb(this_url, this_deb, stanza['Size'], stanza['SHA256'])

        # Focus on all dependencies all the time, to make sure
        # we don't miss anything:
        notif.packages.append(NotifMonitoringPackage(
            package,
            get_deb_version(this_dir / orig_deb) if orig_deb else None,
            stanza['Version'],
            this_url,
            # URL resolution happens after this loop:
            {x: "???" for x in get_deb_depends(this_deb)}
        ))

        # Store a pointer to the new package.
        # FIXME: The dict construction is awful!
        if repo_name not in fs.monitoring:
            fs.monitoring[repo_name] = {}
        if suite not in fs.monitoring[repo_name]:
            fs.monitoring[repo_name][suite] = {}
        if component not in fs.monitoring[repo_name][suite]:
            fs.monitoring[repo_name][suite][component] = {}
        if arch not in fs.monitoring[repo_name][suite][component]:
            fs.monitoring[repo_name][suite][component][arch] = {}
        fs.monitoring[repo_name][suite][component][arch][package] = \
            Path(this_deb).name
        # FIXME: We're writing and sync-ing before sending the
        # notification!
        fs.sync()

    for package in notif.packages:
        for dep in package.depends:
            if dep in package_to_filename:
                package.depends[dep] = f'{repo.url}/{package_to_filename[dep]}'

    # Send, possibly empty if all packages were known already
    # and skipped:
    notify_monitoring(fc, notif, print_only=fc.args.no_notify)


def refresh_monitoring(fc: FridoConfig, fs: FridoState):
    """
    Check the state of the monitored repositories.
    """
    for repo in fc.monitoring.repos:
        for suite in repo.suites:
            for component in repo.components:
                for arch in repo.architectures:
                    refresh_monitoring_one(fc, fs, repo, suite, component, arch)
