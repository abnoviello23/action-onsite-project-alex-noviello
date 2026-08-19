"""Markdown bodies for every Harborline fixture document.

Drive uploads these as native Docs; Notion renders them as pages. Each body
names real people, deals, meetings, projects, and tasks so a retrieval system
has something to join across sources.
"""

from __future__ import annotations

from seeder.company import (
    ARR,
    COMPANY,
    CUSTOMERS,
    DEALS,
    DOMAIN,
    FOUNDED,
    HEADCOUNT,
    HQ,
    LEGAL_NAME,
    MEETINGS,
    PEOPLE,
    PEOPLE_BY_NAME,
    PRODUCT,
    PROJECTS,
    STAGE,
    TASKS,
    Deal,
    Meeting,
    Person,
    Project,
    Task,
    deals_for,
    meetings_for,
    meetings_for_deal,
    meetings_for_project,
    money,
    projects_for,
    reports_of,
    tasks_for,
    tasks_in,
)

# (folder path under the seed root, document title, markdown body)
Doc = tuple[tuple[str, ...], str, str]


def _bullets(items: object) -> str:
    return "\n".join(f"- {item}" for item in list(items))


def _names(people: tuple[Person, ...] | list[Person]) -> str:
    if not people:
        return "none listed"
    return ", ".join(p.name for p in people)


def _join(names: object) -> str:
    items = list(names)
    if not items:
        return "none listed"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# ---------------------------------------------------------------- company --


def _about() -> str:
    leaders = [p for p in PEOPLE if p.team == "Leadership"]
    return f"""# About {COMPANY}

{LEGAL_NAME} is a {STAGE} company headquartered in {HQ}. Founded in {FOUNDED} by Maya Chen, {COMPANY} sells {PRODUCT}, a GTM operating system for mid-market and enterprise revenue teams. The product is the system of record for pipeline, forecast, deal rooms, and enablement. Salesforce stays the CRM of record; Gong and spreadsheets stop being where the company actually runs.

The current snapshot, which Noah Berg will put in the October Series B board pack:

- ARR: {ARR} across {CUSTOMERS} customers
- Headcount: {HEADCOUNT}, with open reqs for three AEs, one Staff Engineer, and a Head of Data
- Stage: {STAGE}, $42M raised March 2026, led by Northpeak Capital
- Domain: {DOMAIN}

## What we sell

{PRODUCT} replaces the patchwork every VP Sales already hates: Salesforce reports no one trusts, a forecast spreadsheet with a version number in the filename, and deal rooms that live in Google Drive. Riley Park's product org is split along that line. Drew Patel owns Core Platform (deal rooms, forecast, pipeline). Sasha Klein owns Integrations, and the Salesforce two-way sync she is shipping is a close condition on Cobalt Financial.

## Who we sell to

ICP is VP Sales and RevOps at 200–2,000 person B2B companies. Enterprise AEs (Marcus Webb) pair with Harper Lin as solutions consultant. Mid-market AEs (Lena Ortiz, Theo Grant) run a tighter motion and quote the new band Quinn Murphy published after the 2026-08-08 pricing committee. Jade Brooks is writing the Q3 Enterprise Playbook so those two motions stop improvising.

## Who runs it

{_bullets(f"{p.name}, {p.role} ({p.location})" for p in leaders)}

Jordan Hale's GTM org sits under the CRO: Priya Raman (Sales), Alex Okonkwo (Marketing), Sam Torres (Customer Success), Cam Diaz (Partnerships). Dana Kim owns engineering and SOC 2 Type II. Elena Voss owns hiring against the Series B plan.

## What is true this quarter

Q3 commit is $4.1M. The two deals that make or miss it are Meridian Health ($420k, Negotiation, Marcus Webb) and Cobalt Financial ($540k, Legal review, Marcus Webb). Brightpath Education is already closed-won and is the customer narrative Maya Chen wants in the board pack, contingent on Paul Singh's 2026-09-04 QBR going well. Enterprise pipeline coverage is 1.6x, which is the number Jordan Hale would rather put on a slide himself than have a board member find.
"""


def _org_chart() -> str:
    lines = ["# Org chart and reporting lines", ""]
    lines.append(
        f"{COMPANY}'s live reporting lines as of August 2026. Elena Voss maintains "
        "the people system of record; this document is the narrative version AEs "
        "and CSMs actually read."
    )
    lines.append("")
    for leader in PEOPLE:
        if leader.manager is not None:
            continue
        lines.append(f"## {leader.name}, {leader.role}")
        lines.append("")
        lines.append(leader.bio)
        lines.append("")
        directs = reports_of(leader.name)
        if directs:
            lines.append("Direct reports:")
            lines.append(_bullets(f"{p.name}, {p.role} ({p.location})" for p in directs))
            lines.append("")
            for child in directs:
                grandchildren = reports_of(child.name)
                if not grandchildren:
                    continue
                lines.append(f"### Under {child.name}")
                lines.append("")
                lines.append(_bullets(f"{p.name}, {p.role} ({p.location})" for p in grandchildren))
                lines.append("")
                for gc in grandchildren:
                    great = reports_of(gc.name)
                    if not great:
                        continue
                    lines.append(f"Under {gc.name}: " + _join([p.name for p in great]) + ".")
                    lines.append("")
    lines.append("## Open reqs Elena Voss is hiring")
    lines.append("")
    lines.append(
        "- Three Account Executives, reporting to Priya Raman. The book is too "
        "concentrated on Marcus Webb (Meridian Health, Cobalt Financial, Atlas "
        "Retail Group, Redwood Clinics)."
    )
    lines.append(
        "- One Staff Engineer, reporting to Tess Nakamura. Luis Ortega is on "
        "both Salesforce two-way sync and SOC 2 Type II; the hire is how those "
        "projects stop colliding."
    )
    lines.append(
        "- Head of Data, reporting to Dana Kim. Noah Berg will slip this req "
        "before he slips SOC 2 if both Meridian Health and Cobalt Financial "
        "move to Q4."
    )
    return "\n".join(lines)


def _gtm_motion() -> str:
    return f"""# GTM motion, ICP, and personas

{COMPANY} runs two motions under Jordan Hale. They share a product ({PRODUCT}) and a RevOps owner (Quinn Murphy). They do not share a sales process, a price band, or an SE.

## Ideal customer profile

- Company: 200–2,000 employees, B2B, already has a CRM (almost always Salesforce)
- Buyer: VP Sales or Head of RevOps. Economic buyer is the CRO or CFO on enterprise, the VP Sales on mid-market
- Pain: forecast the board does not trust, deal rooms in Drive, enablement that is a slide deck
- Not ICP: firms with no CRM and no forecast cadence. Summit Legal is the live example Jade Brooks wants disqualified rather than nurtured

Nina Shah's website relaunch will say this on an ICP page, without list prices, because Quinn Murphy's mid-market pricing refresh is still open.

## Enterprise motion

Owner: Priya Raman. AE: Marcus Webb. SE: Harper Lin. CSM: Ivy Chen, named on paper once a deal is past Proposal.

Any deal above $200k runs MEDDPICC as Harborline actually runs it, which is stricter than the textbook. Metrics and economic buyer are required to stay in commit. Maya Chen is an exec sponsor only when the AE has earned it — Meridian Health has, Atlas Retail Group has not. The Q3 Enterprise Playbook Jade Brooks is writing uses Meridian as the success case and Pinecone Analytics as the failure case.

Live enterprise book:

- Meridian Health, $420k, Negotiation, close 2026-09-12
- Cobalt Financial, $540k, Legal review, close 2026-09-30
- Redwood Clinics, $275k, Technical validation, close 2026-10-08
- Atlas Retail Group, $310k, Discovery, close 2026-10-31 (Q4, not commit)

## Mid-market motion

AEs: Lena Ortiz (Austin) and Theo Grant (Chicago). SE is Harper Lin only when a deal earns it (Northwind Logistics, Vellum Media, Helios Manufacturing, Oakridge Industrial). CSM is Paul Singh.

Price band is the one the 2026-08-08 pricing committee approved. Brightpath Education ($72k, Closed won, 2026-07-22) is grandfathered on the old band. Northwind Logistics must quote the new one. Noah Berg will not approve another one-off SKU while the refresh is open.

## Outbound and inbound

Aisha Patel runs Ben Choi (enterprise outbound, East Coast) and Sofia Reyes (inbound mid-market). Ben sourced Atlas Retail Group. Sofia recycled Cascade Energy into Lena's book. Enterprise coverage is 1.6x, which is why Owen Frost's September partner webinars with Cam Diaz are a GTM project, not a marketing hobby.

## Forecast rules

Quinn Murphy's number is the commit, not the AE rollup. Jordan Hale restated this in the 2026-08-14 Q3 forecast review. Cascade Energy and Summit Legal are upside only. Priya Raman inspects Marcus, Lena, and Theo every Friday against stage-exit criteria. Last quarter's miss was 18%. The Forecast accuracy initiative exists so the October board does not see that twice.
"""


