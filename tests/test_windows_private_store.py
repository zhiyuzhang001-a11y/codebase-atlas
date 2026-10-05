import unittest
import os
from pathlib import Path
import tempfile
from codebase_atlas.windows_private_store import validate_private_acl


class WindowsPrivateAclPolicyTests(unittest.TestCase):
    def test_verified_elevated_default_owner_requires_private_current_account_grant(self):
        facts = dict(token_owner="S-1-5-32-544", elevated=True, administrator_enabled=True)
        entries = [(0, 0x1f01ff, "user"), (0, 0x1f01ff, "S-1-5-18"),
                   (0, 0x1f01ff, "S-1-5-32-544")]
        validate_private_acl("S-1-5-32-544", "user", entries, **facts)
        for changed in (dict(token_owner="user"), dict(elevated=False),
                        dict(administrator_enabled=False), dict(elevated=1)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_private_acl("S-1-5-32-544", "user", entries, **(facts | changed))
        for rejected in ([], entries[1:], [(0, 1, "user")],
                         entries + [(0, 1, "foreign")], entries + [(1, 1, "user")],
                         entries + [(5, 1, "user")]):
            with self.subTest(entries=rejected), self.assertRaises(ValueError):
                validate_private_acl("S-1-5-32-544", "user", rejected, **facts)
        for owner in ("foreign", "S-1-5-18"):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                validate_private_acl(owner, "user", entries, **(facts | dict(token_owner=owner)))

    def test_owner_diagnostic_does_not_admit_administrator_or_expose_sid(self):
        with self.assertRaisesRegex(ValueError, r"owner=administrators, ace_count=1") as failure:
            validate_private_acl("S-1-5-32-544", "private-user-sid", [(0, 1, "private-user-sid")])
        self.assertNotIn("private-user-sid", str(failure.exception))
        self.assertNotIn("S-1-5-32-544", str(failure.exception))

    @unittest.skipUnless(os.name == "nt", "native Windows ACL requires Windows")
    def test_native_private_directory_owner_and_acl_are_verified(self):
        from codebase_atlas.windows_private_store import verify_windows_private_path
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "owned-private"
            path.mkdir(mode=0o700)
            verify_windows_private_path(path)
            receipt = path / "receipt.json"
            receipt.write_bytes(b"{}")
            verify_windows_private_path(receipt)

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
