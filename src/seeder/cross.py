"""Cross-source fixture links.

Drive docs and Notion pages share titles (both render `all_docs()`). After both
trees exist, each side gets the other's URL so ingest mints `mentions` edges.
Slack channels then cite the docs that belong to them.
"""

from __future__ import annotations

from seeder.company import DEALS, MEETINGS, PEOPLE, PROJECTS
from seeder.documents import all_docs

TEAM_CHANNEL: dict[str, str] = {
    "Leadership": "exec",
    "Go-to-Market": "gtm",
    "Sales": "sales",
    "Marketing": "marketing",
    "Customer Success": "cs",
    "Product": "product",
    "Engineering": "engineering",
}

PROJECT_CHANNEL: dict[str, str] = {
    "Q3 Enterprise Playbook": "enablement",
    "Salesforce two-way sync": "product",
    "Series B board pack": "board",
    "Mid-market pricing refresh": "forecast",
    "Partner program launch": "partners",
    "Onboarding 2.0": "cs",
    "SOC 2 Type II": "legal",
    "Website relaunch": "marketing",
    "Deal room templates": "product",
    "Forecast accuracy initiative": "forecast",
}

DEAL_CHANNEL: dict[str, str] = {
    "Meridian Health": "meridian",
    "Cobalt Financial": "cobalt",
}


def drive_doc_url(file_id: str) -> str:
    return f"https://docs.google.com/document/d/{file_id}/edit"


def notion_page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def notion_appendix(url: str) -> str:
    return f"\n\nAlso in Notion: {url}\n"


def bodies_by_title() -> dict[str, str]:
    return {title: body for _, title, body in all_docs()}


def titles_for_channel(slug: str) -> list[str]:
    """Document titles this Slack channel should cite."""
    titles: list[str] = []
    folder_titles: dict[str, list[str]] = {}
    for path, title, _ in all_docs():
        folder_titles.setdefault(path[0] if path else "", []).append(title)

    if slug == "general":
        titles.extend(folder_titles.get("Company", []))
    elif slug == "allhands":
        titles.append("All-hands August follow-up")
        titles.append("Q3 meeting calendar")
    elif slug == "exec":
        titles.extend(
            [
                "About Harborline",
                "Org chart and reporting lines",
                "Series B narrative and use of proceeds",
                "Team overview: Leadership",
            ]
        )
    elif slug == "gtm":
        titles.extend(
            [
                "GTM motion, ICP, and personas",
                "Pipeline snapshot 2026-08-17",
                "MEDDPICC as Harborline runs it",
                "Team overview: Go-to-Market",
            ]
        )
    elif slug == "sales":
        titles.extend(
            [
                "Enterprise AE runbook",
                "Mid-market AE runbook",
                "Win/loss log",
                "Team overview: Sales",
            ]
        )
    elif slug == "deals":
        titles.extend(
            f"Deal — {d.account}" for d in DEALS if d.account not in DEAL_CHANNEL
        )
    elif slug == "enablement":
        titles.extend(
            [
                "Project — Q3 Enterprise Playbook",
                "MEDDPICC as Harborline runs it",
                "Enterprise AE runbook",
            ]
        )
    elif slug == "wins":
        titles.extend(["Win/loss log", "Deal — Brightpath Education"])
    elif slug == "forecast":
        titles.extend(
            [
                "Pipeline snapshot 2026-08-17",
                "Forecast stage-exit criteria",
                "Project — Forecast accuracy initiative",
            ]
        )
    elif slug == "partners":
        titles.extend(
            ["Partner program first wave", "Project — Partner program launch"]
        )
    elif slug == "marketing":
        titles.extend(
            [
                "Team overview: Marketing",
                "Project — Website relaunch",
                "Competitive landscape",
            ]
        )
    elif slug == "cs":
        titles.extend(
            [
                "Team overview: Customer Success",
                "Enterprise CSM brief",
                "Onboarding 2.0 path",
                "Project — Onboarding 2.0",
            ]
        )
    elif slug == "product":
        titles.extend(
            [
                "Team overview: Product",
                "Project — Salesforce two-way sync",
                "Project — Deal room templates",
            ]
        )
    elif slug == "engineering":
        titles.extend(
            [
                "Team overview: Engineering",
                "Project — SOC 2 Type II",
                "Q3 risk register",
            ]
        )
    elif slug == "legal":
        titles.extend(
            [
                "Enterprise security pack guide",
                "Project — SOC 2 Type II",
                "Customer data room index",
            ]
        )
    elif slug == "board":
        titles.extend(
            [
                "Series B narrative and use of proceeds",
                "Project — Series B board pack",
                "Q3 company OKRs",
            ]
        )
    elif slug == "meridian":
        titles.append("Deal — Meridian Health")
        titles.extend(
            f"{m.date} — {m.title}"
            for m in MEETINGS
            if m.related_deal == "Meridian Health"
        )
    elif slug == "cobalt":
        titles.append("Deal — Cobalt Financial")
        titles.extend(
            f"{m.date} — {m.title}"
            for m in MEETINGS
            if m.related_deal == "Cobalt Financial"
        )

    for person in PEOPLE:
        if TEAM_CHANNEL.get(person.team) == slug:
            titles.append(person.name)
    for project in PROJECTS:
        if PROJECT_CHANNEL.get(project.name) == slug:
            title = f"Project — {project.name}"
            if title not in titles:
                titles.append(title)
    for deal in DEALS:
        if DEAL_CHANNEL.get(deal.account) == slug:
            title = f"Deal — {deal.account}"
            if title not in titles:
                titles.insert(0, title)

    seen: set[str] = set()
    out: list[str] = []
    for title in titles:
        if title not in seen:
            seen.add(title)
            out.append(title)
    return out