def _series_b() -> str:
    return f"""# Series B narrative and use of proceeds

{LEGAL_NAME} closed a $42M Series B in March 2026, led by Northpeak Capital. Maya Chen and Noah Berg own the October board pack. This document is the narrative the pack has to support, not the slides themselves. The slides are the Series B board pack project, due 2026-09-28.

## Why we raised

{PRODUCT} had a working mid-market motion and a product that AEs could demo. What it did not have was an enterprise close muscle, a Salesforce two-way sync, or a Type II report. The raise is supposed to buy those three things, in that order. It is not supposed to buy a fourth AE team before Meridian Health and Cobalt Financial prove the enterprise motion closes.

## Use of proceeds, as Noah Berg will tell the board

- GTM: three AEs under Priya Raman, plus enablement (Jade Brooks) and RevOps (Quinn Murphy) already in seat. Enterprise coverage at 1.6x is the gap Alex Okonkwo and Owen Frost are closing with pipeline, not with more AEs this quarter
- Product and engineering: Salesforce two-way sync (Sasha Klein, Luis Ortega), deal rooms GA (Drew Patel, Mei Huang), and the Staff Engineer Elena Voss is hiring so those two projects stop sharing Luis
- Trust: SOC 2 Type II, Dana Kim, evidence window open, customer-facing date 2026-10-15. Cobalt Financial, Meridian Health, and Redwood Clinics all need this in writing
- Working capital: Noah Berg will slip the Head of Data req before he slips SOC 2 if both large deals move to Q4

## What the board will be shown as proof

- Brightpath Education as the customer narrative, contingent on Paul Singh's 2026-09-04 QBR and Nina Shah's case study
- Q3 commit of $4.1M, with Meridian Health and Cobalt Financial called out as not-yet-booked
- Roadmap dates that match what Marcus Webb and Harper Lin are saying in rooms: deal rooms week of 2026-08-25, sync limited availability 2026-09-08
- A GTM slide that names the 1.6x coverage gap instead of hiding it

Maya Chen's line at the 2026-08-07 all-hands is the line on the website relaunch too: we are a GTM operating system, not a forecast tool. Nina Shah and Alex Okonkwo have to make the site catch up to that sentence by 2026-09-12.
"""


def _operating_cadence() -> str:
    standing = [
        m
        for m in MEETINGS
        if m.title.startswith(("Weekly", "Product x", "1:1"))
        or "weekly" in m.title.lower()
    ]
    return f"""# Operating cadence

How {COMPANY} actually runs, as opposed to the calendar invites that accumulated.

## Weekly

- Monday GTM standup, Jordan Hale. Priya Raman, Alex Okonkwo, Sam Torres, Quinn Murphy, Jade Brooks. Cam Diaz joins when partners are on the agenda. This is where deals are pulled out of commit. Cascade Energy and Summit Legal came out on 2026-08-11
- Friday forecast inspect, Priya Raman. Marcus Webb, Lena Ortiz, Theo Grant, Quinn Murphy. Jordan Hale treats Quinn's number as the commit
- Product x Sales weekly, Riley Park. Drew Patel, Sasha Klein, Priya Raman, Harper Lin. Nina Shah joins when messaging is on the agenda. This is where customer-facing dates get set — deal rooms on 2026-08-25 is now a Meridian Health date, not an internal one

## Recurring but not weekly

- Pricing committee, Noah Berg and Quinn Murphy, as needed. Last sat 2026-08-08 and put the new mid-market band in force
- Board prep, Maya Chen and Noah Berg, ramping toward 2026-10-02. First working session was 2026-08-13
- All-hands, first Friday of the month. August named Brightpath Education and SOC 2 as company facts

## Deal cadence, enterprise

Harper Lin owns the security pack and the deal room. Marcus Webb owns the paper. Ivy Chen is named on the form once the deal is past Proposal. Maya Chen attends an exec alignment only when Priya Raman says the deal has earned it. Meridian Health on 2026-08-21 earned it. Atlas Retail Group has not.

## Deal cadence, mid-market

Proposal includes a named CSM (Paul Singh) and the Onboarding 2.0 path. Annual billing. New price band. Lena Ortiz held that line on Northwind Logistics on 2026-08-19 when they asked for monthly.

## Projects have owners, not committees

{_bullets(f"{p.name}: {p.owner} ({p.status}, due {p.due})" for p in PROJECTS)}

Tasks live under those projects. They are not a second operating system. The Q3 task register is the list; Friday inspect is where they move.

## Cadence meetings already on the books

{_bullets(f"{m.date} — {m.title} ({m.kind})" for m in standing)}
"""


def _competitive() -> str:
    return f"""# Competitive landscape

Nina Shah owns this document. AEs are the audience. It is not a feature matrix. It is what to say in a room when a champion names the alternative they already have, which is almost always 'we will keep doing this in Salesforce and a spreadsheet'.

## The real competitor is the status quo

Pinecone Analytics did not lose to another vendor. They lost to doing nothing: the champion (Jon Park) left mid-evaluation, there was no compelling event, and the deal lingered in Lena Ortiz's book until it was closed-lost on 2026-08-01. Jade Brooks is putting that write-up in the Q3 Enterprise Playbook as the failure case. If a deal looks like Pinecone in week two, disqualify it. Do not nurture it.

Summit Legal is the live version of the same pattern. No Salesforce, no forecast cadence, a RevOps manager (Elena Brooks) who is not an economic buyer. Theo Grant is running MEDDPICC this week. If metrics and economic buyer are both still missing, it is a disqualify, not a Q4 maybe.

## When they say they will 'just do this in Salesforce'

That is Cobalt Financial's opening position and Atlas Retail Group's current one. The answer is not 'we replace Salesforce'. The answer is: Salesforce stays the CRM of record, {PRODUCT} becomes the system of record for pipeline, forecast, and deal rooms, and Sasha Klein's two-way sync is how those two facts coexist. Cobalt made limited availability of that sync a close condition. Atlas has four Salesforce orgs and we have not scoped that; Sasha owes Marcus Webb a one-pager by 2026-08-27, and it is Q4 work.

## When they already bought a conversation-intelligence tool

Meridian Health has Gong. We do not ask them to rip it out. Deal rooms and forecast are the wedge; conversation intelligence is a later integration and not a Q3 promise. Harper Lin has this line in the Meridian security pack so nobody in the room invents a Gong replacement on the spot.

## When a CPQ or enablement vendor is in the deal

Jordan Hale used to run sales at a CPQ vendor and will join a call if that is the live comparison. It almost never is. Enablement-only tools lose to us because Jade Brooks's playbook and Drew Patel's deal rooms are the same object, not a slide deck plus a folder. If a deal is actually an enablement-only deal, it is not ICP.

## Pricing fights

Mid-market: new band, annual, no one-off SKUs. Lena Ortiz already held this on Northwind Logistics. Enterprise: Noah Berg's cap is 22% off list. Meridian Health's CFO asked about a 15% milestone holdback tied to deal rooms; Maya Chen's answer on 2026-08-21 was no holdback if deal rooms GA the week of 2026-08-25. That date is now load-bearing.
"""


