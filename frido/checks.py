"""
Checks management.
"""

import logging
import os
import sys
from subprocess import check_call, check_output, run

from .config import FridoConfig
from .state import FridoState


def check_git_consistency(fc: FridoConfig, _fs: FridoState):
    """
    Detect dangerous situations, erroring out if needed.
    """
    os.chdir(fc.git.work_dir.expanduser())
    # Having WIP-oriented branches locally might be needed when fixing build
    # failures, but let's make sure we don't autobuild anything from a branch
    # that's not the configured one:
    current_branch = check_output(['git', 'branch', '--show-current']).decode().rstrip()
    if current_branch != fc.git.debian_branch:
        logging.error('unexpected current branch: %s (should be: %s)',
                      current_branch, fc.git.debian_branch)
        sys.exit(1)

    # Compare the packaging branches, local and remote:
    #  - both have diverged: KO
    #  - both are equal: OK
    #  - local ahead: OK
    #  - remote might be ahead: should be OK; a fast-forward merge is performed,
    #    to avoid having to merge manually when official work is pushed to the
    #    remote from a different machine (e.g. a developer pushes an official
    #    tag and/or new commits are staged for a later version).
    local_is_ancestor = run(['git', 'merge-base', '--is-ancestor',
                             fc.git.debian_branch,
                             f'{fc.git.debian_remote}/{fc.git.debian_branch}'],
                            check=False).returncode == 0
    remote_is_ancestor = run(['git', 'merge-base', '--is-ancestor',
                              f'{fc.git.debian_remote}/{fc.git.debian_branch}',
                              fc.git.debian_branch],
                             check=False).returncode == 0
    if not local_is_ancestor and not remote_is_ancestor:
        logging.error('local and remote %s branches have diverged, must be fixed manually',
                      fc.git.debian_remote)
        sys.exit(1)
    elif local_is_ancestor and remote_is_ancestor:
        logging.debug('local and remote %s branches are equal, no-op', fc.git.debian_remote)
    elif remote_is_ancestor:
        logging.debug('local branch %s is ahead of the remote, no-op', fc.git.debian_remote)
    elif local_is_ancestor:
        logging.debug('remote branch %s is ahead, fast-forwarding', fc.git.debian_remote)
        # --ff-only to be extra sure, as merge-base calls earlier should ensure success:
        check_call(['git', 'merge', '--ff-only', f'{fc.git.debian_remote}/{fc.git.debian_branch}'])


def check_overall_consistency(fc: FridoConfig, fs: FridoState):
    """
    Ensure everything is consistent (git and reference), and ready to perform
    builds (which operates in the git checkout in an automated fashion).
    """
    # Since --refresh* can be called independently from --build, check git
    # consistency (again):
    check_git_consistency(fc, fs)

    # We must hava data! This is never expected to happen once frido's been set
    # up properly, hence the sys.exit().
    if fs.git.debian.dversion is None:
        logging.error('no information for git, please use --refresh(-git)')
        sys.exit(1)
    if fs.reference.version is None:
        logging.error('no information for reference, please use --refresh(-reference)')
        sys.exit(1)

    # Official things must match! This might happen if pushes to Git and to the
    # PPA don't happen in lockstep, hence the RuntimeError.
    if fs.git.debian.dversion != fs.reference.version:
        logging.error('inconsistent versions: %s (git) vs. %s (reference)',
                      fs.git.debian.dversion, fs.reference.version)
        raise RuntimeError('inconsistent versions')
