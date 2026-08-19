"""What to read a file's body from, and whether to read it at all.

Pure policy, no I/O, so the decision can be asserted in tests and reproduced on
replay. It exists as its own module because it is the only place quota is spent
in bulk: a body read costs 200 units against 5 for metadata, so "should we fetch
this" is a 40x question, not a detail.
"""

from __future__ import annotations

from enum import StrEnum

from connectors.drive.models import DriveFile

# Native editor files must be exported; these are the ones with a useful text
# projection. Drawings and Forms are deliberately absent — their exports are
# images or empty, so they stay metadata-only nodes.
NATIVE_EXPORT_MIME: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

# Uploaded blobs worth reading as text. Anything else (images, audio, video,
# archives, office binaries) becomes a node with a title and a URL but no body;
# extracting those needs a parser we do not have yet, and downloading them to
# discover that would be the most expensive possible no-op.
TEXT_BLOB_MIMES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/rtf",
        "image/svg+xml",
    }
)
TEXT_BLOB_PREFIXES: tuple[str, ...] = ("text/",)

# Drive's own export ceiling is 10MB; past that the call fails rather than
# truncating. Staying under it keeps a huge spreadsheet from failing the whole
# cycle.
MAX_BODY_BYTES = 5_000_000

# Bodies are retrieval context, not archives. A multi-megabyte body bloats every
# downstream row for text nobody reads past the first screen.
MAX_BODY_CHARS = 200_000


class BodySource(StrEnum):
    EXPORT = "export"
    DOWNLOAD = "download"
    NONE = "none"


def body_source(file: DriveFile) -> tuple[BodySource, str | None]:
    """How this file's text should be read, and with which export mimeType."""
    if file.is_folder or file.is_shortcut or file.trashed:
        return BodySource.NONE, None

    if file.is_native:
        export_mime = NATIVE_EXPORT_MIME.get(file.mimeType)
        # Size on a native file is a placeholder, so it is not checked here.
        return (
            (BodySource.EXPORT, export_mime) if export_mime else (BodySource.NONE, None)
        )

    if file.size is not None and file.size > MAX_BODY_BYTES:
        return BodySource.NONE, None

    mime = file.mimeType.split(";")[0].strip()
    if mime in TEXT_BLOB_MIMES or mime.startswith(TEXT_BLOB_PREFIXES):
        return BodySource.DOWNLOAD, None

    return BodySource.NONE, None


def body_changed(file: DriveFile, previous_body_version: str | None) -> bool:
    """Whether the bytes differ from the last body we read.

    False means a rename, a move, or a permission edit — the metadata changed and
    must be republished, but re-reading the text would cost 200 units to return
    what we already have.
    """
    if previous_body_version is None:
        return True
    return file.body_version != previous_body_version


def clamp(text: str) -> str:
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[: MAX_BODY_CHARS - 1] + "…"
