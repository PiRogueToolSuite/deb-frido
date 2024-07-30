"""
Action management: instead of multiplying subprocess.check_output() and
subprocess.check_call() calls in the various steps needed to process a
version, let's list all commands that need to be run.
"""

import logging
import re
import shlex
import subprocess


# Important: dependencies indicated in comments are for information only, but
# the run_actions() method does parse the various actions, looking for string
# variables, and checks the variables dict contain all the required keys.

ACTIONS = {
    'clean': [
        # Dependencies: none.
        'git checkout -f',
        'git clean -xdf',
        'git submodule foreach --recursive git checkout -f',
        'git submodule foreach --recursive git clean -xdf',
    ],
    'prepare': [
        # Dependencies: uversion and dversion.
        'git merge %(uversion)s',
        'git submodule update --init --recursive',
        'dch -v %(dversion)s "New upstream release."',
        'git add debian/changelog',
        'git commit -m "Bump changelog."',
    ],
    'patch': [
        # Dependencies: dversion and tagformat.
        'quilt push -a',
        'quilt pop -a',
        'rm -rf .pc',
        'dch -r ""',
        'git add debian/changelog',
        'git commit -m "Release %(dversion)s"',
        # Generating a new auto tag might be needed while fixing things up,
        # hence --retag to avoid failures if tags weren't deleted manually:
        'gbp tag --retag --ignore-branch --debian-tag %(tagformat)s',
    ],
    'push': [
        # Dependencies: remote, branch, and tag.
        'git push -q -f %(remote)s HEAD:%(branch)s',
        'git push -q -f %(remote)s %(tag)s',
    ],
}

def required_variables(actions):
    """
    Detect variables that are needed for the interpolation to work
    correctly.

    For now, concentrate on string variables.
    """
    results = []
    for action in actions:
        interpolations = re.findall(r'\%\((.+?)\)s', action)
        results.extend(interpolations)
    return sorted(set(results))


def run_actions(step, variables):
    """
    Perform actions for the specified step, checking for required
    variables, then interpolating.
    """
    try:
        actions = ACTIONS[step]
    except KeyError:
        raise RuntimeError(f'no known actions for step={step}')

    # Detect required variables:
    dependencies = required_variables(actions)
    for dependency in dependencies:
        if dependency not in variables:
            raise RuntimeError(f'missing required variable={dependency}')

    for action in actions:
        cmd = shlex.split(action % variables)
        try:
            subprocess.check_output(cmd)
        except subprocess.CalledProcessError as ex:
            # With check_output, stderr is visible directly; let's log the rest
            # of the output (== stdout) for completeness, while stderr is None:
            logging.error('Failure while running %s:', ex.cmd)
            for line in ex.stdout.decode().splitlines():
                logging.error('  %s', line)
            raise ex


if __name__ == '__main__':
    for test_step, test_actions in ACTIONS.items():
        print(f'step: {test_step}')
        print(required_variables(test_actions))
        print()