def _pricing() -> str:
    return f"""# Pricing and packaging

Quinn Murphy owns the numbers. Noah Berg owns the exceptions. Priya Raman's AEs do not invent SKUs.

## Mid-market band (in force as of 2026-08-08)

Approved in the pricing committee with Noah Berg, Quinn Murphy, Priya Raman, Lena Ortiz, and Theo Grant in the room.

- Platform: {PRODUCT} for up to 40 sellers, forecast + pipeline + deal rooms
- Onboarding: Onboarding 2.0, named CSM (Paul Singh), fourteen days to first forecast, sold as part of the order form not as a surprise
- Billing: annual. Monthly is a no, which Lena Ortiz already said to Northwind Logistics
- Brightpath Education ($72k, closed 2026-07-22) is grandfathered on the 2025 band and is not a precedent

Northwind Logistics ($96k, Proposal, close 2026-09-18) is the first live quote on the new band. Vellum Media ($180k, Mutual close plan) sits at the top of mid-market and is being run with enterprise hygiene (mutual close plan, named CSM, Harborline paper) without being moved into Marcus Webb's book.

## Enterprise

Priced from seller count, complexity, and whether Salesforce two-way sync, BAA, and a named enterprise CSM (Ivy Chen) are on the paper.

- Meridian Health: $420k, Negotiation. Healthcare, BAA, named CSM, deal rooms as a live date not a holdback
- Cobalt Financial: $540k, Legal review. Sync limited availability by 2026-09-08 is a close condition
- Redwood Clinics: $275k, Technical validation. Will copy Meridian's security pack
- Atlas Retail Group: $310k, Discovery. Four Salesforce orgs, not yet scoped, Q4

Discounting cap is 22% of list. Milestone holdbacks are a no unless Maya Chen and Noah Berg both say otherwise in the same meeting.

## Pilots

Noah Berg's rules, which Theo Grant followed on Oakridge Industrial ($125k, Pilot in progress):

- Paid
- Time-boxed
- Written success criteria (Oakridge: four consecutive weekly forecasts submitted from {PRODUCT}, not the spreadsheet)
- Named CSM (Paul Singh) before the pilot starts

Unpaid pilots are a no. Helios Manufacturing ($110k, Demo scheduled) is being pointed at a Q4 proposal, not a free pilot.

## Deal desk

Quinn Murphy is the desk. Exceptions go to Noah Berg. The mid-market pricing refresh project is still open, which is why Nina Shah's website relaunch will ship a pricing philosophy page and not a numbers page.
"""


def _okrs() -> str:
    return """# Q3 company OKRs

Maya Chen set these after the Series B close. They are the filter on whether a project, a hire, or a deal-desk exception is in Q3.

## 1. Close the enterprise motion

Commit $4.1M. Meridian Health and Cobalt Financial are the two deals that make the enterprise story true rather than anecdotal. Redwood Clinics is coverage. Atlas Retail Group is Q4.

Owners: Jordan Hale, Priya Raman, Marcus Webb, Harper Lin.

Related projects: Q3 Enterprise Playbook (Jade Brooks, 2026-08-29), Deal room templates (Harper Lin, 2026-08-25), Forecast accuracy initiative (Quinn Murphy, 2026-10-01).

## 2. Make the product match what we say in rooms

Deal rooms GA week of 2026-08-25 (Drew Patel, Mei Huang). Salesforce two-way sync limited availability 2026-09-08 (Sasha Klein, Luis Ortega). Those dates have been given to Meridian Health and Cobalt Financial. Riley Park's job is that the board pack, the website relaunch, and the sales rooms all say the same dates.

## 3. Become sellable to a CISO

SOC 2 Type II evidence window is open. Dana Kim's customer-facing date is 2026-10-15. Type I is already in the data room. Audit logging (Tess Nakamura, Mei Huang) and access reviews (Luis Ortega) are the blockers. Noah Berg has already said this project does not slip if a deal does.

## 4. Stop onboarding being an apology

Onboarding 2.0 (Ivy Chen, 2026-09-15) is how Brightpath Education's QBR does not become the template for Meridian Health. Named CSM on every proposal past that stage (Sam Torres). Paul Singh is already on Northwind, Vellum, Oakridge, Helios, Brightpath. Ivy Chen is on Meridian, Cobalt, Redwood.

## 5. Tell the same story outside the building

Website relaunch 2026-09-12 (Nina Shah, Alex Okonkwo). Partner program first wave (Cam Diaz, Northstar RevOps signing now, Fieldline Consulting waiting on AppExchange). Brightpath case study into the October board pack.

## What is explicitly not a Q3 OKR

Head of Data. A fourth AE team. Replacing Gong. Four-org Salesforce sync for Atlas Retail Group. A free Helios Manufacturing pilot.
"""


def _cadence_calendar() -> str:
    return f"""# Q3 meeting calendar

The live meetings already captured in this corpus, so a search for a deal or a project lands on the conversation that moved it.

{_bullets(f"{m.date} — {m.title}. {m.kind}. Attendees: {_join(m.attendees)}. Deal: {m.related_deal or '—'}. Project: {m.related_project or '—'}." for m in MEETINGS)}

Customer-facing meetings this fortnight that AEs should not reschedule without Priya Raman:

- 2026-08-20 Atlas Retail Group discovery (Marcus Webb, Harper Lin, Ben Choi). Happened; follow-up is a Sasha Klein one-pager, not a second workshop
- 2026-08-21 Meridian Health exec alignment (Maya Chen, Jordan Hale, Marcus Webb, Harper Lin, Ivy Chen)
- 2026-08-21 Cobalt Financial legal and architecture (Marcus Webb, Harper Lin, Luis Ortega, Sasha Klein, Dana Kim)
- 2026-08-22 Helios Manufacturing demo (Theo Grant, Harper Lin)
- 2026-08-24 Vellum Media mutual close planning (Lena Ortiz, Harper Lin, Paul Singh)
- 2026-08-27 Redwood Clinics technical validation (Marcus Webb, Harper Lin, Tess Nakamura, Mei Huang, Ivy Chen)
"""


# ---------------------------------------------------------------- people --


def _person_doc(person: Person) -> str:
    manager = PEOPLE_BY_NAME.get(person.manager) if person.manager else None
    reports = reports_of(person.name)
    deals = deals_for(person.name)
    projects = projects_for(person.name)
    tasks = tasks_for(person.name)
    meetings = meetings_for(person.name)
    lines = [
        f"# {person.name}",
        "",
        f"{person.role}, {person.team}. Based in {person.location}. {person.email}.",
        "",
        person.bio,
        "",
        f"**This quarter's focus.** {person.focus}",
        "",
    ]
    if manager:
        lines += [
            "## Reports to",
            "",
            f"{manager.name}, {manager.role} ({manager.location}). {manager.email}.",
            "",
        ]
    else:
        lines += ["## Reports to", "", "Board. Maya Chen is CEO.", ""]
    if reports:
        lines += [
            "## Direct reports",
            "",
            _bullets(f"{p.name}, {p.role} ({p.location}, {p.email})" for p in reports),
            "",
        ]
    if deals:
        lines += [
            "## Live deals this person is on",
            "",
            _bullets(
                f"{d.account} — {money(d.arr)} ARR, {d.stage}, close {d.close_date}, "
                f"owner {d.owner}, SE {d.se or 'none'}, CSM {d.csm or 'none'}. "
                f"Champion: {d.champion}. Next: {d.next_step}"
                for d in deals
            ),
            "",
        ]
    if projects:
        lines += [
            "## Projects",
            "",
            _bullets(
                f"{p.name} ({'owner' if p.owner == person.name else 'member'}), "
                f"{p.status}, due {p.due}, {p.area}. {p.summary}"
                for p in projects
            ),
            "",
        ]
    if tasks:
        lines += [
            "## Open work",
            "",
            _bullets(
                f"{t.title} [{t.status}, {t.priority}, due {t.due}] on {t.project}. {t.notes}"
                for t in tasks
            ),
            "",
        ]
    if meetings:
        lines += [
            "## Meetings on the book",
            "",
            _bullets(
                f"{m.date} — {m.title} ({m.kind}). With {_join(m.attendees)}."
                for m in meetings
            ),
            "",
        ]
    lines += [
        "## How to involve them",
        "",
        f"Do not add {person.name} to a deal thread unless they are already on "
        f"the account team or the project. The people who are stretched across "
        f"too many live objects this quarter are Harper Lin (every enterprise "
        f"SE motion), Luis Ortega (sync and SOC 2), and Marcus Webb (four "
        f"enterprise deals). Priya Raman's 2026-08-17 1:1 with Jordan Hale "
        f"already pulled Atlas Retail Group off Marcus's next two weeks.",
    ]
    return "\n".join(lines)


