# deb-frido

## Introduction

This is a companion repository for the `deb-frida` one, with the following
goals:

 - detect new upstream versions (comparing upstream tags to the latest Debian
   tag);
 - prepare an updated packaging (merging and bumping changelog as usual);
 - check patches still apply;
 - check building goes fine (first on `amd64`, then on `arm64` if successful);
 - integrate successfully built packages into a staging Debian repository;
 - notify PTS developers about the results, positive or negative.


## Architecture

This tool should be running on a system which has:

 - read access to the upstream frida repositories (main repository and
   submodules);
 - write access to the PTS `deb-frida` repository (to push branches with
   preparatory work, and possibly temporary tags to be reviewed by PTS
   developers);
 - access to a GPG signing key to sign the staging Debian repository;
 - access to a server on which the staging Debian repository can be published;
 - access to a Discord webhook to let PTS developers know about results.

At least at the beginning, `amd64` builds are expected to be native, either
directly on the system, or inside an `schroot`-controlled chroot. On the
contrary, `arm64` builds are expected to be done only inside an
`schroot`-controlled chroot, thanks to `qemu-system-arm` and `qemu-user-static`.

It would be better if we could consider cleaner builds thanks to `sbuild` or
`cowbuilder`, but building a proper source package would eat up a lot of
resource (because of the size of the main repository and submodules), and we
would need to enable network access during the build because of the
toolchain/SDK downloads anyway.


## Requirements

The following packages are required for native builds, in addition to the
build-dependencies listed in `debian/control`:

    # Basic building tools:
    sudo apt-get install -y build-essential devscripts git git-buildpackage

    # Dependencies for frido:
    sudo apt-get install -y apt-utils gpg python3 python3-debian python3-packaging \
        python3-pydantic python3-requests python3-yaml rsync

The following packages are extra dependencies, to handle `arm64` crossbuilds:

    sudo apt-get install -y debootstrap schroot qemu-system-arm qemu-user-static
