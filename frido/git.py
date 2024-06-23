"""
Git management.
"""

import os
import re
from subprocess import PIPE, Popen, check_call, check_output

from debian.debian_support import Version as DVersion
from packaging.version import Version as UVersion

from .config import FridoConfig
from .state import FridoState


def refresh_git(fc: FridoConfig, fs: FridoState):
    """
    Check the state of the git branches and tags: upstream, debian, and
    locally.
    """
    # Sync remotes and detect tags:
    os.chdir(fc.git.work_dir.expanduser())
    check_call(['git', 'fetch', fc.git.debian_remote])
    check_call(['git', 'fetch', fc.git.upstream_remote])
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