def _team_overview(team: str, owner: str, narrative: str) -> str:
    members = [p for p in PEOPLE if p.team == team]
    deals = []
    for p in members:
        for d in deals_for(p.name):
            if d not in deals:
                deals.append(d)
    projects = []
    for p in members:
        for proj in projects_for(p.name):
            if proj not in projects:
                projects.append(proj)
    return f"""# {team}

Owner: {owner}.

{narrative}

## People

{_bullets(f"{p.name}, {p.role} ({p.location}). Manager: {p.manager or 'board'}. {p.focus}" for p in members)}

## Deals this team is touching

{_bullets(f"{d.account} — {money(d.arr)}, {d.stage}, {d.owner}" for d in deals) or '- None directly.'}

## Projects this team owns or staffs

{_bullets(f"{p.name} — owner {p.owner}, {p.status}, due {p.due}" for p in projects) or '- None directly.'}
"""


# ----------------------------------------------------------------- deals --


def _deal_doc(deal: Deal) -> str:
    owner = PEOPLE_BY_NAME[deal.owner]
    se = PEOPLE_BY_NAME.get(deal.se) if deal.se else None
    csm = PEOPLE_BY_NAME.get(deal.csm) if deal.csm else None
    related_meetings = meetings_for_deal(deal.account)
    related_projects = [
        p
        for p in PROJECTS
        if deal.account.split()[0] in p.summary
        or deal.account in p.summary
        or (p.name == "Deal room templates" and deal.arr >= 200_000)
        or (p.name == "Salesforce two-way sync" and deal.account in ("Cobalt Financial", "Meridian Health", "Atlas Retail Group"))
        or (p.name == "SOC 2 Type II" and deal.industry in ("Healthcare", "Fintech"))
        or (p.name == "Onboarding 2.0" and deal.stage in ("Proposal", "Mutual close plan", "Closed won", "Pilot in progress", "Negotiation", "Legal review"))
        or (p.name == "Mid-market pricing refresh" and deal.account in ("Northwind Logistics", "Brightpath Education", "Vellum Media"))
        or (
            p.name == "Q3 Enterprise Playbook"
            and (deal.arr >= 200_000 or deal.account == "Pinecone Analytics")
        )
        or (p.name == "Forecast accuracy initiative" and deal.account in ("Cascade Energy", "Summit Legal", "Meridian Health", "Cobalt Financial"))
    ]
    # dedupe
    seen: set[str] = set()
    projects: list[Project] = []
    for p in related_projects:
        if p.name not in seen:
            seen.add(p.name)
            projects.append(p)
    return f"""# Deal: {deal.account}

{money(deal.arr)} ARR. Stage: {deal.stage}. Close: {deal.close_date}. Industry: {deal.industry}.

## Account team

- Owner: {owner.name}, {owner.role} ({owner.email}, {owner.location})
- Solutions: {f"{se.name}, {se.role}" if se else "not assigned — mid-market default unless Harper Lin is pulled in"}
- CSM: {f"{csm.name}, {csm.role}" if csm else "not named yet. Sam Torres's rule is a named CSM once the deal is past Proposal"}
- Champion: {deal.champion}

## Why we win

{deal.thesis}

## Risks

{deal.risks}

## Next step

{deal.next_step}

## Related projects

{_bullets(f"{p.name} (owner {p.owner}, {p.status}, due {p.due}). {p.summary}" for p in projects) or '- None tagged.'}

## Meetings that already happened or are on the book

{_bullets(f"{m.date} — {m.title}. {_join(m.attendees)}. {m.summary}" for m in related_meetings) or '- None captured yet.'}

## Inspection notes

Run this deal against the Q3 Enterprise Playbook if it is above $200k, and against Jade Brooks's mid-market MEDDPICC one-pager otherwise. Quinn Murphy will not let a champion-identified deal with no economic buyer sit in commit; that is why Cascade Energy and Summit Legal were pulled on 2026-08-11. {deal.account} is currently **{deal.stage}**.
"""


def _pipeline() -> str:
    commit_stages = {"Negotiation", "Legal review", "Proposal", "Mutual close plan", "Closed won", "Pilot in progress"}
    commit = [d for d in DEALS if d.stage in commit_stages and d.account not in ("Atlas Retail Group",)]
    upside = [d for d in DEALS if d not in commit]
    return f"""# Pipeline snapshot, 2026-08-17

Quinn Murphy's view. This is the board the Friday inspect uses. Jordan Hale treats this number as the commit, not the AE-rollup number in Salesforce, until Sasha Klein's two-way sync makes those the same object.

Q3 commit target: $4.1M. Snapshot below is live pipeline, not the whole number (existing book plus expansion lives in Noah Berg's ARR bridge).

## In or next to commit

{_bullets(f"{d.account} — {money(d.arr)}, {d.stage}, {d.owner}, close {d.close_date}. Champion {d.champion}." for d in commit)}

## Upside / Q4 / lost

{_bullets(f"{d.account} — {money(d.arr)}, {d.stage}, {d.owner}, close {d.close_date}." for d in upside)}

## Concentration

Marcus Webb is on Meridian Health, Cobalt Financial, Atlas Retail Group, and Redwood Clinics. Priya Raman and Jordan Hale already agreed, in their 2026-08-17 1:1, that Marcus's next two weeks are Meridian, Cobalt, and Redwood only. Atlas follow-ups wait. Elena Voss's three AE reqs exist because this concentration is a company risk, not a Marcus performance issue.

Harper Lin is the SE on every live enterprise deal and on Northwind, Vellum, Helios, and Oakridge. Deal room templates shipping the week of 2026-08-25 is how that load becomes clone-and-go instead of a Drive folder per account.

## Stage hygiene

Jade Brooks's playbook and Quinn Murphy's stage-exit criteria are the same rule: no economic buyer, no commit. Cascade Energy (Omar Haddad cannot produce a CFO meeting) and Summit Legal (Elena Brooks is not a buyer) are the tests of whether we mean that.
"""


def _win_loss() -> str:
    won = [d for d in DEALS if d.stage == "Closed won"]
    lost = [d for d in DEALS if d.stage == "Closed lost"]
    return f"""# Win/loss log

Jade Brooks owns the enablement reading of this file. Lena Ortiz owns the Pinecone Analytics write-up that is still in progress (due 2026-08-21).

## Closed won

{_bullets(f"{d.account} — {money(d.arr)}, owner {d.owner}, close {d.close_date}. Champion {d.champion}. {d.thesis}" for d in won)}

Brightpath Education is also a marketing object: Nina Shah needs a quote from the 2026-09-04 QBR for the October board pack. Paul Singh's job is that the QBR is about outcomes and expansion into the second campus, not about the old onboarding path they are still on.

## Closed lost

{_bullets(f"{d.account} — {money(d.arr)}, owner {d.owner}, close {d.close_date}. Champion {d.champion}. {d.risks}" for d in lost)}

Pinecone Analytics is the worked example in the Q3 Enterprise Playbook. Lost to doing nothing. Champion left. No compelling event. Should have been disqualified in week two. Theo Grant and Marcus Webb are the audience for that write-up; Lena and Jade are the authors.

## Still live, already teaching us something

- Meridian Health: earned an exec sponsor (Maya Chen on 2026-08-21). This is what 'metrics plus economic buyer plus a date' looks like
- Cobalt Financial: a close condition on a product date (Salesforce two-way sync, 2026-09-08). Riley Park has to keep that date honest
- Cascade Energy: inbound that never found a buyer. Recycle, do not commit
- Oakridge Industrial: a paid pilot with written success criteria, which is how Noah Berg wants every mid-market pilot to look
"""


