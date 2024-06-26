"""
Notification management
"""

import logging
import sys

import requests

from .config import FridoConfig
from .state import FridoStateResult


def notify_build(fc: FridoConfig, version: str, result: FridoStateResult):
    """
    Build a message for this version, and send it via a Discord webhook.
    """
    message = []
    if result.success:
        message.append(f'**Successful automatic packaging: {version}**')
    else:
        message.append(f'**Failed automatic packaging: {version}**')

    ppa_suite_path = fc.ppa.work_dir.expanduser() / fc.ppa.suite
    for step, status in result.steps.items():
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
                    details = f'[`{details}`]({fc.ppa.publish_url}{fc.ppa.suite}/{details})'
                message.append(f'{emoji} {step}: {details}')

    # The file indirection means we can keep the config file under revision
    # control without leaking the actual webhook URL:
    try:
        webhook_url = fc.discord.webhook_url_file.expanduser().read_text().strip()
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
