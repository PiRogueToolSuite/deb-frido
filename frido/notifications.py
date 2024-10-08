"""
Notification management
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Tuple

import requests

from .config import FridoConfig
from .state import FridoStateResult, SUCCESS, WARNING


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


@dataclass
class NotifMonitoringPackage:
    """
    Collect info about a given monitored package.
    """
    package: str
    old_version: Optional[str]
    version: str
    url: str
    # Let's go for a package to url mapping, to manage (new) dependencies:
    depends: dict[str, str]


@dataclass
class NotifMonitoring:
    """
    Collect info about a given monitored repository.
    """
    repo: str
    suite: str
    component: str
    architecture: str
    # `field(…)` is used instead of `= []` to set a default value:
    packages: list[NotifMonitoringPackage] = field(default_factory=list)


def notify_send(fc: FridoConfig, message: str, topic: str):
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
        logging.debug('successfully notified about %s', topic)
    except BaseException as ex:
        print(ex)
        logging.error('failed to notify about %s', topic)
        sys.exit(1)


def combine_files(fc: FridoConfig,
                  step: str,
                  reference_dversion: str,
                  lines: list[str]
                  ) -> Tuple[list[str], dict[str, str]]:
    """
    Initially we would iterate over the list and include many links, but
    what we care about is really the deb files, with build logs and debdiffs
    deserving a little less emphasis.

    Let's go for the following format, with emoji being either SUCCESS or
    WARNING depending on the worst case across all files:

    (emoji) [frida_<version>_<arch>.deb] — [build log] — [debdiff against <reference version>]

    Do that when both support files are present for a given .deb, and let the
    caller iterate over any remaining files in the original fashion.
    """
    # Convert lines into a dict with files as keys, emojis as values:
    files = {line[2:]: line[0] for line in lines}

    # Try and combine files for each .deb:
    combined_lines = []
    debs = sorted([x for x in files.keys() if x.endswith('.deb')])
    for deb in debs:
        build = re.sub(r'\.deb$', '.build', deb)
        debdiff = re.sub(r'\.deb$', '.debdiff.txt', deb)
        if build in files and debdiff in files:
            # Since the caller checked the overall status is a success we can
            # only have SUCCESS and WARNING here:
            emoji = WARNING if WARNING in [files[deb], files[build], files[debdiff]] else SUCCESS
            base_url = f'{fc.ppa.publish_url}{fc.ppa.suite}'

            combined_lines.append(
                f'{emoji} {step}:'
                f' [`{deb}`]({base_url}/{deb})'
                f' — [build log]({base_url}/{build})'
                f' — [debdiff against {reference_dversion}]({base_url}/{debdiff})'
            )
            # Forget all those files that go together:
            del files[deb]
            del files[build]
            del files[debdiff]
    return combined_lines, files


def notify_build(fc: FridoConfig,
                 uversion: str,
                 reference_dversion: str,
                 result: FridoStateResult,
                 print_only: bool = False):
    """
    Build a message for this version, and send it via a Discord webhook.
    """
    lines = []
    if result.success:
        lines.append(f'**Successful automatic packaging: {uversion}**')
    else:
        lines.append(f'**Failed automatic packaging: {uversion}**')

    for step, status in result.steps.items():
        # DRY: some steps only have an emoji, some others have details.
        # Compensate in the former case.
        if len(status) == 1:
            lines.append(f'{status} {step}')
        elif step == 'publish_file' and result.success:
            # Combine .deb with their build log and debdiff files if present,
            # but include a fallback if some files are missing, and also for any
            # other files that might be present.
            #
            # We only use this format if the overall result is a success.
            # Otherwise we fall back to the initial implementation: the failing
            # step might be publish_file, in which case we want some linear
            # view.
            combined_lines, remaining_items = combine_files(
                fc, step, reference_dversion, status.splitlines()
            )
            lines.extend(combined_lines)
            for item, emoji in remaining_items.items():
                lines.append(f'{emoji} {step}: {item}')
        else:
            # The following works for single and multiple lines:
            for line in status.splitlines():
                emoji = line[0]
                details = line[2:]
                if step == 'publish_file' and (fc.ppa.work_dir / fc.ppa.suite / details).exists():
                    # Direct download link to any file available in the PPA:
                    details = f'[`{details}`]({fc.ppa.publish_url}{fc.ppa.suite}/{details})'
                lines.append(f'{emoji} {step}: {details}')
    message = '\n'.join(lines).strip()

    # Print or send:
    if print_only:
        logging.debug('not sending the following notification, as requested')
        print(message)
    else:
        notify_send(fc, message, f'building version {uversion}')


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
            lines.append(f'- {metadata.title}: `{metadata.old}`')
        else:
            lines.append(f'- {metadata.title}: `{metadata.old}` → **`{metadata.new}`**')
            changes = True
    # Initial decision: we don't keep the block if nothing changed.
    if not changes:
        lines = []

    # Add a to do block, only if there are new to do versions:
    if notif.todo:
        lines.append('\n**To do:**')
        for version in notif.todo:
            lines.append(f'- `{version}`')

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
        notify_send(fc, message, 'refreshing data')


def url_to_filename(url):
    """
    Extract the filename part of the specified URL.

    This seems to be required to make sure links are interpreted correctly by
    Discord.
    """
    return re.sub(r'^.+/', '', url)


def notify_monitoring(fc: FridoConfig, notif: NotifMonitoring, print_only: bool = False):
    """
    Sample notification:

    **Package monitoring: updated firmware-brcm80211**
    - Repository: Raspberry OS
    - Suite: bookworm
    - Component: main
    - Architecture: arm64
    - Package: `firmware-brcm80211`
    - Version: `20230625-2+rpt2` → `1:20230625-2+rpt3`
    - Download: [https://archive.raspberrypi.com/…](https://archive.raspberrypi.com/…)
    - Dependencies: none
    """
    # NOTE: Sending one message per package, to make sure each of them can be
    # spotted and managed separately.
    for package in notif.packages:
        lines = []
        if package.old_version:
            lines.append(f'**Package monitoring: updated {package.package}**')
        else:
            lines.append(f'**Package monitoring: new {package.package}**')
        lines.append(f'- Repository: {notif.repo}')
        lines.append(f'- Suite: {notif.suite}')
        lines.append(f'- Component: {notif.component}')
        lines.append(f'- Architecture: {notif.architecture}')
        lines.append(f'- Package: `{package.package}`')
        if package.old_version:
            lines.append(f'- Version: `{package.old_version}` → `{package.version}`')
        else:
            lines.append(f'- Version: `{package.version}`')
        lines.append(f'- Download: [{url_to_filename(package.url)}]({package.url})')
        if package.depends:
            lines.append('- Dependencies:')
            for dep, url in package.depends.items():
                if url != '???':
                    lines.append(f'   - {dep}: [{url_to_filename(url)}]({url})')
                else:
                    lines.append(f'   - {dep}')
        else:
            lines.append('- Dependencies: none')

        message = '\n'.join(lines).strip()
        # Print or send:
        if print_only:
            logging.debug('not sending the following notification, as requested')
            print(message)
        else:
            notify_send(fc, message, f'monitoring package: {package.package}')
