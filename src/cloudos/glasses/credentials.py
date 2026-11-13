"""Store Jarvis glasses secrets in Windows Credential Manager, never in Git."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


TARGET = "CloudAIOS/JarvisGlasses/VisorToken"
AUDIT_DATABASE_URL_TARGET = "CloudAIOS/JarvisGlasses/AuditDatabaseUrl"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CredentialError(RuntimeError):
    pass


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _require_windows():
    if os.name != "nt":
        raise CredentialError("Windows Credential Manager is required on the Jarvis PC.")
    return ctypes.WinDLL("Advapi32.dll")


def _save_secret(target: str, secret: str, *, empty_message: str, write_error: str) -> None:
    if not secret.strip():
        raise ValueError(empty_message)
    advapi = _require_windows()
    encoded = secret.strip().encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = CREDENTIALW(
        Type=CRED_TYPE_GENERIC,
        TargetName=target,
        CredentialBlobSize=len(encoded),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName=os.environ.get("USERNAME", "Jarvis"),
    )
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    if not advapi.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"{write_error} (Windows error {ctypes.get_last_error()}).")


def _load_secret(target: str, *, missing_message: str) -> str:
    advapi = _require_windows()
    pointer = ctypes.POINTER(CREDENTIALW)()
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        raise CredentialError(missing_message)
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        advapi.CredFree(pointer)


def save_token(token: str) -> None:
    _save_secret(
        TARGET,
        token,
        empty_message="The Visor token cannot be empty.",
        write_error="Could not save the Visor token",
    )


def load_token() -> str:
    return _load_secret(
        TARGET,
        missing_message="The Visor token has not been configured for this Windows account.",
    )


def save_audit_database_url(url: str) -> None:
    _save_secret(
        AUDIT_DATABASE_URL_TARGET,
        url,
        empty_message="The Jarvis audit database URL cannot be empty.",
        write_error="Could not save the Jarvis audit database URL",
    )


def load_audit_database_url() -> str:
    return _load_secret(
        AUDIT_DATABASE_URL_TARGET,
        missing_message="The Jarvis audit database URL has not been configured for this Windows account.",
    )


def ensure_audit_database_url_env() -> bool:
    """Load Credential Manager URL into JARVIS_AUDIT_DATABASE_URL when unset."""
    if os.environ.get("JARVIS_AUDIT_DATABASE_URL", "").strip():
        return True
    try:
        os.environ["JARVIS_AUDIT_DATABASE_URL"] = load_audit_database_url().strip()
    except CredentialError:
        return False
    return bool(os.environ.get("JARVIS_AUDIT_DATABASE_URL", "").strip())
