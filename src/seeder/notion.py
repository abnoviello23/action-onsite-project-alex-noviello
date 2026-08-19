"""Notion fixtures.

Writes real pages into the workspace rather than inserting rows, so the poller
ingests them through the identical path production content takes.

The tree mirrors Drive: Company, People, GTM Deals, Meetings, Projects, Tasks,
each page a document that names the same Harborline entities. Under Registers,
five databases hold the structured rows (people, deals, meetings, projects,
tasks) so a query can join the way a human would.

Everything hangs under a root page a human created and connected: an internal
integration cannot create workspace-level pages, so the root is an input to the
seeder rather than something it makes.

Seeding is idempotent: pages are looked up by title, database rows are skipped
when the data source is already populated. `--reset` trashes the Harborline
seed page (and the previous Acme page, if present).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from connectors.notion.writer import NotionWriter, bookmark, paragraph
from seeder.company import (
    DEALS,
    MEETINGS,
    PEOPLE,
    PROJECTS,
    SEED_ROOT,
    TASKS,
    Deal,
    Meeting,
    Person,
    Project,
    Task,
)
from seeder.cross import notion_page_url
from seeder.documents import all_docs

log = logging.getLogger("seeder.notion")

OLD_SEED_TITLE = "Seed - Acme Corp"


def _title(value: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": value[:2000]}}]}


def _text(value: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}


def _select(value: str) -> dict:
    return {"select": {"name": value}}


def _date(value: str) -> dict:
    return {"date": {"start": value}}


def _number(value: int) -> dict:
    return {"number": value}


def _email(value: str) -> dict:
    return {"email": value}


def _options(values: Iterable[str]) -> dict:
    return {"options": [{"name": name} for name in sorted(set(values))]}


async def _folder(writer: NotionWriter, parent_id: str, title: str) -> str:
    created = await writer.page(
        parent_id,
        title,
        body="Harborline fixture folder. Child pages are the documents.",
    )
    return created["id"]


async def _seed_pages(writer: NotionWriter, seed_root: str) -> dict[str, str]:
    folders: dict[tuple[str, ...], str] = {(): seed_root}
    docs = all_docs()
    ids: dict[str, str] = {}
    for path, title, body in docs:
        parent_path: tuple[str, ...] = ()
        parent_id = seed_root
        for part in path:
            parent_path = parent_path + (part,)
            if parent_path not in folders:
                folders[parent_path] = await _folder(writer, parent_id, part)
            parent_id = folders[parent_path]
        ids[title] = (await writer.page(parent_id, title, body=body))["id"]
    log.info("notion: %d document pages under %d folders", len(docs), len(folders) - 1)
    return ids


async def _seed_registers(writer: NotionWriter, seed_root: str) -> None:
    registers = await _folder(writer, seed_root, "Registers")

    people_db, people_ds = await writer.database(
        registers,
        "People",
        {
            "Name": {"title": {}},
            "Role": {"rich_text": {}},
            "Team": {"select": _options(p.team for p in PEOPLE)},
            "Email": {"email": {}},
            "Location": {"select": _options(p.location for p in PEOPLE)},
            "Manager": {"rich_text": {}},
            "Focus": {"rich_text": {}},
        },
    )
    deals_db, deals_ds = await writer.database(
        registers,
        "GTM Deals",
        {
            "Name": {"title": {}},
            "Stage": {"select": _options(d.stage for d in DEALS)},
            "ARR": {"number": {}},
            "Owner": {"rich_text": {}},
            "SE": {"rich_text": {}},
            "CSM": {"rich_text": {}},
            "Close": {"date": {}},
            "Industry": {"select": _options(d.industry for d in DEALS)},
            "Champion": {"rich_text": {}},
            "Next step": {"rich_text": {}},
        },
    )
    meetings_db, meetings_ds = await writer.database(
        registers,
        "Meetings",
        {
            "Name": {"title": {}},
            "Date": {"date": {}},
            "Type": {"select": _options(m.kind for m in MEETINGS)},
            "Attendees": {"rich_text": {}},
            "Related deal": {"rich_text": {}},
            "Related project": {"rich_text": {}},
            "Summary": {"rich_text": {}},
        },
    )
    projects_db, projects_ds = await writer.database(
        registers,
        "Projects",
        {
            "Name": {"title": {}},
            "Status": {
                "select": _options(
                    list({p.status for p in PROJECTS} | {"Not started", "In progress", "Done"})
                )
            },
            "Owner": {"rich_text": {}},
            "Due": {"date": {}},
            "Area": {"select": _options(p.area for p in PROJECTS)},
            "Members": {"rich_text": {}},
            "Success": {"rich_text": {}},
        },
    )
    tasks_db, tasks_ds = await writer.database(
        registers,
        "Tasks",
        {
            "Name": {"title": {}},
            "Status": {
                "select": _options(
                    list({t.status for t in TASKS} | {"Not started", "In progress", "Done"})
                )
            },
            "Assignee": {"rich_text": {}},
            "Project": {"rich_text": {}},
            "Due": {"date": {}},
            "Priority": {"select": _options(t.priority for t in TASKS)},
            "Notes": {"rich_text": {}},
        },
    )
    _ = (people_db, deals_db, meetings_db, projects_db, tasks_db)

    await _rows_if_empty(writer, people_ds, "people", [_person_row(p) for p in PEOPLE])
    await _rows_if_empty(writer, deals_ds, "deals", [_deal_row(d) for d in DEALS])
    await _rows_if_empty(
        writer, meetings_ds, "meetings", [_meeting_row(m) for m in MEETINGS]
    )
    await _rows_if_empty(
        writer, projects_ds, "projects", [_project_row(p) for p in PROJECTS]
    )
    await _rows_if_empty(writer, tasks_ds, "tasks", [_task_row(t) for t in TASKS])


async def _rows_if_empty(
    writer: NotionWriter, data_source_id: str, label: str, rows: list[dict]
) -> None:
    existing = await writer.rows(data_source_id)
    if existing:
        log.info("notion %s: %d rows exist", label, len(existing))
        return
    for props in rows:
        await writer.row(data_source_id, props)
    log.info("notion %s: %d rows created", label, len(rows))


def _person_row(person: Person) -> dict:
    return {
        "Name": _title(person.name),
        "Role": _text(person.role),
        "Team": _select(person.team),
        "Email": _email(person.email),
        "Location": _select(person.location),
        "Manager": _text(person.manager or "Board"),
        "Focus": _text(person.focus),
    }


def _deal_row(deal: Deal) -> dict:
    return {
        "Name": _title(deal.account),
        "Stage": _select(deal.stage),
        "ARR": _number(deal.arr),
        "Owner": _text(deal.owner),
        "SE": _text(deal.se or "—"),
        "CSM": _text(deal.csm or "—"),
        "Close": _date(deal.close_date),
        "Industry": _select(deal.industry),
        "Champion": _text(deal.champion),
        "Next step": _text(deal.next_step),
    }


def _meeting_row(meeting: Meeting) -> dict:
    return {
        "Name": _title(f"{meeting.date} — {meeting.title}"),
        "Date": _date(meeting.date),
        "Type": _select(meeting.kind),
        "Attendees": _text(", ".join(meeting.attendees)),
        "Related deal": _text(meeting.related_deal or "—"),
        "Related project": _text(meeting.related_project or "—"),
        "Summary": _text(meeting.summary),
    }


def _project_row(project: Project) -> dict:
    return {
        "Name": _title(project.name),
        "Status": _select(project.status),
        "Owner": _text(project.owner),
        "Due": _date(project.due),
        "Area": _select(project.area),
        "Members": _text(", ".join(project.members)),
        "Success": _text(project.success),
    }


def _task_row(task: Task) -> dict:
    return {
        "Name": _title(task.title),
        "Status": _select(task.status),
        "Assignee": _text(task.assignee),
        "Project": _text(task.project),
        "Due": _date(task.due),
        "Priority": _select(task.priority),
        "Notes": _text(task.notes),
    }


async def attach_bookmarks(
    writer: NotionWriter, page_id: str, urls: list[str | None]
) -> None:
    """Idempotent bookmarks so a Drive/Slack URL becomes a `mentions` edge."""
    wanted = [url for url in urls if url]
    if not wanted:
        return
    existing = await writer.bookmark_urls(page_id)
    missing = [url for url in wanted if url not in existing]
    if not missing:
        return
    blocks = []
    if not existing:
        blocks.append(paragraph("Related material in other systems:"))
    blocks.extend(bookmark(url) for url in missing)
    await writer.append(page_id, blocks)


async def attach_drive_links(
    writer: NotionWriter,
    ids_by_title: dict[str, str],
    drive_urls: dict[str, str],
) -> int:
    """Bookmark the matching Drive doc on each Notion page of the same title."""
    paired = [
        (title, ids_by_title[title], drive_urls[title])
        for title in ids_by_title
        if title in drive_urls
    ]
    for title, page_id, url in paired:
        await attach_bookmarks(writer, page_id, [url])
        log.debug("notion: bookmarked Drive on %r", title)
    log.info("notion: linked %d pages to their Drive counterparts", len(paired))
    return len(paired)


def urls_from_ids(ids: dict[str, str]) -> dict[str, str]:
    return {title: notion_page_url(page_id) for title, page_id in ids.items() if page_id}


async def seed(
    writer: NotionWriter,
    root_page_id: str,
    *,
    reset: bool = False,
) -> dict[str, str]:
    """Create the fixture tree under `root_page_id`. Returns title -> page id."""
    if reset:
        for title in (SEED_ROOT, OLD_SEED_TITLE):
            existing = await writer.find_child_page(root_page_id, title)
            if existing:
                await writer.trash(existing)
                log.info("trashed previous %r", title)

    seed_root_page = await writer.page(
        root_page_id,
        SEED_ROOT,
        body=(
            "Harborline fixture corpus: people, GTM deals, meetings, projects, "
            "and tasks. Document pages mirror Drive. Structured rows live under "
            "Registers."
        ),
    )
    seed_root = seed_root_page["id"]
    ids = await _seed_pages(writer, seed_root)
    await _seed_registers(writer, seed_root)
    log.info("notion: seeded %r", SEED_ROOT)
    ids[SEED_ROOT] = seed_root
    return ids
