"""Read-only Windows owner/DACL checks; never invoke a shell or modify ACLs."""
import ctypes
from ctypes import wintypes
import os


def validate_private_acl(owner, current_user, entries, *, token_owner=None,
                         elevated=False, administrator_enabled=False):
    # Windows elevated processes may create objects owned by their token's
    # Administrators default owner. This is NOT a general group-owner exception:
    # all three facts come from the same native process token, and the current
    # account must itself have explicit full access. Existing trusted SYSTEM /
    # Administrators ACEs remain the only other grants admitted.
    administrator_owner = (owner == "S-1-5-32-544" and token_owner == owner
                           and elevated is True and administrator_enabled is True
                           and any(kind == 0 and sid == current_user
                                   and mask & 0x1f01ff == 0x1f01ff
                                   for kind, mask, sid in entries)
                           and not any(kind == 1 and mask for kind, mask, sid in entries))
    if (owner != current_user and not administrator_owner) or not entries:
        # Report categories, not account SIDs or paths. Elevated Windows runners
        # may use a different default owner; never admit it without verified
        # token facts or rewrite an existing directory's ACL.
        relation = ("current-user" if owner == current_user else
                    "administrators" if owner == "S-1-5-32-544" else "foreign")
        raise ValueError("Windows store must have an owned, explicit DACL "
                         f"(owner={relation}, ace_count={len(entries)})")
    trusted = {current_user, "S-1-5-18", "S-1-5-32-544"}  # SYSTEM, Administrators
    for ace_type, mask, sid in entries:
        if ace_type == 1:  # Deny entries cannot grant access.
            continue
        if ace_type != 0 or (mask and sid not in trusted):
            raise ValueError("Windows store grants foreign or unrecognized access")


