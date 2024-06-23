#!/usr/bin/python3
"""
Frida auto-packager, see README.md for details.
"""

import argparse
import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import yaml
from debian.deb822 import Deb822
from packaging.version import Version

import frido.config
import frido.state
from frido.actions import run_actions
from frido.reference import sync_reference


# Resolve the path directly so that we don't have to keep track of the current
# working directory:
CONFIG_FILE = Path('config.yaml').resolve()
STATE_FILE = Path('state.yaml').resolve()

# Successive steps for each version. Some of them only return an OK/KO status
# (through a single emoji), while others can return a multiline status with some
# extra details.
STEPS = [
    'clean',
    'prepare',
    'patch',
    'build',
    'debdiff',
    'publish',
    'push',
]


def detect():
    """
    Check git repository: upstream vs. debian packaging.

    The state file gets updated accordingly:
     - last-package is the last upstream version with an official debian
       package.
       FIXME: we should probably keep track of the upstream version and of
       the debian revision, to access the debian packages.
     - todo is the list of upstream versions since that package.
     - results is left untouched, as that one is about the steps that have
       been performed in the past.
    """
    # Initial values, if the file doesn't exist yet:
    state = {
        'last-package': None,
        'todo': [],
        'results': {},
    }
    if STATE_FILE.exists():
        state = yaml.safe_load(STATE_FILE.read_text())

    # Sync and detect tags:
    os.chdir(FC.git.work_dir.expanduser())
    subprocess.check_call(['git', 'fetch', FC.git.upstream_remote])
    tags = subprocess.check_output(['git', 'tag', '-l']).decode().splitlines()

    # Compute needed work:
    upstream_tags = [tag
                     for tag in tags
                     if re.match(FC.git.upstream_tags, tag)]
    upstream_tags.sort(key=Version)
    debian_tags = [version_match.group(1)
                   for tag in tags
                   if (version_match := re.match(FC.git.debian_tags, tag))]
    debian_tags.sort(key=Version)

    last_package = debian_tags[-1]
    todo = [tag for tag in upstream_tags if Version(tag) > Version(last_package)]
    todo.sort(key=Version)

    # Save state:
    state['last-package'] = last_package
    state['todo'] = todo
    STATE_FILE.write_text(yaml.dump(state, sort_keys=False))


