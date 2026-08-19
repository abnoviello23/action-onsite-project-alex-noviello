"""Drive fixtures.

Writes native Google Docs into the configured shared drive rather than inserting
rows. The poller's changes.list feed then picks them up through the identical
path production content takes.

The corpus is Harborline: a Series B GTM company. Every file is a Doc (export
path) with a body that names the same people, deals, meetings, projects, and
tasks Slack and Notion seed. Sheets, blobs, and other types are out of scope
for this corpus.

Seeding is idempotent: every create looks for an existing child of the same
name first. `--reset` trashes the Harborline seed root (and the previous Acme
root, if it is still sitting there).
"""

from __future__ import annotations

import asyncio
import logging

from connectors.drive.models import DOC_MIME
from connectors.drive.writer import DriveWriter
from seeder.company import SEED_ROOT
from seeder.cross import bodies_by_title, drive_doc_url, notion_appendix
from seeder.documents import all_docs

log = logging.getLogger("seeder.drive")

OLD_SEED_ROOT = "Seed - Acme Corp"
FILE_CONCURRENCY = 4


async def _ensure_folder(
    writer: DriveWriter, cache: dict[tuple[str, ...], str], path: tuple[str, ...]
) -> str:
    if path in cache:
        return cache[path]
    parent_path = path[:-1]
    parent_id = await _ensure_folder(writer, cache, parent_path)
    folder_id = await writer.create_folder(path[-1], parent_id)
    cache[path] = folder_id
    return folder_id


async def seed(
    writer: DriveWriter, drive_id: str, *, reset: bool = False
) -> dict[str, str]:
    """Create the fixture tree. Returns title -> file id (caller builds URLs)."""
    if reset:
        for name in (SEED_ROOT, OLD_SEED_ROOT):
            existing = await writer.find_child(drive_id, name)
            if existing:
                await writer.trash(existing)
                log.info("trashed previous %r", name)

    docs = all_docs()
    root = await writer.create_folder(SEED_ROOT, drive_id)
    folders: dict[tuple[str, ...], str] = {(): root}

    for path, _, _ in docs:
        await _ensure_folder(writer, folders, path)

    sem = asyncio.Semaphore(FILE_CONCURRENCY)
    ids: dict[str, str] = {}

    async def _one(path: tuple[str, ...], title: str, body: str) -> None:
        async with sem:
            created = await writer.create_file(
                title,
                folders[path],
                body,
                upload_mime="text/plain",
                target_mime=DOC_MIME,
            )
            ids[title] = created["id"]

    await asyncio.gather(*(_one(path, title, body) for path, title, body in docs))
    log.info(
        "drive %s: %d docs in %d folders under %r",
        drive_id,
        len(docs),
        len(folders),
        SEED_ROOT,
    )
    return ids


def urls_from_ids(ids: dict[str, str]) -> dict[str, str]:
    return {title: drive_doc_url(file_id) for title, file_id in ids.items() if file_id}


async def attach_notion_links(
    writer: DriveWriter,
    ids_by_title: dict[str, str],
    notion_urls: dict[str, str],
) -> int:
    """Write each matching Notion URL into the Drive doc of the same title."""
    bodies = bodies_by_title()
    paired = [
        (title, ids_by_title[title], notion_urls[title])
        for title in ids_by_title
        if title in notion_urls
    ]
    if not paired:
        return 0

    sem = asyncio.Semaphore(FILE_CONCURRENCY)

    async def _one(title: str, file_id: str, notion_url: str) -> None:
        async with sem:
            body = bodies[title]
            marker = notion_appendix(notion_url)
            content = body if marker.strip() in body else f"{body.rstrip()}{marker}"
            await writer.update_content(file_id, content, upload_mime="text/plain")

    await asyncio.gather(*(_one(title, file_id, url) for title, file_id, url in paired))
    log.info("drive: linked %d docs to their Notion counterparts", len(paired))
    return len(paired)
