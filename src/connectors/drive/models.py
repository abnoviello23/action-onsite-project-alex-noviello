"""Drive v3 API records.

The distilled subset the pipeline reads, not the full resource. Fields the API
omits unless asked for are optional here, because a narrower `fields` mask is
the difference between a cheap list call and an expensive one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

FOLDER_MIME = "application/vnd.google-apps.folder"
# A native Google Doc. Uploading text/plain against this makes Drive convert
# the upload, which is how both the seeder and `drive.create_file` produce real
# editor files rather than attachments.
DOC_MIME = "application/vnd.google-apps.document"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
# Everything under this prefix is a Drive-native editor file. It has no bytes to
# download and must be exported instead.
NATIVE_PREFIX = "application/vnd.google-apps."


def drive_entity_id(file_id: str) -> str:
    """Drives, folders and files share one id space in Drive, so they do here."""
    return f"drive:{file_id}"


class DriveUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    displayName: str | None = None
    emailAddress: str | None = None


class PermissionDetail(BaseModel):
    """Present only on shared-drive items, and only when requested.

    `inherited` is the field the whole ACL mirror turns on: on a shared drive
    every descendant reports the root's grants as inherited copies, so writing
    them per file would duplicate one grant across the entire tree.
    """

    model_config = ConfigDict(frozen=True)

    permissionType: str | None = None
    role: str | None = None
    inherited: bool = False
    inheritedFrom: str | None = None


class Permission(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    # user | group | domain | anyone
    type: str
    # owner | organizer | fileOrganizer | writer | commenter | reader
    role: str
    emailAddress: str | None = None
    domain: str | None = None
    deleted: bool = False
    permissionDetails: list[PermissionDetail] = Field(default_factory=list)

    @property
    def is_inherited(self) -> bool:
        """True when this grant comes from an ancestor rather than this item.

        Absent details means a My Drive item, where Drive does not report
        inheritance at all; treating that as direct is the fail-safe reading —
        it writes a redundant grant rather than dropping a real one.
        """
        return any(d.inherited for d in self.permissionDetails)


class DriveFile(BaseModel):
    """A file or folder. Drive models folders as files with a special mimeType."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    mimeType: str = ""
    # Drive v3 allows multiple parents historically; shared drives permit exactly
    # one. parents[0] is the containment edge, and access inherits along it.
    parents: list[str] = Field(default_factory=list)
    driveId: str | None = None

    createdTime: datetime | None = None
    modifiedTime: datetime | None = None

    # Monotonic, but bumps on metadata-only changes too — a rename or a move
    # advances it without the content differing.
    version: int = 0
    # Advances only when bytes change, which makes it the correct guard for
    # skipping an expensive re-download. Native editor files do not expose it.
    headRevisionId: str | None = None
    md5Checksum: str | None = None
    size: int | None = None

    trashed: bool = False
    explicitlyTrashed: bool = False
    webViewLink: str | None = None
    lastModifyingUser: DriveUser | None = None

    # Shared drives only: true when the item carries grants of its own on top of
    # the ones it inherits. False means a permissions call would return nothing
    # but inherited copies, so the connector can skip it entirely.
    hasAugmentedPermissions: bool = False

    @field_validator("size", mode="before")
    @classmethod
    def _size_to_int(cls, value: object) -> object:
        # The API returns size as a decimal string; native files report a
        # placeholder that has nothing to do with content length.
        if isinstance(value, str):
            return int(value) if value.isdigit() else None
        return value

    @property
    def entity_id(self) -> str:
        return drive_entity_id(self.id)

    @property
    def parent_entity_id(self) -> str | None:
        return drive_entity_id(self.parent_id) if self.parent_id else None

    @property
    def is_folder(self) -> bool:
        return self.mimeType == FOLDER_MIME

    @property
    def is_shortcut(self) -> bool:
        return self.mimeType == SHORTCUT_MIME

    @property
    def is_native(self) -> bool:
        """A Docs/Sheets/Slides-style file, including folders and shortcuts."""
        return self.mimeType.startswith(NATIVE_PREFIX)

    @property
    def parent_id(self) -> str | None:
        return self.parents[0] if self.parents else None

    @property
    def content_version(self) -> str:
        """The guarded upsert's ordering key.

        `version` and not `headRevisionId`: a rename or a move leaves the head
        revision untouched, so using it here would make the upsert discard every
        metadata-only change as stale.

        Zero-padded because the guard compares strings, and "10" sorts below "9".
        """
        return f"{self.version:020d}"

    @property
    def body_version(self) -> str:
        """Changes only when the bytes do — the guard for re-reading a body.

        Native editor files expose no head revision and fall back to `version`,
        so they re-export on metadata changes too. That wastes a call rather than
        serving stale text, which is the safe direction to be wrong in.
        """
        return self.headRevisionId or str(self.version)


class SharedDrive(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    createdTime: datetime | None = None

    @property
    def entity_id(self) -> str:
        return drive_entity_id(self.id)


class Change(BaseModel):
    """One entry from changes.list.

    Not every entry is about a file: `changeType == "drive"` describes the shared
    drive itself and carries no fileId, so consumers must not assume one.
    """

    model_config = ConfigDict(frozen=True)

    changeType: str = "file"
    time: datetime | None = None
    removed: bool = False
    fileId: str | None = None
    driveId: str | None = None
    file: DriveFile | None = None

    @property
    def entity_id(self) -> str | None:
        if self.file is not None:
            return self.file.entity_id
        if self.fileId:
            return drive_entity_id(self.fileId)
        if self.driveId:
            return drive_entity_id(self.driveId)
        return None

    @property
    def is_drive_change(self) -> bool:
        return self.changeType == "drive" or self.fileId is None

    @property
    def is_gone(self) -> bool:
        """Removed from view, or trashed.

        These arrive as different signals and mean different things — `removed`
        is a hard delete or a permission revocation, `trashed` is the bin — but
        both must stop the content being retrievable, so the graph treats them
        alike.
        """
        return self.removed or bool(self.file and self.file.trashed)