def process_one(version: str, reference: dict):
    """
    Run every step for the specified version.
    """
    result = {
        'steps': {},
        'success': True,
    }
    publish_queue = []
    fullversion = f'{version}{FC.git.debian_suffix}'
    os.chdir(FC.git.work_dir.expanduser())

    for step in STEPS:
        result['steps'][step] = '…'
        status = ''
        # TODO: Make sure the current branch is the expected one, but don't
        # force an initial state (in case of local commits).

        if step == 'clean':
            # Clean all the things (hopefully):
            try:
                run_actions(step, {})
                status = '✅'
            except BaseException:
                status = '❌'

        elif step == 'prepare':
            # Merge and bump changelog:
            try:
                run_actions(step, {
                    'version': version,
                    'fullversion': fullversion,
                })
                status = '✅'
            except BaseException:
                status = '❌'

        elif step == 'patch':
            # Apply/deapply patches, finalize changelog (that last commit can be
            # scraped if the build fails):
            try:
                run_actions(step, {
                    'fullversion': fullversion,
                    'tagformat': FC.git.debian_auto_tag_format,
                })
                status = '✅'
            except BaseException:
                status = '❌'

        elif step == 'build':
            # Build for each configured architecture, one after another. The
            # status is multiline (one line per architecture).
            #
            # Design choice: the first failure is fatal.
            try:
                for build in FC.builds:
                    # Determine the full build command. Note the -us option to
                    # avoid signing the source package, which debsign tries to
                    # do despite -b.
                    build_cmd = 'debuild -b -us -uc'
                    if build.wrapper:
                        build_cmd = f'{build.wrapper} -- {build_cmd}'

                    # Make it possible to cheat: saving debian/files when
                    # building for real, and reusing it when cheating.
                    cheating_file = f'../cheat/frida_{fullversion}_{build.arch}.files'
                    dpkg_genchanges_options = []
                    if args.cheat:
                        build_cmd = 'true'
                        dpkg_genchanges_options = [f'-f{cheating_file}']

                    # Clean everything, not absolutely required for the first
                    # arch, but definitely preferable for all others. Note we're
                    # borrowing actions from the 'clean' step.
                    run_actions('clean', {})

                    # Run the build:
                    subprocess.check_output(shlex.split(build_cmd))
                    if not args.cheat:
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
                    publish_queue.extend(files)
                    status += f'✅ {build.arch}\n'
            except subprocess.CalledProcessError as ex:
                lines = 20
                logging.error('Failure while running %s: (last %d lines)', ex.cmd, lines)
                for line in ex.stdout.decode().splitlines()[-lines:]:
                    logging.error('  %s', line)
                status += f'❌ {build.arch}'
            except BaseException as ex:
                # We do much more than call run_actions(), so always mention the
                # exception:
                print(ex)
                status += f'❌ {build.arch}'

        elif step == 'debdiff':
            # We have a list of files to publish, some of them .deb, which we
            # want to check against reference files to generate diffs.
            try:
                debdiff_files = []
                for publish_file in publish_queue:
                    if not publish_file.endswith('.deb'):
                        continue

                    arch = None
                    arch_match = re.match(r'.*_(.+?)\.deb', publish_file)
                    if not arch_match:
                        logging.error('unable to determine architecture for %s', publish_file)
                        sys.exit(1)
                    arch = arch_match.group(1)
                    logging.debug('determined architecture %s from filename %s', arch, publish_file)

                    reference_path = FC.reference.work_dir.expanduser() / reference['debs'][arch]
                    debdiff_run = subprocess.run(['debdiff', reference_path, f'../{publish_file}'],
                                                 capture_output=True)
                    if debdiff_run.returncode not in [0, 1]:
                        raise RuntimeError(f'unexpected returncode for debdiff ({debdiff_run.returncode})')
                    debdiff_path = Path('..') / re.sub(r'\.deb', '.debdiff.txt', publish_file)
                    debdiff_path.write_bytes(debdiff_run.stdout)
                    debdiff_files.append(debdiff_path.name)
                    status += f'✅ {arch}\n'
            except BaseException as ex:
                print(ex)
                status += f'❌ {arch}'

            # FIXME: It is a bit silly to have an extra step instead of
            # extending the publish_queue directly.
            try:
                publish_queue.extend(debdiff_files)
                status += f'✅ queue\n'
            except BaseException as ex:
                print(ex)
                status += f'❌ queue'

        elif step == 'publish':
            # Phase 1: Import from the publish queue, warning for each file that
            # already exists with different contents.
            #
            # TODO: Compute and publish diff against last official version.
            try:
                suite_path = FC.ppa.work_dir.expanduser() / FC.ppa.suite
                suite_path.mkdir(parents=True, exist_ok=True)
                for publish_file in sorted(publish_queue):
                    src_file = Path('..') / publish_file
                    dst_file = suite_path / publish_file
                    icon = '✅'
                    if dst_file.exists():
                        src_digest = hashlib.file_digest(src_file.open('rb'), 'sha256')
                        dst_digest = hashlib.file_digest(dst_file.open('rb'), 'sha256')
                        if src_digest.hexdigest() != dst_digest.hexdigest():
                            icon = '❗'
                    shutil.copy(src_file, dst_file)
                    status += f'{icon} {publish_file}\n'
            except BaseException as ex:
                # We do much more than call run_actions(), so always mention the
                # exception:
                print(ex)
                status += f'❌ {publish_file}\n'

            # Phase 2: Refresh indices, even if the previous step failed.
            try:
                # Clean indices first, since apt-archive could pick up existing files:
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
                    'gpg', '--armor', '--local-user', FC.ppa.signing_key,
                    '--detach-sign', '--output', 'Release.gpg', 'Release',
                ])

                # Export the signing key under a name matching the mail address
                # part of the first user ID detected, leaving a generic name if
                # it cannot be determined:
                signing_key = 'signing-key.asc'
                output = subprocess.check_output([
                    'gpg', '--armor',
                    '--export', '--output', signing_key, FC.ppa.signing_key,
                ])
                packets = subprocess.check_output(['gpg', '--list-packets', signing_key])
                for line in packets.decode().splitlines():
                    match = re.match(r'^:user ID packet: ".*<(.+?)>"?', line)
                    if match:
                        shutil.move(signing_key, f'{match.group(1)}.asc')
                        break

                # Switch to the ppa directory for final publish operation (if
                # any), then back to the old current working directory:
                if FC.ppa.publish_wrapper:
                    os.chdir(FC.ppa.work_dir.expanduser())
                    output = subprocess.check_output(shlex.split(FC.ppa.publish_wrapper))
                os.chdir(cwd)
                status += '✅ repository'
            except BaseException as ex:
                # We do much more than call run_actions(), so always mention the
                # exception:
                print(ex)
                status += '❌ repository'

        elif step == 'push':
            # Push branch and tag to our git repository:
            try:
                # The tag isn't quite the same as the fullversion, as we have
                # some characters getting replaced. That's why we go for git
                # describe:
                tag = subprocess.check_output(['git', 'describe']).decode().rstrip()

                # Since we're using specific settings for auto branch and auto
                # tags, we're pushing with -f and overwriting without any kind
                # of second thought!
                run_actions(step, {
                    'remote': FC.git.debian_remote,
                    'branch': FC.git.debian_auto_branch,
                    'tag': tag,
                })
                status = '✅'
            except BaseException as ex:
                # We do a little more than call run_actions(), so always mention
                # the exception:
                print(ex)
                status = '❌'

        # Log, remember, and stop here if a failure is spotted:
        for line in status.splitlines():
            logging.info('step %s: %s', step, line)
        result['steps'][step] = status
        if status.find('❌') != -1:
            result['success'] = False
            break

    return result


