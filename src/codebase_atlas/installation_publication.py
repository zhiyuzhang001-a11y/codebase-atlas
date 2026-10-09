"""Publish a private staged installation without replacing existing paths."""
import os
from pathlib import Path
import stat


def publish_installation(staging: Path, destination: Path, *, receipt: str, validate):
    """Reserve each path exclusively; roll back only this operation's inodes.

    A missing receipt makes interrupted publication fail closed. This does not
    delete unknown entries or silently repair a previously incomplete install.
    """
    owned = []
    def remember(path):
        metadata = os.lstat(path)
        owned.append((path, metadata.st_dev, metadata.st_ino, stat.S_ISDIR(metadata.st_mode)))
    def publish(source, target):
        metadata = os.lstat(source)
        if stat.S_ISDIR(metadata.st_mode):
            target.mkdir(mode=stat.S_IMODE(metadata.st_mode))
            remember(target)
            for child in source.iterdir():
                publish(child, target / child.name)
        elif stat.S_ISLNK(metadata.st_mode):
            os.symlink(os.readlink(source), target)
            remember(target)
        elif stat.S_ISREG(metadata.st_mode):
            os.link(source, target)
            remember(target)
        else:
            raise RuntimeError("Installation staging contains an unsafe file")
    try:
        destination.mkdir(mode=0o700)
        remember(destination)
        for source in staging.iterdir():
            if source.name != receipt:
                publish(source, destination / source.name)
        publish(staging / receipt, destination / receipt)
        return validate()
    except BaseException:
        for path, device, inode, directory in reversed(owned):
            try:
                metadata = os.lstat(path)
                if (metadata.st_dev, metadata.st_ino) != (device, inode):
                    continue
                if directory:
                    path.rmdir()  # Unknown added entries are never removed.
                else:
                    path.unlink()
            except OSError:
                pass
        raise
