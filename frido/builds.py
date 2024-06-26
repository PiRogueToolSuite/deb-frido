"""
Builds management.
"""

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from debian.deb822 import Deb822

from .actions import run_actions
from .checks import check_overall_consistency
from .notifications import notify_build

from .config import FridoConfig
from .state import FridoState, FridoStateResult, SUCCESS, FAILURE, WARNING


STEPS = []
def stepmethod(func):
    """
    Decorator for step-oriented methods in the FridoBuild class.
    """
    def wrapper(self):
        return func(self)
    STEPS.append(func.__name__)
    return wrapper


class FridoBuild:
    """
    Run successive steps to build one specific version.

    Status can be either just SUCCESS/FAILURE (a single emoji in the initial
    implementation), or one or more lines (newline-separated) starting with
    SUCCESS, FAILURE, or WARNING, followed by a space and details.

    The @stepmethod decorator and the order are important, for the step
    dispatcher to do its work.
    """
    def __init__(self, fc: FridoConfig, fs: FridoState, version: str):
        self.fc = fc
        self.fs = fs
        # We distinguish between upstream and debian versions:
        self.uversion = version
        self.dversion = f'{version}{fc.git.debian_suffix}'
        self.publish_queue: list[str] = []
        os.chdir(fc.git.work_dir)

    @stepmethod
    def clean(self):
        """
        Clean all the things (hopefully).
        """
        try:
            run_actions('clean', {})
            return SUCCESS
        except BaseException:
            return FAILURE

    @stepmethod
    def prepare(self):
        """
        Merge and bump changelog.
        """
        try:
            run_actions('prepare', {
                'uversion': self.uversion,
                'dversion': self.dversion,
            })
            return SUCCESS
        except BaseException:
            return FAILURE

    @stepmethod
    def patch(self):
        """"
        Apply/deapply patches, finalize changelog (that last commit can be
        scraped if the build fails.
        """
        try:
            run_actions('patch', {
                'dversion': self.dversion,
                'tagformat': self.fc.git.debian_auto_tag_format,
            })
            return SUCCESS
        except BaseException:
            return FAILURE

    @stepmethod
    def build(self):
        """
        Build for each configured architecture, one after another. The
        status is multiline (one line per architecture).

        Design choice: the first failure is fatal.
        """
        status = ''
        try:
            for build in self.fc.builds:
                # Determine the full build command. Note the -us option to
                # avoid signing the source package, which debsign tries to
                # do despite -b.
                build_cmd = 'debuild -b -us -uc'
                if build.wrapper:
                    build_cmd = f'{build.wrapper} -- {build_cmd}'

                # Make it possible to cheat: saving debian/files when
                # building for real, and reusing it when cheating.
                cheating_file = f'../cheat/frida_{self.dversion}_{build.arch}.files'
                dpkg_genchanges_options = []
                if self.fc.args.cheat:
                    build_cmd = 'true'
                    dpkg_genchanges_options = [f'-f{cheating_file}']

                # Clean everything, not absolutely required for the first
                # arch, but definitely preferable for all others. Note we're
                # borrowing actions from the 'clean' step.
                run_actions('clean', {})

                # Run the build:
                subprocess.check_output(shlex.split(build_cmd))
                if not self.fc.args.cheat:
                    shutil.copy('debian/files', cheating_file)

                # Collect information for publication:
                output_changes = subprocess.check_output(['dpkg-genchanges', '-b',
                                                          *dpkg_genchanges_options],
                                                         stderr=subprocess.DEVNULL)
                changes = Deb822(output_changes.decode())
                # Trick: to publish the build log, we pick the .buildinfo
                # path and remove 'info' at the very end:
                files = [re.sub(r'\.buildinfo$', '.build', line.split(' ')[-1])
                         for line in changes['Files'].splitlines()
                         if line.endswith('.deb') or line.endswith('.buildinfo')]
                self.publish_queue.extend(files)
                status += f'{SUCCESS} {build.arch}\n'
        except subprocess.CalledProcessError as ex:
            lines = 20
            logging.error('Failure while running %s: (last %d lines)', ex.cmd, lines)
            for line in ex.stdout.decode().splitlines()[-lines:]:
                logging.error('  %s', line)
            status += f'{FAILURE} {build.arch}'
        except BaseException as ex:
            # We do much more than call run_actions(), so always mention the
            # exception:
            print(ex)
            status += f'{FAILURE} {build.arch}'
        return status

    @stepmethod
    def debdiff(self):
        """
        We have a list of files to publish, some of them .deb, which we want
        to check against reference files to generate diffs.
        """
        status = ''
        try:
            # We're adding items to the list while we're looping over it,
            # which is OK given the .deb extension condition, but pylint
            # suggests operating on a copy:
            for publish_file in self.publish_queue.copy():
                if not publish_file.endswith('.deb'):
                    continue

                arch = None
                arch_match = re.match(r'.*_(.+?)\.deb', publish_file)
                if not arch_match:
                    logging.error('unable to determine architecture for %s', publish_file)
                    sys.exit(1)
                arch = arch_match.group(1)

                reference_path = self.fc.reference.work_dir / self.fs.reference.debs[arch]
                debdiff_run = subprocess.run(['debdiff', reference_path, f'../{publish_file}'],
                                             capture_output=True, check=False)
                if debdiff_run.returncode not in [0, 1]:
                    raise RuntimeError(f'unexpected returncode for debdiff ({debdiff_run.returncode})')
                debdiff_path = Path('..') / re.sub(r'\.deb', '.debdiff.txt', publish_file)
                debdiff_path.write_bytes(debdiff_run.stdout)
                self.publish_queue.append(debdiff_path.name)
                status += f'{SUCCESS} {arch}\n'
        except BaseException as ex:
            print(ex)
            status += f'{FAILURE} {arch}'
        return status

    @stepmethod
    def publish_file(self):
        """
        Import from the publish queue, warning for each file that already
        exists with different contents.
        """
        status = ''
        try:
            suite_path = self.fc.ppa.work_dir / self.fc.ppa.suite
            suite_path.mkdir(parents=True, exist_ok=True)
            for publish_file in sorted(self.publish_queue):
                src_file = Path('..') / publish_file
                dst_file = suite_path / publish_file
                icon = SUCCESS
                if dst_file.exists():
                    src_digest = hashlib.file_digest(src_file.open('rb'), 'sha256')
                    dst_digest = hashlib.file_digest(dst_file.open('rb'), 'sha256')
                    if src_digest.hexdigest() != dst_digest.hexdigest():
                        icon = WARNING
                shutil.copy(src_file, dst_file)
                status += f'{icon} {publish_file}\n'
        except BaseException as ex:
            # We do much more than call run_actions(), so always mention the
            # exception:
            print(ex)
            status += f'{FAILURE} {publish_file}\n'
        return status

    @stepmethod
    def publish_repo(self):
        """
        Refresh indices, publish/sync repository.
        """
        try:
            # Clean indices first, since apt-archive could pick up existing files:
            suite_path = self.fc.ppa.work_dir / self.fc.ppa.suite
            for index in suite_path.glob('Packages*'):
                index.unlink()
            for index in suite_path.glob('Release*'):
                index.unlink()

            # Switch to the suite directory for indexing/signing operations:
            cwd = os.getcwd()
            os.chdir(suite_path)
            output = subprocess.check_output(['apt-ftparchive', 'packages', '.'])
            (suite_path / 'Packages').write_bytes(output)
            output = subprocess.check_output(['xz', '-9', '-k', 'Packages'])
            output = subprocess.check_output(['apt-ftparchive', 'release', '.'])
            (suite_path / 'Release').write_bytes(output)
            output = subprocess.check_output([
                'gpg', '--armor', '--local-user', self.fc.ppa.signing_key,
                '--detach-sign', '--output', 'Release.gpg', 'Release',
            ])

            # Export the signing key under a name matching the mail address
            # part of the first user ID detected, leaving a generic name if
            # it cannot be determined:
            signing_key = 'signing-key.asc'
            output = subprocess.check_output([
                'gpg', '--armor',
                '--export', '--output', signing_key, self.fc.ppa.signing_key,
            ])
            packets = subprocess.check_output(['gpg', '--list-packets', signing_key])
            for line in packets.decode().splitlines():
                match = re.match(r'^:user ID packet: ".*<(.+?)>"?', line)
                if match:
                    shutil.move(signing_key, f'{match.group(1)}.asc')
                    break

            # Switch to the ppa directory for final publish operation (if
            # any), then back to the old current working directory:
            if self.fc.ppa.publish_wrapper:
                os.chdir(self.fc.ppa.work_dir)
                output = subprocess.check_output(shlex.split(self.fc.ppa.publish_wrapper))
            os.chdir(cwd)
            return SUCCESS
        except BaseException as ex:
            # We do much more than call run_actions(), so always mention the
            # exception:
            print(ex)
            return FAILURE

    @stepmethod
    def push(self):
        """Push branch and tag to our git repository"""
        try:
            # The tag isn't quite the same as the fullversion, as we have
            # some characters getting replaced. That's why we go for git
            # describe:
            tag = subprocess.check_output(['git', 'describe']).decode().rstrip()

            # Since we're using specific settings for auto branch and auto
            # tags, we're pushing with -f and overwriting without any kind
            # of second thought!
            run_actions('push', {
                'remote': self.fc.git.debian_remote,
                'branch': self.fc.git.debian_auto_branch,
                'tag': tag,
            })
            return SUCCESS
        except BaseException as ex:
            # We do a little more than call run_actions(), so always mention
            # the exception:
            print(ex)
            return FAILURE

    def run_steps(self) -> FridoStateResult:
        """
        Step dispatcher, iterating over (decorated) steps.

        Regardless of the overall success, the state file is updated
        accordingly, and the FridoStateResult object is returned to the caller
        for further processing (e.g. sending notifications).
        """
        result = FridoStateResult(steps={}, success=True)
        for step in STEPS:
            result.steps[step] = '…'
            status = getattr(self, step)()

            # Log, remember, and stop here if a failure is spotted:
            for line in status.splitlines():
                logging.info('step %s: %s', step, line)
            result.steps[step] = status
            if status.find(FAILURE) != -1:
                result.success = False
                break

        self.fs.results[self.uversion] = result
        self.fs.sync()
        return result


def build_all(fc: FridoConfig, fs: FridoState):
    """
    Iterate over the todo list, building as requested.

    For each version in the todo list, we might have results. If operations were
    successful, skip the version, otherwise stop immediately: some human brain
    is needed.

    Otherwise, process that version, and update the state file accordingly. If
    that was a success, move to the next version, otherwise stop immediately:
    some human brain is needed.
    """
    check_overall_consistency(fc, fs)

    for version in fs.todo:
        # If we've seen this version already, either skip or stop:
        if version in fs.results:
            if fs.results[version].success:
                logging.debug('skipping %s, finished successfully', version)
                continue

            logging.debug('version %s did not finish successfully, stopping', version)
            sys.exit(1)

        # Otherwise: process, notify, and maybe continue:
        logging.info('building %s', version)

        frido_build = FridoBuild(fc, fs, version)
        result = frido_build.run_steps()
        notify_build(fc, version, result, print_only=fc.args.no_notify)

        if not result.success:
            logging.error('automated packaging of %s failed, stopping', version)
            sys.exit(1)

        if fc.args.only_one:
            logging.debug('building only one version as requested, stopping')
            sys.exit(0)