def verify_windows_private_path(path):
    if os.name != "nt":
        raise ValueError("Windows ACL verification requires Windows")
    # LOAD_LIBRARY_SEARCH_SYSTEM32: do not load an attacker-selected DLL from
    # the project or inherited search path.
    advapi = ctypes.WinDLL("advapi32.dll", use_last_error=True, winmode=0x800)
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    pointer = ctypes.c_void_p
    pp = ctypes.POINTER(pointer)
    def function(library, name, arguments, result):
        selected = getattr(library, name)
        selected.argtypes, selected.restype = arguments, result
        return selected
    open_token = function(advapi, "OpenProcessToken", [wintypes.HANDLE, wintypes.DWORD, pp], wintypes.BOOL)
    token_info = function(advapi, "GetTokenInformation", [wintypes.HANDLE, ctypes.c_int, pointer,
                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    duplicate = function(advapi, "DuplicateToken", [wintypes.HANDLE, ctypes.c_int, pp], wintypes.BOOL)
    membership = function(advapi, "CheckTokenMembership", [wintypes.HANDLE, pointer,
                          ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
    security = function(advapi, "GetNamedSecurityInfoW", [wintypes.LPCWSTR, ctypes.c_int,
                        wintypes.DWORD, pp, pp, pp, pp, pp], wintypes.DWORD)
    get_ace = function(advapi, "GetAce", [pointer, wintypes.DWORD, pp], wintypes.BOOL)
    sid_string = function(advapi, "ConvertSidToStringSidW", [pointer, pp], wintypes.BOOL)
    local_free = function(kernel, "LocalFree", [pointer], pointer)
    close = function(kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
    current_process = function(kernel, "GetCurrentProcess", [], wintypes.HANDLE)
    class ACL(ctypes.Structure):
        _fields_ = [("revision", wintypes.BYTE), ("reserved", wintypes.BYTE),
                    ("size", wintypes.WORD), ("count", wintypes.WORD), ("reserved2", wintypes.WORD)]
    class ACE(ctypes.Structure):
        _fields_ = [("kind", wintypes.BYTE), ("flags", wintypes.BYTE), ("size", wintypes.WORD),
                    ("mask", wintypes.DWORD), ("sid_start", wintypes.DWORD)]
    def stringify(sid):
        result = pointer()
        if not sid or not sid_string(sid, ctypes.byref(result)):
            raise ValueError("Windows SID cannot be verified")
        try:
            return ctypes.wstring_at(result)
        finally:
            local_free(result)
    token, descriptor, owner, dacl, duplicate_token = (pointer() for _ in range(5))
    try:
        if not open_token(current_process(), 0xa, ctypes.byref(token)):  # QUERY | DUPLICATE
            raise ValueError("Windows process owner cannot be verified")
        size = wintypes.DWORD()
        token_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not 0 < size.value <= 65536:
            raise ValueError("Windows token information is invalid")
        buffer = ctypes.create_string_buffer(size.value)
        if not token_info(token, 1, buffer, size.value, ctypes.byref(size)):
            raise ValueError("Windows token information is unavailable")
        user = stringify(pointer.from_buffer(buffer).value)
        if security(str(path), 1, 0x5, ctypes.byref(owner), None, ctypes.byref(dacl),
                    None, ctypes.byref(descriptor)) != 0 or not owner or not dacl:
            raise ValueError("Windows store security descriptor is unavailable")
        owner_text = stringify(owner)
        default_owner, elevated, administrator_enabled = None, False, False
        if owner_text == "S-1-5-32-544" and owner_text != user:
            size = wintypes.DWORD()
            token_info(token, 4, None, 0, ctypes.byref(size))  # TokenOwner
            if not ctypes.sizeof(pointer) <= size.value <= 65536:
                raise ValueError("Windows default owner information is invalid")
            owner_buffer = ctypes.create_string_buffer(size.value)
            if not token_info(token, 4, owner_buffer, size.value, ctypes.byref(size)):
                raise ValueError("Windows default owner is unavailable")
            default_sid = pointer.from_buffer(owner_buffer).value
            default_owner = stringify(default_sid)
            elevation = wintypes.DWORD()
            if not token_info(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation),
                              ctypes.byref(size)) or size.value != ctypes.sizeof(elevation):
                raise ValueError("Windows token elevation is unavailable")
            elevated = elevation.value == 1
            # Membership requires an impersonation token. Duplicate explicitly
            # rather than implicitly consulting an unrelated thread token.
            if not duplicate(token, 1, ctypes.byref(duplicate_token)):  # SecurityIdentification
                raise ValueError("Windows token membership cannot be verified")
            enabled = wintypes.BOOL()
            if not membership(duplicate_token, default_sid, ctypes.byref(enabled)):
                raise ValueError("Windows token membership is unavailable")
            administrator_enabled = default_owner == "S-1-5-32-544" and bool(enabled.value)
        header = ACL.from_address(dacl.value)
        if header.size < ctypes.sizeof(ACL) or header.count > 4096:
            raise ValueError("Windows store ACL is invalid")
        entries = []
        for index in range(header.count):
            raw = pointer()
            if not get_ace(dacl, index, ctypes.byref(raw)) or not raw:
                raise ValueError("Windows store ACE is unavailable")
            if not dacl.value <= raw.value <= dacl.value + header.size - ctypes.sizeof(ACE):
                raise ValueError("Windows store ACE is outside the ACL")
            ace = ACE.from_address(raw.value)
            if ace.size < ctypes.sizeof(ACE) or raw.value + ace.size > dacl.value + header.size:
                raise ValueError("Windows store ACE is invalid")
            # Unknown/object/callback ACE layouts are intentionally rejected.
            if ace.kind not in (0, 1):
                raise ValueError("Windows store ACE type requires review")
            # An inherit-only ACE cannot prove current-account access. Foreign
            # trustees remain rejected even when their grants are inherit-only.
            sid = stringify(raw.value + ACE.sid_start.offset)
            if ace.flags & 0x8 and sid == user and ace.kind == 0:
                continue
            entries.append((ace.kind, ace.mask, sid))
        validate_private_acl(owner_text, user, entries, token_owner=default_owner,
                             elevated=elevated, administrator_enabled=administrator_enabled)
    finally:
        if duplicate_token:
            close(duplicate_token)
        if descriptor:
            local_free(descriptor)
        if token:
            close(token)