def notify(version: str, result: dict):
    """
    Build a message for this version, and send it via a Discord webhook.
    """
    if args.no_notify:
        logging.debug('skipping notification as requested')
        return

    message = []
    if result['success']:
        message.append(f'**Successful automatic packaging: {version}**')
    else:
        message.append(f'**Failed automatic packaging: {version}**')

    ppa_suite_path = FC.ppa.work_dir.expanduser() / FC.ppa.suite
    for step, status in result['steps'].items():
        # DRY: some steps only have an emoji, some others have details.
        # Compensate in the former case.
        if len(status) == 1:
            message.append(f'{status} {step}')
        else:
            # The following works for single and multiple lines:
            for line in status.splitlines():
                emoji = line[0]
                details = line[2:]
                # And we might adjust details:
                #  - for now, add a download link of that's a file in the
                #    suite's directory;
                #  - later, we might want lines with 3 links like this:
                #    [frida_<version>_<arch>.deb] [build log] [debdiff against <reference_version>]
                if step == 'publish' and (ppa_suite_path / details).exists():
                    # Direct download link to packages, debdiffs, build logs, etc.:
                    details = f'[`{details}`]({FC.ppa.publish_url}{FC.ppa.suite}/{details})'
                message.append(f'{emoji} {step}: {details}')

    # The file indirection means we can keep the config file under revision
    # control without leaking the actual webhook URL:
    try:
        webhook_url = FC.discord.webhook_url_file.expanduser().read_text().strip()
        # As of 2024, 204 (No content) is documented as the status code for
        # successful webhook usage, but let's be flexible:
        reply = requests.post(webhook_url,
                              json={'content': '\n'.join(message)},
                              timeout=30)
        reply.raise_for_status()
        logging.debug('successfully notified about %s', version)
    except BaseException as ex:
        print(ex)
        logging.error('failed to notify about %s', version)
        sys.exit(1)


def process():
    """
    Iterate over the todo list.

    For each version in the todo list, we might have results. If operations were
    successful, skip the version, otherwise stop immediately: some human brain
    is needed.

    Otherwise, process that version, and update the state file accordingly. If
    that was a success, move to the next version, otherwise stop immediately:
    some human brain is needed.
    """
    state = yaml.safe_load(STATE_FILE.read_text())
    # TODO: which checks to perform on the git side when dealing with the first item?
    #  - matches the origin branch?
    #  - matches a tag for the last package? might not be true if extra work was done
    #  - some git branch should be set?

    # We need reference files:
    if 'reference' not in state:
        logging.error('please run --reference first, we need reference files to debdiff against')
        sys.exit(1)

    for version in state['todo']:
        # If we've seen this version already, either skip or stop:
        if version in state['results']:
            if state['results'][version]['success']:
                logging.debug('skipping %s, finished successfully', version)
                continue

            logging.debug('version %s did not finish successfully, stopping', version)
            sys.exit(1)

        # Otherwise: process, save results, notify, and maybe continue:
        logging.info('processing %s', version)
        result = process_one(version, state['reference'])
        state['results'][version] = result
        STATE_FILE.write_text(yaml.dump(state, sort_keys=False))
        notify(version, result)
        if not result['success']:
            logging.error('automated packaging of %s failed, stopping', version)
            sys.exit(1)

        if args.only_one:
            logging.debug('processing only one version as requested, stopping')
            sys.exit(0)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='Frida auto-packager')
    parser.add_argument('--detect', action='store_true')
    parser.add_argument('--reference', action='store_true')
    parser.add_argument('--process', action='store_true')
    parser.add_argument('--cheat', action='store_true')
    parser.add_argument('--only-one', action='store_true')
    parser.add_argument('--no-notify', action='store_true')
    args = parser.parse_args()

    FC = frido.config.init(CONFIG_FILE)
    FS = frido.state.init(STATE_FILE)

    if args.detect:
        detect()
    if args.reference:
        sync_reference(FC, FS)
    if args.process:
        process()
