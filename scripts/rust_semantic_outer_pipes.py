"""Prepared sole outer FD owner for C0-O5, not a launcher or supervisor.

Only allocate() creates native objects, under an explicitly reviewed caller.
No CLI, spawn, arbitrary inherited FD adoption or signal/reap entry. Tests
inject every OS call. The caller must remain sole-threaded and never close or
replace borrowed FDs; validation cannot defeat a concurrent descriptor swap.
"""
import os
import stat
import sys


class OuterPipes:
    STREAMS = ('stderr', 'stdout', 'trace')

    def __init__(self, os_api=None):
        if sys.platform != 'linux':
            raise ValueError('Linux owned control pipes required')
        self.os = os if os_api is None else os_api
        self.started = self.ready = False
        self.owned, self.receipts, self.errors = {}, [], []
        self.reads, self.writes = {}, {}

    def _identity(self, fd, write):
        value = self.os.fstat(fd)
        if (not stat.S_ISFIFO(value.st_mode) or value.st_uid != self.os.getuid()
                or self.os.get_inheritable(fd)
                or self.os.get_blocking(fd) is not write):
            raise ValueError('owned CLOEXEC pipe direction/blocking contract')
        return {'device': value.st_dev, 'inode': value.st_ino,
                'uid': value.st_uid, 'type': stat.S_IFMT(value.st_mode)}

    def allocate(self):
        if self.started:
            raise RuntimeError('one allocation attempt only; never reopen uncertain FDs')
        self.started = True
        try:
            for name in self.STREAMS:
                pair = self.os.pipe2(self.os.O_CLOEXEC)
                if type(pair) is not tuple or len(pair) != 2:
                    raise ValueError('native pipe2 pair required')
                # Take ownership immediately, including before flag/receipt checks.
                for fd in pair:
                    if type(fd) is not int or fd < 0 or fd in self.owned:
                        raise ValueError('distinct native created descriptors required')
                    self.owned[fd] = None
                read, write = pair
                self.reads[name], self.writes[name] = read, write
                receipt = {'stream': name, 'read_fd': read, 'write_fd': write,
                           'identity': None, 'validated': False}
                self.receipts.append(receipt)
                if not all(3 <= fd <= 65535 for fd in pair):
                    raise ValueError('created control FD outside frozen bounds')
                self.os.set_blocking(read, False)
                left, right = self._identity(read, False), self._identity(write, True)
                if left != right or any(row['identity'] == left for row in self.receipts[:-1]):
                    raise ValueError('distinct stream pipe inode required')
                self.owned[read], self.owned[write] = left, right
                receipt.update(identity=left, validated=True)
            self.ready = True
            return self.borrow_reads(), self.borrow_writes()
        except Exception as exc:
            self.errors.append({'operation': 'allocate', 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})
            self.close_all()
            raise

    def _borrow(self, mapping, write):
        if not self.ready:
            raise RuntimeError('complete created-pipe receipt required')
        for name, fd in mapping.items():
            if fd not in self.owned or self._identity(fd, write) != self.owned[fd]:
                raise ValueError('created pipe no longer owned or unchanged')
        return dict(mapping)

    def borrow_reads(self):
        return self._borrow(self.reads, False)

    def borrow_writes(self):
        # Only the reviewed fixed observer launcher may use this copy. Passing
        # these to an arbitrary process is outside this preparation contract.
        return self._borrow(self.writes, True)

    def _close(self, descriptors):
        for fd in descriptors:
            if fd not in self.owned:
                continue
            identity = self.owned.pop(fd)  # uncertain close never retried
            try:
                if identity is not None:
                    current = self.os.fstat(fd)
                    found = {'device': current.st_dev, 'inode': current.st_ino,
                             'uid': current.st_uid, 'type': stat.S_IFMT(current.st_mode)}
                    if found != identity:
                        raise ValueError('FD replaced; refuse closing foreign object')
                self.os.close(fd)
            except (OSError, ValueError) as exc:
                self.errors.append({'fd': fd, 'operation': 'close',
                                    'type': type(exc).__name__,
                                    'errno': getattr(exc, 'errno', None)})

    def close_parent_writes(self):
        """After reviewed spawn has inherited writers; never proof of child exit."""
        self._close(list(self.writes.values()))

    def close_all(self):
        self.ready = False
        self._close(list(self.owned))

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'ready': self.ready, 'owned_fds': sorted(self.owned),
                'receipts': [dict(row, identity=None if row['identity'] is None
                                 else dict(row['identity']))
                             for row in self.receipts],
                'errors': [dict(row) for row in self.errors]}
