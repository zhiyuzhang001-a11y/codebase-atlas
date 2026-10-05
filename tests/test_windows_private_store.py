import unittest
import os
from pathlib import Path
import tempfile
from codebase_atlas.windows_private_store import validate_private_acl


class WindowsPrivateAclPolicyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native Windows ACL requires Windows")
    def test_native_private_directory_owner_and_acl_are_verified(self):
        from codebase_atlas.windows_private_store import verify_windows_private_path
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "owned-private"
            path.mkdir(mode=0o700)
            verify_windows_private_path(path)

    def test_private_owner_system_and_administrators_are_admitted(self):
        validate_private_acl("user", "user", [(0, 0x1f01ff, "user"), (0, 0x1f01ff, "S-1-5-18"),
                                               (0, 0x1f01ff, "S-1-5-32-544"), (1, 1, "foreign")])

    def test_wrong_owner_null_acl_and_foreign_read_or_write_are_rejected(self):
        cases = [("foreign", [(0, 1, "user")]), ("user", []),
                 ("user", [(0, 1, "foreign")]), ("user", [(0, 2, "foreign")]),
                 ("user", [(5, 1, "user")])]
        for owner, entries in cases:
            with self.subTest(owner=owner, entries=entries), self.assertRaises(ValueError):
                validate_private_acl(owner, "user", entries)