def _meddpicc() -> str:
    return f"""# MEDDPICC as Harborline runs it

Jade Brooks. This is stricter than the textbook because last quarter's 18% miss was champion-identified deals sitting in forecast. Quinn Murphy's Forecast accuracy initiative is the process twin of this document.

## Metrics

A number the economic buyer already believes, not a number we modeled. Meridian Health: replace three tools for a 900-person revenue org. Brightpath Education: first forecast the board trusted. Summit Legal has no metrics yet, which is why it does not belong in commit.

## Economic buyer

CRO or CFO on enterprise (James Okada at Cobalt Financial, Meridian's CFO in the 2026-08-21 room). VP Sales on mid-market (Morgan Ellison at Brightpath, Samira Cole at Vellum Media). Directors of Sales Ops are champions, not buyers. Omar Haddad at Cascade Energy and Elena Brooks at Summit Legal are the live examples.

## Decision criteria

Must include where forecast will be submitted from. Oakridge Industrial wrote it down: four consecutive Fridays from {PRODUCT}, not the spreadsheet. If they cannot say where the forecast lives after we close, we are a seat and not a system of record.

## Decision process

Mutual close plan or it is not a close plan. Vellum Media has one, signature by 2026-09-22 because Samira Cole travels. Meridian Health is getting one the day of exec alignment, Marcus Webb's action.

## Paper process

Harborline paper unless Noah Berg and Dana Kim both agree otherwise. BAA for healthcare (Meridian, Redwood). DPA for Cobalt. Type II date in writing: 2026-10-15.

## Identify pain

The pain is a forecast the board does not trust, or deal rooms in Drive, or both. It is not 'we need another dashboard'. Helios Manufacturing's pain is plant managers who are not in Salesforce; that is a deal-room deal, which is why Harper Lin did not demo forecast first.

## Champion

Named, with a title, in the deal doc. If the champion cannot produce the buyer by a dated next step, recycle. Lena Ortiz has 2026-09-05 for Cascade Energy.

## Competition

Usually the status quo. See the competitive landscape doc. If they name Salesforce as the alternative, the answer is two-way sync, not rip-and-replace.
"""


# -------------------------------------------------------------- meetings --


