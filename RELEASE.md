# How to publish a new release

## Step 1: update the PPA

The first step means opening a pull request against the git repository for the
PPA (`debian-12` as of 2024), adding the latest `amd64` and `arm64` builds to
the `pirogue-3rd-party` directory. It's best not to use the `./ppa --refresh`
command to refresh `Packages*` and `Release*` while doing so, as that'd generate
conflicts if the branch isn't reviewed/merged right away, but that also means
`./ppa --refresh` must not be forgotten when the merge happens.

*Note: there's a `./ppa --check` command as well, that will be used in a GitHub
action at some point, to make sure any oversight is spotted, reported, and fixed
swiftly.*

Example pull request: https://github.com/PiRogueToolSuite/debian-12/pull/6


## Step 2: update the git repository

Once the packages have been tested successfully and merged into the PPA, one
needs to record that on the git side as well, advancing the packaging branch
(`debian/bookworm` as of 2024) to the tag matching the version that was just
published.

For the pull request above, that means doing the following in a `deb-frida.git`
checkout. It's best to use the actual tag rather than the automated packaging
branch (`auto/debian/bookworm` as of 2024) as that branch might have been
updated in the meanwhile, if new builds happened between step 1 and step 2.

    git checkout debian/bookworm
    git merge auto/debian/16.4.8_pirogue1
    # not really required, but for consistency:
    git submodule update --init --recursive
    git push origin HEAD

We also want to make the release official with a “real” tag and not just an
“automated” one (which can be updated a few times when a build is considered to
be broken and requires a do-over — or several).

There are two solutions regarding that tag, either create it with the default
settings (meaning it'll appear as being close to the publication date), or
create it with a timestamp matching the original tag (meaning it'll appear as
being close to the build date).

At first glance it seems the former is the most informative. If someone cares
about the build date, that's available in `debian/changelog` and in git commit
dates.

    gbp tag
    git push origin $(git describe)


## Step 3: merge pending work (if anything)

Some work might have been staged in branches, and merging it at this point makes
sense. For example:

    git merge gbp-configuration
    git merge disable-source-maps
    git push origin HEAD

Of course, for that to work, one must have stopped the regular builds, otherwise
the automated packaging branch might have moved on already, meaning a divergence
between the regular packaging branch and the automated one.

In this case, re-cron `frido.py` in the VM hosting automated builds, at which
point the automated packaging branch should get fast-forwarded to the tip of the
regular packaging branch.

Otherwise (e.g. two more builds happened already): rewind the automated
packaging branch to the appropriate commit, and cheat by editing the
`state.yaml` file, pretending those builds didn't happen. They'll get a do-over,
which can be confirmed with the ❗ emoji next to the files having been
overwritten.


## Bonus: clean up old releases (once in a blue moon)

As discussed in `debian-12`'s [PR#12](https://github.com/PiRogueToolSuite/debian-12/pull/22),
it seems to make sense to only keep builds for two upstream series in the
repository. Implementing some automated cleanup might take time and might be
risky, so here are the few manual steps required to perform that cleanup.

Example: 16.7.\* and 17.0.\* are current, 16.6.\* goes away, all of this built
for `bookworm`.

 - On the machine performing builds, enter the directory configured via
   `ppa.work_dir`.
 - Remove the old series, e.g. `rm frida-bookworm/frida_16.6.*`
 - Run the sync command configured via `ppa.publish_wrapper`, enabling
   deletions. The recommended configuration is `rsync`, without any
   `--delete*`, so a first step can be using `--delete --dry-run`. If that
   looks good, drop `--dry-run`.

The `Packages*` and `Release*` files are still going to need a refresh to stop
referencing those long-obsolete packages, but that can wait until the next
successful build.
