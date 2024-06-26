"""
Notification management
"""

import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

import requests

from .config import FridoConfig
from .state import FridoStateResult


@dataclass
class NotifRefreshMetadata:
    """
    Store a title, an old value (if there's one already), and a new value.
    Both values might be equal.
    """
    title: str
    old: Optional[str]
    new: str


@dataclass
class NotifRefresh:
    """
    Collect information about Git and reference files, and the todo list.
    """
    # `field(…)` is used instead of `= []` to set a default value:
    metadata: list[NotifRefreshMetadata] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)

    def append_metadata(self, title: str, old: Optional[str], new: str):
        """Turn the 3 parameters into a proper NotifRefreshMetadata instance."""
        self.metadata.append(NotifRefreshMetadata(title, old, new))


def notify_send(fc: FridoConfig, message: str):
    """
    Actually send the notification to Discord.

    The file indirection means we can keep the config file under revision
    control without leaking the actual webhook URL.
    """
    try:
        webhook_url = fc.discord.webhook_url_file.expanduser().read_text().strip()
        # As of 2024, 204 (No content) is documented as the status code for
        # successful webhook usage, but let's be flexible:
        reply = requests.post(webhook_url,
                              json={'content': message},
                              timeout=30)
        reply.raise_for_status()
        logging.debug('successfully notified about refreshed data')
    except BaseException as ex:
        print(ex)
        logging.error('failed to notify about refreshed data')
        sys.exit(1)


def notify_build(fc: FridoConfig, version: str, result: FridoStateResult, print_only: bool = False):
    """
    Build a message for this version, and send it via a Discord webhook.
    """
    lines = []
    if result.success:
        lines.append(f'**Successful automatic packaging: {version}**')
    else:
        lines.append(f'**Failed automatic packaging: {version}**')

    ppa_suite_path = fc.ppa.work_dir / fc.ppa.suite
    for step, status in result.steps.items():
        # DRY: some steps only have an emoji, some others have details.
        # Compensate in the former case.
        if len(status) == 1:
            lines.append(f'{status} {step}')
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
                if step == 'publish_file' and (ppa_suite_path / details).exists():
                    # Direct download link to packages, debdiffs, build logs, etc.:
                    details = f'[`{details}`]({fc.ppa.publish_url}{fc.ppa.suite}/{details})'
                lines.append(f'{emoji} {step}: {details}')
    message = '\n'.join(lines).strip()

    # Print or send:
    if print_only:
        logging.debug('not sending the following notification, as requested')
        print(message)
    else:
        notify_send(fc, message)


def notify_refresh(fc: FridoConfig, notif: NotifRefresh, print_only: bool = False):
    """
    Build a message about refreshed data, and send it via a Discord webhook.

    We build a message based on title, old and new values for each metadata
    item, but we only output the block is something change, skipping it entirely
    otherwise.

    **Metadata update:**
     - Git upstream version: OLD → NEW
     - Git package version:  OLD~pirogue1 → NEW~pirogue1
     - PPA package version:  OLD~pirogue1 → NEW~pirogue1
     - Overall consistency:  ✅ or ❌

    **To do:**
     - NEW1
     - NEW2
    """
    # Build a metadata block:
    lines = ['**Metadata update:**']
    changes = False
    for metadata in notif.metadata:
        if metadata.old == metadata.new:
            lines.append(f' - {metadata.title}: `{metadata.old}`')
        else:
            lines.append(f' - {metadata.title}: `{metadata.old}` → **`{metadata.new}`**')
            changes = True
    # Initial decision: we don't keep the block if nothing changed.
    if not changes:
        lines = []

    # Add a to do block, only if there are new to do versions:
    if notif.todo:
        lines.append('\n**To do:**')
        for version in notif.todo:
            lines.append(f' - `{version}`')

    # Merge everything together:
    message = '\n'.join(lines).strip()
    if message == '':
        logging.debug('no changes, skipping notification')
        return

    # Print or send:
    if print_only:
        logging.debug('not sending the following notification, as requested')
        print(message)
    else:
        notify_send(fc, message)