def _meeting_doc(meeting: Meeting) -> str:
    attendees = [PEOPLE_BY_NAME[n] for n in meeting.attendees if n in PEOPLE_BY_NAME]
    deal = next((d for d in DEALS if d.account == meeting.related_deal), None)
    project = next((p for p in PROJECTS if p.name == meeting.related_project), None)
    lines = [
        f"# {meeting.title}",
        "",
        f"{meeting.date}. {meeting.kind} meeting.",
        "",
        f"**Attendees.** {_join(f'{p.name} ({p.role})' for p in attendees)}.",
        "",
        meeting.summary,
        "",
        "## Decisions",
        "",
        _bullets(meeting.decisions),
        "",
        "## Actions",
        "",
        _bullets(meeting.actions),
        "",
    ]
    if deal:
        lines += [
            "## Related deal",
            "",
            f"{deal.account} — {money(deal.arr)} ARR, {deal.stage}, close {deal.close_date}, "
            f"owner {deal.owner}, SE {deal.se or 'none'}, CSM {deal.csm or 'none'}. "
            f"Champion: {deal.champion}.",
            "",
            deal.thesis,
            "",
            f"Next step on the deal: {deal.next_step}",
            "",
        ]
    if project:
        lines += [
            "## Related project",
            "",
            f"{project.name}, owner {project.owner}, {project.status}, due {project.due}, "
            f"area {project.area}. Members: {_join(project.members)}.",
            "",
            project.summary,
            "",
            "Open tasks on this project:",
            "",
            _bullets(
                f"{t.title} — {t.assignee}, {t.status}, due {t.due}. {t.notes}"
                for t in tasks_in(project.name)
            )
            or "- None open.",
            "",
        ]
    missing = [n for n in meeting.attendees if n not in PEOPLE_BY_NAME]
    if missing:
        lines += [
            "## External attendees",
            "",
            _join(missing) + " (customer or partner side).",
            "",
        ]
    lines += [
        "## Follow-up hygiene",
        "",
        f"Actions from {meeting.title} belong on the project or the deal, not "
        f"in a private Slack DM. Priya Raman will ask about them in Friday "
        f"inspect if they are commercial; Riley Park will ask in Product x "
        f"Sales if they are dates we have already given a customer.",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------- projects --


def _project_doc(project: Project) -> str:
    owner = PEOPLE_BY_NAME[project.owner]
    members = [PEOPLE_BY_NAME[n] for n in project.members if n in PEOPLE_BY_NAME]
    tasks = tasks_in(project.name)
    meetings = meetings_for_project(project.name)
    return f"""# Project: {project.name}

Owner: {owner.name}, {owner.role} ({owner.email}). Status: {project.status}. Due: {project.due}. Area: {project.area}.

## Why this exists

{project.summary}

## What done looks like

{project.success}

## Staffing

{_bullets(f"{p.name}, {p.role} ({p.location})" for p in members)}

Chris Navarro froze unrelated platform sprint work through 2026-09-08 because Salesforce two-way sync and deal rooms are both on Luis Ortega and Mei Huang. If this project needs those two, it is already in the freeze conversation, not a new ask.

## Tasks

{_bullets(f"{t.title} — {t.assignee}, {t.status}, {t.priority}, due {t.due}. {t.notes}" for t in tasks) or '- No tasks captured.'}

## Meetings that moved this project

{_bullets(f"{m.date} — {m.title}. {m.summary}" for m in meetings) or '- None captured.'}

## Dependencies on other live objects

Projects at {COMPANY} are coupled on purpose. Deal room templates has to land for Meridian Health's exec alignment to stay honest. Salesforce two-way sync has to land for Cobalt Financial and for Cam Diaz's AppExchange plan. SOC 2 Type II is in every healthcare and fintech security pack. Onboarding 2.0 is in every proposal past that stage. Do not staff a fifth project onto Harper Lin, Luis Ortega, or Ivy Chen without taking something off.
"""


def _project_status(project: Project) -> str:
    tasks = tasks_in(project.name)
    in_prog = [t for t in tasks if t.status == "In progress"]
    not_started = [t for t in tasks if t.status == "Not started"]
    done = [t for t in tasks if t.status == "Done"]
    return f"""# Status: {project.name}

Week of 2026-08-17. Owner {project.owner}. Overall: {project.status}. Due {project.due}.

## This week

{project.summary}

## In progress

{_bullets(f"{t.title} ({t.assignee}, due {t.due}). {t.notes}" for t in in_prog) or '- Nothing in progress, which is itself a smell if the project is marked In progress.'}

## Not started, still in Q3

{_bullets(f"{t.title} ({t.assignee}, due {t.due}). {t.notes}" for t in not_started) or '- Queue is empty.'}

## Done

{_bullets(f"{t.title} ({t.assignee})." for t in done) or '- Nothing marked done in this snapshot.'}

## What would make this slip

If {project.owner} is pulled onto a deal fire drill. The live fire drills are Meridian Health (Thursday exec alignment), Cobalt Financial (architecture review the same day), and Redwood Clinics (technical validation 2026-08-27). Harper Lin, Dana Kim, and Luis Ortega are on more than one of those. {project.name} only stays on {project.due} if those rooms do not steal its people without a backfill.

## Ask

Unblock the {t.priority if (t := (in_prog or not_started or [None])[0]) else 'listed'} work rather than adding scope. Success is still: {project.success}
"""


# ---------------------------------------------------------------- tasks --


def _task_register() -> str:
    by_status: dict[str, list[Task]] = {}
    for t in TASKS:
        by_status.setdefault(t.status, []).append(t)
    chunks = ["# Q3 task register", "", f"{len(TASKS)} live tasks across {len(PROJECTS)} projects. Quinn Murphy and the project owners keep this honest; it is not a second Jira."]
    for status, items in by_status.items():
        chunks += ["", f"## {status}", "", _bullets(
            f"{t.title} — {t.assignee} on {t.project}, {t.priority}, due {t.due}. {t.notes}"
            for t in items
        )]
    chunks += [
        "",
        "## Load hotspots",
        "",
        "Harper Lin: Meridian security questionnaire, standard security-pack section, manufacturing demo org. She is also in Meridian, Cobalt, Redwood, Northwind, Vellum, and Helios rooms this fortnight.",
        "",
        "Luis Ortega: Salesforce sandbox, sync rollback note, access-review screenshots. Staff Engineer hire (Elena Voss, Chris Navarro) is the actual unblock.",
        "",
        "Dana Kim: Type II date letter, BAA draft. Same week as Cobalt architecture review and Meridian exec alignment.",
        "",
        "Lena Ortiz: Pinecone win/loss, Northwind proposal, Vellum close plan, Cascade CFO meeting.",
    ]
    return "\n".join(chunks)


def _project_tasks(project: Project) -> str:
    tasks = tasks_in(project.name)
    return f"""# Tasks: {project.name}

Project owner {project.owner}. Project due {project.due}. These tasks are how {project.name} actually moves; the project brief is the why.

{project.summary}

## Board

{_bullets(f"[{t.status} / {t.priority}] {t.title} — {t.assignee}, due {t.due}. {t.notes}" for t in tasks) or '- No tasks, which means the project is a slogan.'}

## People named here

{_join(sorted({t.assignee for t in tasks})) or 'none'}

If a name on this list is also on Meridian Health, Cobalt Financial, or SOC 2 Type II this week, assume slip risk and talk to {project.owner} before adding more.
"""


def _blocked_tasks() -> str:
    blockedish = [
        t
        for t in TASKS
        if "need" in t.notes.lower()
        or "block" in t.notes.lower()
        or t.priority == "High" and t.status == "Not started"
        or "Dana Kim" in t.notes
        or "close condition" in t.notes.lower()
    ]
    # always include a coherent set
    names = {t.title for t in blockedish}
    extras = [
        t for t in TASKS
        if t.title in (
            "Meridian security questionnaire",
            "Type II date letter",
            "Salesforce sandbox connected",
            "Deal-room clone path",
            "Field mapping signed with RevOps",
            "BAA draft for healthcare deals",
            "AppExchange submission plan",
        ) and t.title not in names
    ]
    items = blockedish + extras
    seen: set[str] = set()
    uniq: list[Task] = []
    for t in items:
        if t.title not in seen:
            seen.add(t.title)
            uniq.append(t)
    return f"""# Blocked and load-bearing tasks

Not a formal 'blocked' column. These are the tasks other dates are standing on. If they move, customer-facing dates move with them.

{_bullets(f"{t.title} — {t.assignee} on {t.project}, {t.status}, due {t.due}. {t.notes}" for t in uniq)}

## Customer dates these are holding up

- Meridian Health exec alignment 2026-08-21 and close 2026-09-12: security questionnaire, BAA, deal-room clone path, named CSM language
- Cobalt Financial close 2026-09-30: Type II letter, Salesforce sandbox, field mapping, sync limited availability
- Redwood Clinics validation 2026-08-27: audit-log evidence, reusable security pack
- Northwind Logistics COO review this week: rebuilt proposal on the new band
- Vellum Media signature 2026-09-22: mutual close plan, named CSM
- Website relaunch 2026-09-12: ICP page, no list prices
- October board 2026-10-02: ARR bridge, Brightpath case study, coverage slide
"""


def _this_week_tasks() -> str:
    due_soon = [
        t for t in TASKS
        if t.due <= "2026-08-26" and t.status != "Done"
    ]
    return f"""# This week (2026-08-17 through 2026-08-26)

Anything due in this window, across every project. This is the list Priya Raman, Riley Park, and Dana Kim should be able to recite.

{_bullets(f"{t.due} — {t.title} ({t.assignee}, {t.project}, {t.priority}, {t.status}). {t.notes}" for t in sorted(due_soon, key=lambda x: x.due))}

## Rooms that consume the same people

- Thu 08-21 morning-ish: Meridian Health exec alignment (Maya Chen, Jordan Hale, Marcus Webb, Harper Lin, Ivy Chen)
- Thu 08-21: Cobalt Financial legal and architecture (Marcus Webb, Harper Lin, Luis Ortega, Sasha Klein, Dana Kim)
- Fri 08-22: Helios demo (Theo Grant, Harper Lin) and Dana Kim's Type II letter / BAA
- Sun-Mon 08-24: Vellum close plan (Lena Ortiz, Harper Lin, Paul Singh)
- Wed 08-27: Redwood technical validation (Harper Lin again, plus Tess Nakamura and Mei Huang)

Harper Lin is in every one of those customer rooms. Deal room templates has to reduce her work this week, not add a side project.
"""


# ----------------------------------------------------------- enablement --


def _security_pack_guide() -> str:
    return f"""# Enterprise security pack, how we actually do it

Harper Lin writes it. Dana Kim signs it. Tess Nakamura and Luis Ortega supply the evidence. This is the section that will live inside Deal room templates once Mei Huang's clone path is GA, so we stop building a Drive folder per account.

## Always included

- Type I report (in the data room today)
- Type II date in writing: 2026-10-15, Dana Kim's letter, reusable
- Architecture: Salesforce stays CRM of record, {PRODUCT} is system of record for pipeline / forecast / deal rooms, two-way sync is Sasha Klein's project
- Audit logging: Tess Nakamura, customer-facing note for Redwood Clinics on 2026-08-27
- Access reviews: Luis Ortega

## Healthcare (Meridian Health, Redwood Clinics)

- BAA. Dana Kim's draft due 2026-08-25, reusable
- Named CSM (Ivy Chen) on the paper
- Do not invent a Gong replacement. Meridian has Gong; it stays

## Fintech (Cobalt Financial)

- DPA redline, in flight
- Sync limited availability by 2026-09-08 as a close condition, with a rollback note
- Architecture review with Luis Ortega (already on 2026-08-21)

## What is not in the pack

A promise of Type II before 2026-10-15. A four-org Salesforce design for Atlas Retail Group. A custom SKU. Anything Noah Berg has not seen.

## Live packs

- Meridian Health: two remaining questionnaire items, Harper Lin due 2026-08-22, then Thursday exec alignment
- Cobalt Financial: Type I in the room, Type II date going in writing 2026-08-20
- Redwood Clinics: clone of Meridian's pack after Thursday, before 2026-08-27
"""


def _onboarding_path() -> str:
    return f"""# Onboarding 2.0 path (fourteen days to first forecast)

Ivy Chen owns the project. Paul Singh is running it on mid-market. Brightpath Education is still on the old Zoom-tour path, which is why the 2026-09-04 QBR has to be steered away from implementation residue.

## Day 0, on the order form

Named CSM. Enterprise: Ivy Chen. Mid-market: Paul Singh. Sam Torres's task is that this language is already on the form, which Meridian Health asked for on the exec alignment.

## Days 1–3

Admin connect. Salesforce connected if they have it (and they almost always do). Quinn Murphy's field mapping is the default; we do not invent a parallel forecast field. If they are Cobalt Financial or Meridian Health, they are also a Salesforce two-way sync limited-availability customer, which is Sasha Klein and Luis Ortega, not CS.

## Days 4–8

Pipeline imported. Deal room cloned from Harper Lin's template, not a Drive folder. Forecast board created. Champion and economic buyer from the deal doc become the first two users who must log in.

## Days 9–14

First forecast Friday submitted from {PRODUCT}. That is the SLA. Oakridge Industrial's paid pilot is this SLA times four. If a logo misses two Fridays, CS owns the escalation to Sam Torres, not the AE.

## What we will not do

A custom onboarding for Atlas Retail Group's four Salesforce orgs in Q3. A free implementation for Helios Manufacturing. Leaving Brightpath on the old path and calling 2.0 done anyway — the next logo (Meridian or Cobalt or Vellum) has to land on 2.0 or the project is theater.
"""


def _forecast_rules() -> str:
    return """# Forecast stage-exit criteria

Quinn Murphy. Used in Friday inspect. This is how commit missed 18% last quarter and how it does not miss that way again.

## Stages that may sit in commit

Proposal, Mutual close plan, Negotiation, Legal review, Pilot in progress (if paid, with written success criteria), Closed won.

Champion identified and Qualification are never commit. Cascade Energy and Summit Legal were pulled on 2026-08-11 for that reason. Demo scheduled (Helios Manufacturing) is a date, not a number.

## Exit criteria, compressed

- Discovery: champion named, pain named, next meeting with a buyer or it recycles
- Qualification / Champion identified: economic buyer identified or recycle by a dated step (Cascade: 2026-09-05)
- Demo / Technical validation: SE (Harper Lin) in the room for anything above $200k or anything with a BAA
- Proposal: new mid-market band or enterprise discounting rules, named CSM, annual billing
- Mutual close plan: a date the buyer can actually sign (Vellum: 2026-09-22 because Samira Cole travels)
- Negotiation / Legal: Dana Kim on paper, no invented product dates

## Who may put a deal in commit

Quinn Murphy. AEs propose. Priya Raman inspects. Jordan Hale does not overlay a second number. Maya Chen does not have a private forecast.

## This week's inspect list

Marcus Webb: Meridian Health, Cobalt Financial, Redwood Clinics. Atlas Retail Group is Q4.
Lena Ortiz: Northwind Logistics, Vellum Media. Cascade Energy upside only.
Theo Grant: Oakridge Industrial (pilot), Helios Manufacturing (not commit), Summit Legal (qualify or kill).
"""


def _ae_enterprise_runbook() -> str:
    return """# Enterprise AE runbook (Marcus's book, generalized)

Written by Jade Brooks with Marcus Webb, for the three AEs Elena Voss is hiring and for anyone covering if Marcus is in a Meridian/Cobalt/Redwood tunnel.

## Account team

AE + Harper Lin + Ivy Chen once past Proposal. Maya Chen as exec sponsor only when Priya Raman says the deal earned it. Dana Kim on every security pack. Luis Ortega on any architecture review that names Salesforce sync.

## First thirty days of a new enterprise logo

Ben Choi or Aisha Patel sources. AE qualifies. If there is no economic buyer path in two meetings, it is Atlas-shaped: interesting, Q4, not commit. If there is a buyer and a date, Harper Lin builds the deal room from the template, not from Drive.

## Live book as the worked examples

- Meridian Health: earned Maya, BAA, named CSM, deal rooms as a date. This is the good one
- Cobalt Financial: product close condition (sync 2026-09-08). Do not give product dates Harper and Sasha have not signed
- Redwood Clinics: will copy Meridian's pack. Healthcare means BAA. Tess Nakamura in the 08-27 room
- Atlas Retail Group: four Salesforce orgs, junior champion. Discovery happened 08-20. Do not spend the next two weeks here

## What not to do

Do not promise Type II before 2026-10-15. Do not promise four-org sync. Do not trade a 15% holdback for a feature date. Do not add Lena Ortiz or Theo Grant as 'backup AE' on enterprise paper.
"""


def _ae_midmarket_runbook() -> str:
    return """# Mid-market AE runbook

Jade Brooks, with Lena Ortiz and Theo Grant. Appendix to the Q3 Enterprise Playbook so the playbook does not read as enterprise-only.

## Defaults

New price band. Annual billing. Named CSM (Paul Singh) on the proposal. Harper Lin only when the deal earns an SE (Northwind, Vellum, Helios, Oakridge). MEDDPICC one-pager, not the enterprise inspection.

## Worked examples

- Brightpath Education: closed-won $72k, grandfathered pricing, case study path, still on old onboarding. Do not copy the onboarding
- Northwind Logistics: first quote on the new band, they asked for monthly, Lena said no, COO this week
- Vellum Media: mid-market money, enterprise hygiene, signature by 2026-09-22
- Oakridge Industrial: paid pilot, four forecast Fridays, Paul Singh owns cadence
- Helios Manufacturing: good demo, seasonal budget, Q4 proposal, not a free pilot
- Cascade Energy: inbound, no buyer, recycle date 2026-09-05
- Summit Legal: probably not ICP, qualify or disqualify this week
- Pinecone Analytics: already lost, write it up, do not reopen

## Deal desk

Quinn Murphy. Exceptions to Noah Berg. There are no AE-invented SKUs while the mid-market pricing refresh is open.
"""


def _csm_enterprise() -> str:
    return """# Enterprise CSM brief for logos about to close

Ivy Chen. Meridian Health, Cobalt Financial, Redwood Clinics. This is the brief Sam Torres wants attached to the order form.

## Named on paper

Meridian asked. Cobalt will copy. Redwood should not be a surprise. Language is Ivy Chen's task due 2026-08-25.

## First thirty days

Onboarding 2.0, not the Brightpath-era Zoom tour. If Salesforce two-way sync is on the paper (Cobalt, likely Meridian), Sasha Klein and Luis Ortega are in the first-week thread, not as a CS escalation after week three.

## QBR posture

Outcomes and expansion, not implementation. Paul Singh is rehearsing that posture on Brightpath's 2026-09-04 QBR so the first enterprise QBR does not invent it.

## Escalations

Security: Dana Kim. Product dates already given to the customer: Riley Park. Commercial: Marcus Webb and Priya Raman. Do not let a CISO conversation become an AE-only thread in #hl-deals.
"""


def _partner_one_pager() -> str:
    return """# Partner program, first wave

Cam Diaz. Project due 2026-09-30.

## Northstar RevOps

Ready to sign without AppExchange. Paper going this week. They will be the face of Owen Frost's September webinars, which exist to close Alex Okonkwo's 1.6x enterprise coverage gap, not to generate more mid-market inbound Sofia Reyes is already covering.

## Fieldline Consulting

Will not sign until there is a dated AppExchange plan. Sasha Klein owes that plan by 2026-09-12 even if the listing is October, because the listing depends on Salesforce two-way sync limited availability (2026-09-08).

## What partners are allowed to say

ICP, MEDDPICC as we run it, deal rooms, forecast as system of record. They are not allowed to quote the mid-market band from memory while Quinn Murphy's refresh is open, and they are not allowed to promise Type II before 2026-10-15.

## What this is not

A reseller motion. A second AE team. A way to cover Atlas Retail Group without Marcus Webb.
"""


def _data_room_index() -> str:
    return f"""# Customer data room index

What is actually in the room Harper Lin attaches to an enterprise deal, so AEs stop asking in Slack.

## Trust

- SOC 2 Type I report
- Type II date letter (Dana Kim, 2026-10-15) once the 2026-08-20 task closes
- BAA draft for healthcare (due 2026-08-25)
- DPA template Noah Berg and Dana Kim share

## Product

- Architecture one-pager: Salesforce + {PRODUCT} + sync (Sasha Klein)
- Deal-room template screenshots once Mei Huang's clone path is flag-flipped for Harper
- Audit-log note (Tess Nakamura)

## Commercial

- Enterprise paper, not the customer's paper, unless Noah Berg says otherwise
- Named CSM language (Ivy Chen)
- Discounting cap 22%

## Do not put in the room

Internal OKRs. The Pinecone win/loss. Headcount plans. The 1.6x coverage slide. Anything from #hl-exec or #hl-board.
"""


def _allhands_followup() -> str:
    return """# All-hands August, follow-up notes

Meeting date 2026-08-07. Maya Chen, Jordan Hale, Riley Park, Dana Kim, Elena Voss, Noah Berg on stage. This is the internal reading copy; Alex Okonkwo is pulling a clip for the website relaunch.

## What was said that is now company policy

- We sell a GTM operating system, not a forecast tool. Nina Shah's relaunch copy has to catch up by 2026-09-12
- Brightpath Education is the customer we will talk about in October, if the QBR is green
- Enterprise coverage is the GTM problem of Q3. That is Alex and Owen, not a surprise AE hire
- SOC 2 Type II is a company project with a date (2026-10-15), not an engineering side quest
- Open reqs: three AEs, Staff Engineer, Head of Data. The last of those slips first if both big deals slip

## What was not said and should not be inferred

No one promised Meridian Health or Cobalt Financial as booked. No one promised AppExchange this quarter. No one promised four-org Salesforce.

## Actions already living on projects

Elena Voss x Chris Navarro: Staff Engineer spec (task due 2026-08-21).
Alex Okonkwo: relaunch copy from the narrative (task due 2026-08-28).
Nina Shah: Brightpath case study outline (task due 2026-08-26).
"""


def _hiring_plan() -> str:
    return f"""# Q3 hiring against the Series B plan

Elena Voss. Headcount is {HEADCOUNT}. Noah Berg will slip Head of Data before SOC 2. He will not slip the Staff Engineer if Luis Ortega remains on both sync and Type II evidence.

## Three Account Executives, reporting to Priya Raman

Why: Marcus Webb is on four live enterprise deals. The 2026-08-17 1:1 with Jordan Hale already narrowed Marcus to Meridian, Cobalt, and Redwood for two weeks. That is a staffing problem, not a coaching problem. Jade Brooks's enterprise runbook is the onboarding path for whoever accepts.

## Staff Engineer, reporting to Tess Nakamura

Why: Luis Ortega is the named architect on Cobalt Financial and the access-review owner on SOC 2. Chris Navarro's sprint freeze through 2026-09-08 is a patch. The hire is the fix. Spec due 2026-08-21.

## Head of Data, reporting to Dana Kim

Why: real, but not this quarter's close path. Slips if Meridian and Cobalt both move to Q4.

## What we are not hiring in Q3

A second SE (Harper Lin's unblock is deal-room templates, not a hire). A dedicated enablement contractor (Jade Brooks is in seat). A partner AE (Cam Diaz is launching a program, not a channel sales team).
"""


def _risk_register() -> str:
    return """# Q3 risk register

Noah Berg and Maya Chen. The live risks that would change the October board story.

## Concentration on Marcus Webb and Harper Lin

Four enterprise deals, one AE, one SE. Mitigation already in motion: Marcus's two-week focus list, deal-room templates so Harper clones instead of builds, three AE reqs. Residual risk: if Meridian's Thursday room goes badly, Cobalt the same day inherits the mood.

## Product dates that are now customer dates

Deal rooms week of 2026-08-25 (Meridian). Sync 2026-09-08 (Cobalt). Riley Park, Drew Patel, Sasha Klein, Chris Navarro, Mei Huang, Luis Ortega. Sprint freeze is the mitigation. Residual risk: both dates sit on the same two engineers.

## Type II date in writing

Once Dana Kim's 2026-08-20 letter goes out, 2026-10-15 is a commitment to Cobalt, Meridian, and Redwood. Mitigation: evidence window already open, audit log in the platform sprint. Residual risk: access reviews are screenshots Luis has not taken yet.

## Brightpath as the board narrative

If the 2026-09-04 QBR is about the old onboarding path, Maya does not have a customer story. Mitigation: Paul Singh's brief, Nina Shah in the room for a quote, Onboarding 2.0 for the next logo even if Brightpath cannot be retrofitted.

## Enterprise coverage 1.6x

Not a this-week close risk. A board-slide risk. Mitigation: Owen Frost and Cam Diaz September webinars, Northstar RevOps signing. Residual risk: webinars are not pipeline today.

## Pricing exceptions

If Meridian gets a holdback, Northwind gets monthly, and someone grandfathers a second Brightpath, the mid-market pricing refresh is theater. Mitigation: Noah Berg in the pricing committee, Lena already held annual on Northwind, Maya already said no holdback if deal rooms GA.
"""


# -------------------------------------------------------------- assemble --


def _company_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("Company",)
    return [
        (folder, "About Harborline", _about()),
        (folder, "Org chart and reporting lines", _org_chart()),
        (folder, "GTM motion, ICP, and personas", _gtm_motion()),
        (folder, "Series B narrative and use of proceeds", _series_b()),
        (folder, "Operating cadence", _operating_cadence()),
        (folder, "Competitive landscape", _competitive()),
        (folder, "Pricing and packaging", _pricing()),
        (folder, "Q3 company OKRs", _okrs()),
        (folder, "Q3 meeting calendar", _cadence_calendar()),
        (folder, "Q3 hiring against the Series B plan", _hiring_plan()),
        (folder, "Q3 risk register", _risk_register()),
        (folder, "All-hands August follow-up", _allhands_followup()),
    ]


def _people_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("People",)
    docs: list[Doc] = [
        (
            folder,
            p.name,
            _person_doc(p),
        )
        for p in PEOPLE
    ]
    docs.extend(
        [
            (
                folder,
                "Team overview: Leadership",
                _team_overview(
                    "Leadership",
                    "Maya Chen",
                    "The six people who will be in the October board room, plus "
                    "the CRO's GTM staff which reports through Jordan Hale. Maya "
                    "Chen is the exec sponsor of Meridian Health this week and "
                    "of the Series B board pack all quarter.",
                ),
            ),
            (
                folder,
                "Team overview: Go-to-Market",
                _team_overview(
                    "Go-to-Market",
                    "Jordan Hale",
                    "VP-level GTM: Priya Raman (Sales), Alex Okonkwo (Marketing), "
                    "Sam Torres (CS), Cam Diaz (Partnerships). Jordan's Q3 is "
                    "the $4.1M commit and the 1.6x enterprise coverage gap.",
                ),
            ),
            (
                folder,
                "Team overview: Sales",
                _team_overview(
                    "Sales",
                    "Priya Raman",
                    "AEs, SDRs, SE, RevOps, enablement. This is the team on "
                    "every live deal. Concentration risk is Marcus Webb and "
                    "Harper Lin; process owner is Quinn Murphy; playbook owner "
                    "is Jade Brooks.",
                ),
            ),
            (
                folder,
                "Team overview: Marketing",
                _team_overview(
                    "Marketing",
                    "Alex Okonkwo",
                    "Demand gen (Owen Frost) and content (Nina Shah). Website "
                    "relaunch 2026-09-12 and September partner webinars are the "
                    "two Q3 outputs that matter to the board.",
                ),
            ),
            (
                folder,
                "Team overview: Customer Success",
                _team_overview(
                    "Customer Success",
                    "Sam Torres",
                    "Ivy Chen takes enterprise logos as they close. Paul Singh "
                    "has Brightpath today and every mid-market close coming. "
                    "Onboarding 2.0 is the project that makes that split real.",
                ),
            ),
            (
                folder,
                "Team overview: Product",
                _team_overview(
                    "Product",
                    "Riley Park",
                    "Drew Patel (Core Platform, deal rooms) and Sasha Klein "
                    "(Integrations, Salesforce sync). Customer-facing dates "
                    "are now their dates.",
                ),
            ),
            (
                folder,
                "Team overview: Engineering",
                _team_overview(
                    "Engineering",
                    "Dana Kim",
                    "Chris Navarro runs the org; Tess Nakamura runs platform; "
                    "Luis Ortega and Mei Huang are the two engineers every Q3 "
                    "date is sitting on. SOC 2 is Dana's, staffed here.",
                ),
            ),
        ]
    )
    return docs


def _deal_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("GTM Deals",)
    docs: list[Doc] = [(folder, f"Deal — {d.account}", _deal_doc(d)) for d in DEALS]
    docs.extend(
        [
            (folder, "Pipeline snapshot 2026-08-17", _pipeline()),
            (folder, "Win/loss log", _win_loss()),
            (folder, "MEDDPICC as Harborline runs it", _meddpicc()),
            (folder, "Enterprise AE runbook", _ae_enterprise_runbook()),
            (folder, "Mid-market AE runbook", _ae_midmarket_runbook()),
            (folder, "Enterprise CSM brief", _csm_enterprise()),
            (folder, "Enterprise security pack guide", _security_pack_guide()),
            (folder, "Customer data room index", _data_room_index()),
            (folder, "Partner program first wave", _partner_one_pager()),
        ]
    )
    return docs


def _meeting_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("Meetings",)
    docs: list[Doc] = []
    for m in MEETINGS:
        title = f"{m.date} — {m.title}"
        docs.append((folder, title, _meeting_doc(m)))
    return docs


def _project_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("Projects",)
    docs: list[Doc] = []
    for p in PROJECTS:
        docs.append((folder, f"Project — {p.name}", _project_doc(p)))
        docs.append((folder, f"Status — {p.name}", _project_status(p)))
    return docs


def _task_docs() -> list[Doc]:
    folder: tuple[str, ...] = ("Tasks",)
    docs: list[Doc] = [
        (folder, "Q3 task register", _task_register()),
        (folder, "Blocked and load-bearing tasks", _blocked_tasks()),
        (folder, "This week 2026-08-17", _this_week_tasks()),
        (folder, "Onboarding 2.0 path", _onboarding_path()),
        (folder, "Forecast stage-exit criteria", _forecast_rules()),
    ]
    for p in PROJECTS:
        docs.append((folder, f"Tasks — {p.name}", _project_tasks(p)))
    return docs


def all_docs() -> list[Doc]:
    """Every fixture document. Count is asserted so a quiet edit cannot drop under 100."""
    docs = (
        _company_docs()
        + _people_docs()
        + _deal_docs()
        + _meeting_docs()
        + _project_docs()
        + _task_docs()
    )
    titles = [title for _, title, _ in docs]
    if len(titles) != len(set(titles)):
        dup = {t for t in titles if titles.count(t) > 1}
        raise RuntimeError(f"duplicate document titles: {sorted(dup)}")
    if len(docs) < 100:
        raise RuntimeError(f"expected at least 100 docs, got {len(docs)}")
    return docs
