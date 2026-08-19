"""Canonical Harborline fixture universe.

Drive docs, Notion pages, and Slack threads all render from this module so the
three sources talk about the same people, deals, meetings, projects, and tasks.
Nothing here is source-specific: names, dates, and relationships are the
contract the document generators consume.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPANY = "Harborline"
PRODUCT = "Harborline OS"
LEGAL_NAME = "Harborline, Inc."
DOMAIN = "harborline.com"
HQ = "San Francisco"
FOUNDED = "2021"
STAGE = "Series B"
ARR = "$18.4M"
HEADCOUNT = 85
CUSTOMERS = 140
SEED_ROOT = "Seed - Harborline"

# Prefix so seeded channels do not collide with a real workspace's #general.
SLACK_PREFIX = "hl-"


@dataclass(frozen=True)
class Person:
    name: str
    role: str
    team: str
    location: str
    manager: str | None
    bio: str
    focus: str

    @property
    def email(self) -> str:
        first, last = self.name.lower().split()
        return f"{first}.{last}@{DOMAIN}"

    @property
    def handle(self) -> str:
        first, last = self.name.lower().split()
        return f"{first[0]}{last}"


@dataclass(frozen=True)
class Deal:
    account: str
    owner: str
    se: str | None
    csm: str | None
    stage: str
    arr: int
    close_date: str
    industry: str
    champion: str
    next_step: str
    thesis: str
    risks: str


@dataclass(frozen=True)
class Meeting:
    title: str
    date: str
    kind: str
    attendees: tuple[str, ...]
    related_deal: str | None
    related_project: str | None
    summary: str
    decisions: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class Project:
    name: str
    owner: str
    status: str
    due: str
    area: str
    members: tuple[str, ...]
    summary: str
    success: str


@dataclass(frozen=True)
class Task:
    title: str
    assignee: str
    project: str
    status: str
    due: str
    priority: str
    notes: str


PEOPLE: tuple[Person, ...] = (
    Person(
        "Maya Chen",
        "Chief Executive Officer",
        "Leadership",
        "San Francisco",
        None,
        "Founded Harborline after running revenue operations at two Series C "
        "SaaS companies that drowned in Salesforce reports, Gong snippets, and "
        "spreadsheet forecasts. She raised the $42M Series B with Northpeak "
        "Capital in March 2026 and is the executive sponsor of the Series B "
        "board pack.",
        "Keep the company on a path to $30M ARR by Q2 2027 without letting "
        "enterprise deals distort the mid-market motion.",
    ),
    Person(
        "Jordan Hale",
        "Chief Revenue Officer",
        "Leadership",
        "New York",
        "Maya Chen",
        "Jordan built the current GTM motion: enterprise AEs paired with a "
        "solutions consultant, mid-market AEs running a product-led close, and "
        "RevOps owning forecast accuracy. Previously VP Sales at a CPQ vendor.",
        "Q3 commit of $4.1M, with Meridian Health and Cobalt Financial as the "
        "two deals that make or miss the number.",
    ),
    Person(
        "Riley Park",
        "VP Product",
        "Leadership",
        "San Francisco",
        "Maya Chen",
        "Riley owns the Harborline OS roadmap. Core Platform (deal rooms, "
        "forecast, pipeline) reports through Drew Patel; Integrations through "
        "Sasha Klein. Riley sits on every Product x Sales weekly.",
        "Ship Salesforce two-way sync and deal-room templates before the "
        "Meridian Health legal close, or the deal slips to Q4.",
    ),
    Person(
        "Dana Kim",
        "Chief Technology Officer",
        "Leadership",
        "San Francisco",
        "Maya Chen",
        "Dana is the executive owner of SOC 2 Type II and the person legal "
        "sends when Cobalt Financial or Redwood Clinics ask for a security "
        "whitepaper. Reports: Chris Navarro (eng) plus the still-unfilled "
        "Head of Data role.",
        "Type II report in the auditor's hands by 2026-10-15 so enterprise "
        "legal reviews stop stalling.",
    ),
    Person(
        "Elena Voss",
        "VP People",
        "Leadership",
        "San Francisco",
        "Maya Chen",
        "Elena is hiring three AEs, one Staff Engineer, and a Head of Data "
        "against the Series B plan. She runs the weekly headcount meeting "
        "with Noah Berg and owns leveling for the GTM org.",
        "Close the Staff Engineer offer and open the Head of Data req before "
        "September board.",
    ),
    Person(
        "Noah Berg",
        "Chief Financial Officer",
        "Leadership",
        "San Francisco",
        "Maya Chen",
        "Noah owns the board pack, the mid-market pricing refresh with Quinn "
        "Murphy, and the cash runway narrative. He does not like deal desks "
        "that invent one-off SKUs for Meridian Health.",
        "Hold discounting on enterprise to 22% of list and fund SOC 2 without "
        "slipping the Q4 hiring plan.",
    ),
    Person(
        "Priya Raman",
        "VP Sales",
        "Go-to-Market",
        "New York",
        "Jordan Hale",
        "Priya runs the AE and SDR orgs. She inspects every deal above $200k "
        "in the Monday forecast call and is the internal champion of the Q3 "
        "Enterprise Playbook Jade Brooks is writing.",
        "Get Meridian Health and Cobalt Financial through legal, and keep "
        "mid-market AEs from stalling in 'champion identified'.",
    ),
    Person(
        "Alex Okonkwo",
        "VP Marketing",
        "Go-to-Market",
        "San Francisco",
        "Jordan Hale",
        "Alex owns demand gen, content, the website relaunch, and partner "
        "marketing with Cam Diaz. Pipeline coverage is 3.2x on mid-market and "
        "1.6x on enterprise — the enterprise gap is the Q3 fight.",
        "Website relaunch live by 2026-09-12 and 40 qualified enterprise "
        "opportunities added this quarter.",
    ),
    Person(
        "Sam Torres",
        "VP Customer Success",
        "Go-to-Market",
        "New York",
        "Jordan Hale",
        "Sam inherited a CS org that was still doing onboarding as a Zoom "
        "tour of the product. Onboarding 2.0, owned by Ivy Chen, is the "
        "project that is supposed to cut time-to-first-forecast from 34 days "
        "to 14.",
        "Keep Brightpath Education's QBR green and have a CSM named on every "
        "deal past Proposal.",
    ),
    Person(
        "Cam Diaz",
        "Director of Partnerships",
        "Go-to-Market",
        "San Francisco",
        "Jordan Hale",
        "Cam is launching the partner program with a first wave of three "
        "RevOps consultancies and a Salesforce AppExchange listing that "
        "depends on Sasha Klein's two-way sync actually shipping.",
        "Signed partner agreements with Northstar RevOps and Fieldline "
        "Consulting by end of September.",
    ),
    Person(
        "Marcus Webb",
        "Account Executive, Enterprise",
        "Sales",
        "New York",
        "Priya Raman",
        "Marcus owns Meridian Health, Cobalt Financial, Atlas Retail Group, "
        "and Redwood Clinics — the four largest live enterprise deals. He "
        "works every technical close with Harper Lin.",
        "Land Meridian Health this quarter. Everything else is coverage.",
    ),
    Person(
        "Lena Ortiz",
        "Account Executive, Mid-Market",
        "Sales",
        "Austin",
        "Priya Raman",
        "Lena's book is Northwind Logistics, Cascade Energy, Vellum Media, "
        "and the closed-lost Pinecone Analytics deal she is writing a "
        "win/loss on with Jade Brooks.",
        "Close Northwind and Vellum before 2026-09-30; recycle Cascade if "
        "the champion cannot get a CFO meeting.",
    ),
    Person(
        "Theo Grant",
        "Account Executive, Mid-Market",
        "Sales",
        "Chicago",
        "Priya Raman",
        "Theo closed Brightpath Education in July and is working Helios "
        "Manufacturing, Summit Legal, and Oakridge Industrial. He is the "
        "reference AE for the mid-market pricing refresh.",
        "Convert the Oakridge Industrial pilot to an annual and get Helios "
        "to a commercial proposal the week after demo.",
    ),
    Person(
        "Aisha Patel",
        "SDR Manager",
        "Sales",
        "New York",
        "Priya Raman",
        "Aisha runs Ben Choi and Sofia Reyes. Enterprise outbound is "
        "pointed at VP Sales and RevOps at 400–2,000 person B2B companies; "
        "mid-market is inbound plus a light outbound overlay.",
        "40 enterprise meetings set in Q3, with at least eight landing on "
        "Marcus Webb's calendar.",
    ),
    Person(
        "Harper Lin",
        "Solutions Consultant",
        "Sales",
        "New York",
        "Priya Raman",
        "Harper is the SE on every live enterprise deal and the owner of "
        "the Deal room templates project. She writes the security packs "
        "Dana Kim then signs.",
        "Unblock Meridian Health's security questionnaire and publish the "
        "standard deal-room template so AEs stop building them in Google "
        "Drive by hand.",
    ),
    Person(
        "Quinn Murphy",
        "RevOps Lead",
        "Sales",
        "San Francisco",
        "Priya Raman",
        "Quinn owns forecast accuracy, the mid-market pricing refresh with "
        "Noah Berg, and the Salesforce two-way sync requirements with Sasha "
        "Klein. Jordan Hale treats Quinn's forecast as the commit.",
        "Bring forecast miss from 18% to under 10% by the October board.",
    ),
    Person(
        "Jade Brooks",
        "Sales Enablement Lead",
        "Sales",
        "San Francisco",
        "Priya Raman",
        "Jade is writing the Q3 Enterprise Playbook and the Pinecone "
        "Analytics win/loss. She also trains new AEs on MEDDPICC as Harborline "
        "runs it, which is stricter than the textbook version.",
        "Playbook v1 in the hands of Marcus, Lena, and Theo by 2026-08-29.",
    ),
    Person(
        "Ben Choi",
        "Sales Development Representative",
        "Sales",
        "New York",
        "Aisha Patel",
        "Ben covers enterprise outbound on the East Coast. He sourced the "
        "Atlas Retail Group discovery that Marcus Webb is running and is "
        "working a thread into Summit Legal's RevOps team.",
        "Eight enterprise meetings this month, two of them net-new logos "
        "not already in the book.",
    ),
    Person(
        "Sofia Reyes",
        "Sales Development Representative",
        "Sales",
        "Miami",
        "Aisha Patel",
        "Sofia covers inbound mid-market plus LATAM-adjacent English-speaking "
        "accounts. She recycled the Cascade Energy inbound into Lena Ortiz's "
        "book after a three-week nurture.",
        "Keep inbound SLA under four hours and produce 12 mid-market SQLs "
        "in August.",
    ),
    Person(
        "Owen Frost",
        "Demand Generation Lead",
        "Marketing",
        "San Francisco",
        "Alex Okonkwo",
        "Owen runs paid, webinars, and the enterprise pipeline programs that "
        "are supposed to close Alex's 1.6x coverage gap. He co-owns the "
        "website relaunch with Nina Shah.",
        "Two enterprise webinars in September, jointly with Cam Diaz's "
        "partner channel.",
    ),
    Person(
        "Nina Shah",
        "Content Lead",
        "Marketing",
        "Denver",
        "Alex Okonkwo",
        "Nina owns the website relaunch copy, the customer narrative for "
        "Brightpath Education, and the competitive landscape doc AEs actually "
        "read. She sits in the Product x Sales weekly when messaging is on "
        "the agenda.",
        "Ship the relaunch and a Brightpath case study before the October "
        "board, where Maya wants a customer story in the pack.",
    ),
    Person(
        "Ivy Chen",
        "Enterprise Customer Success Manager",
        "Customer Success",
        "New York",
        "Sam Torres",
        "Ivy will be the named CSM on Meridian Health, Cobalt Financial, and "
        "Redwood Clinics the moment they close. She also owns Onboarding 2.0 "
        "as a project, which makes her the CS counterpart to Drew Patel.",
        "Stand up Onboarding 2.0 so the first enterprise logo this quarter "
        "does not get the old Zoom-tour onboarding.",
    ),
    Person(
        "Paul Singh",
        "Mid-Market Customer Success Manager",
        "Customer Success",
        "Austin",
        "Sam Torres",
        "Paul is the CSM on Brightpath Education (closed-won) and will take "
        "Northwind, Helios, Oakridge, and Vellum. He is writing the first "
        "QBR template with Sam Torres.",
        "Brightpath QBR on 2026-09-04 with a documented expansion path into "
        "their second campus.",
    ),
    Person(
        "Drew Patel",
        "Product Manager, Core Platform",
        "Product",
        "San Francisco",
        "Riley Park",
        "Drew owns deal rooms, forecast, and pipeline inside Harborline OS. "
        "He is the product counterpart on Deal room templates (Harper) and "
        "Onboarding 2.0 (Ivy), and he attends the Meridian technical calls "
        "when the customer asks about the object model.",
        "Deal rooms GA the week of 2026-08-25, in time for Harper to use "
        "them on Meridian and Cobalt.",
    ),
    Person(
        "Sasha Klein",
        "Product Manager, Integrations",
        "Product",
        "San Francisco",
        "Riley Park",
        "Sasha owns the Salesforce two-way sync, which is the integration "
        "Cobalt Financial's RevOps team has made a close condition and the "
        "listing Cam Diaz needs for AppExchange. Luis Ortega is the staff "
        "engineer on the work.",
        "Two-way sync in limited availability for Cobalt and Meridian by "
        "2026-09-08.",
    ),
    Person(
        "Chris Navarro",
        "VP Engineering",
        "Engineering",
        "San Francisco",
        "Dana Kim",
        "Chris runs the engineering org: Tess Nakamura's platform team, a "
        "still-forming integrations pod, and the SOC 2 evidence work Dana "
        "Kim sponsors. He is hiring the Staff Engineer Elena Voss has open.",
        "Keep Salesforce sync and SOC 2 from colliding on the same three "
        "engineers.",
    ),
    Person(
        "Tess Nakamura",
        "Engineering Manager, Platform",
        "Engineering",
        "San Francisco",
        "Chris Navarro",
        "Tess manages Luis Ortega and Mei Huang. Platform is on the hook for "
        "deal rooms, the object model Meridian's architects keep probing, "
        "and the audit logging SOC 2 is asking for.",
        "Audit log GA and deal-room performance under 400ms p95 before the "
        "Redwood Clinics technical validation on 2026-08-27.",
    ),
    Person(
        "Luis Ortega",
        "Staff Engineer",
        "Engineering",
        "San Francisco",
        "Tess Nakamura",
        "Luis is the staff engineer on Salesforce two-way sync and the "
        "person who will be named in Cobalt Financial's architecture review. "
        "He is also the internal reviewer on SOC 2 access-control evidence.",
        "Sync v1 against a production Salesforce sandbox by 2026-09-01.",
    ),
    Person(
        "Mei Huang",
        "Senior Engineer",
        "Engineering",
        "San Francisco",
        "Tess Nakamura",
        "Mei owns deal-room rendering and the audit-log work SOC 2 needs. "
        "She pairs with Drew Patel on the Core Platform weekly and with "
        "Harper Lin when a customer deal room misbehaves in a demo.",
        "Ship the shared deal-room template Harper can clone per opportunity.",
    ),
)

PEOPLE_BY_NAME: dict[str, Person] = {p.name: p for p in PEOPLE}

DEALS: tuple[Deal, ...] = (
    Deal(
        "Meridian Health",
        "Marcus Webb",
        "Harper Lin",
        "Ivy Chen",
        "Negotiation",
        420_000,
        "2026-09-12",
        "Healthcare",
        "Dr. Alicia Rowe, VP Revenue Cycle",
        "Exec alignment Thursday with Maya Chen and Jordan Hale; Harper Lin "
        "delivers the security questionnaire Dana Kim already reviewed.",
        "Replace a three-tool stack (Salesforce reports, a homegrown forecast "
        "sheet, and Gong) for a 900-person revenue organization across six "
        "hospitals. Harborline OS becomes the system of record for pipeline "
        "and deal rooms; Salesforce stays the CRM of record via Sasha Klein's "
        "two-way sync.",
        "Security questionnaire is six days late. Legal wants a BAA and a "
        "named CSM before signature. If deal rooms are not GA, they will "
        "try to hold 15% of ARR back as a milestone.",
    ),
    Deal(
        "Cobalt Financial",
        "Marcus Webb",
        "Harper Lin",
        "Ivy Chen",
        "Legal review",
        540_000,
        "2026-09-30",
        "Fintech",
        "James Okada, Chief Revenue Officer",
        "Redline the DPA this week. Luis Ortega joins the architecture "
        "review on 2026-08-21. SOC 2 Type I is already in the data room; "
        "they have asked for Type II timing in writing.",
        "Cobalt's CRO wants one operating system for a 70-person revenue "
        "team that currently forecasts in three regional spreadsheets. This "
        "is the largest live deal and the one that funds Q3 if Meridian slips.",
        "Close condition: Salesforce two-way sync in limited availability. "
        "Their counsel is slow. Type II is not done; Dana Kim must put a "
        "date in writing without promising a report we do not have.",
    ),
    Deal(
        "Atlas Retail Group",
        "Marcus Webb",
        "Harper Lin",
        None,
        "Discovery",
        310_000,
        "2026-10-31",
        "Retail",
        "Dana Whitfield, VP Sales",
        "Discovery workshop 2026-08-20. Ben Choi sourced the meeting; "
        "Marcus and Harper run it. Map their four-banner sales org before "
        "talking packaging.",
        "Atlas runs four retail banners with separate Salesforce orgs and no "
        "single forecast. Harborline OS would sit above those orgs. This is "
        "a 2026 Q4 deal unless discovery uncovers a forcing function.",
        "No economic buyer yet. Four Salesforce orgs is a Sasha Klein "
        "problem we have not scoped. Champion is enthusiastic and junior.",
    ),
    Deal(
        "Northwind Logistics",
        "Lena Ortiz",
        "Harper Lin",
        "Paul Singh",
        "Proposal",
        96_000,
        "2026-09-18",
        "Logistics",
        "Chris Lang, Head of RevOps",
        "Proposal review with Lena, Harper, and Chris Lang on 2026-08-19. "
        "Pricing uses the new mid-market band Quinn Murphy is socializing, "
        "not the old one.",
        "Northwind wants Harborline OS for a 22-person sales team that "
        "forecasts on Friday in a Google Sheet named 'real-forecast-v7'. "
        "This is the cleanest mid-market close on the board.",
        "They will try to pay monthly. Noah Berg has already said no. "
        "Paul Singh needs to be in the room so onboarding is part of the "
        "proposal, not a surprise.",
    ),
    Deal(
        "Brightpath Education",
        "Theo Grant",
        None,
        "Paul Singh",
        "Closed won",
        72_000,
        "2026-07-22",
        "Education",
        "Morgan Ellison, VP Enrollment",
        "QBR on 2026-09-04. Nina Shah is writing the case study. Paul Singh "
        "is mapping expansion into the second campus.",
        "First mid-market close of Q3 and the reference customer Maya Chen "
        "wants in the October board pack. They replaced a spreadsheet "
        "forecast and a neglected HubSpot pipeline.",
        "Expansion depends on the second campus VP, who was not in the "
        "original evaluation. If Onboarding 2.0 is late, the QBR will be "
        "about implementation, not expansion.",
    ),
    Deal(
        "Helios Manufacturing",
        "Theo Grant",
        "Harper Lin",
        "Paul Singh",
        "Demo scheduled",
        110_000,
        "2026-10-15",
        "Manufacturing",
        "Ruth Keller, VP Sales",
        "Demo 2026-08-22. Theo and Harper. Show deal rooms on a "
        "manufacturing-shaped pipeline, not the SaaS demo org.",
        "Helios sells capital equipment with nine-month cycles. They want "
        "deal rooms their SEs can share with plant managers who are not in "
        "Salesforce. If the demo lands, this becomes a Q4 proposal.",
        "Long cycle, seasonal budget. Champion has been ghosted internally "
        "before. Do not forecast this in Q3 commit.",
    ),
    Deal(
        "Redwood Clinics",
        "Marcus Webb",
        "Harper Lin",
        "Ivy Chen",
        "Technical validation",
        275_000,
        "2026-10-08",
        "Healthcare",
        "Priya Nandakumar, VP Operations",
        "Technical validation 2026-08-27 with Tess Nakamura and Mei Huang "
        "on audit logging and BAA scope. Harper runs the session.",
        "A 40-clinic network that needs a BAA, audit logs, and a deal room "
        "their regional directors will actually open. Smaller than Meridian "
        "but a second healthcare logo, which Maya wants for the board story.",
        "They will copy Meridian's security questionnaire. If Meridian is "
        "still stuck, Redwood stalls with it. Tess's p95 work is on the "
        "critical path.",
    ),
    Deal(
        "Cascade Energy",
        "Lena Ortiz",
        None,
        None,
        "Champion identified",
        85_000,
        "2026-11-20",
        "Energy",
        "Omar Haddad, Director of Sales Ops",
        "Lena needs a CFO meeting by 2026-09-05 or this recycles. Sofia "
        "Reyes stays on the nurture in parallel.",
        "Inbound from a webinar Owen Frost ran in June. Champion likes the "
        "product and cannot get time with finance. Classic mid-market stall.",
        "No economic buyer, no compelling event. Priya Raman has already "
        "told Lena not to put this in commit.",
    ),
    Deal(
        "Summit Legal",
        "Theo Grant",
        None,
        None,
        "Qualification",
        64_000,
        "2026-11-30",
        "Professional services",
        "Elena Brooks, RevOps Manager",
        "Theo qualifies against MEDDPICC this week. Ben Choi is still "
        "working a second thread into their managing partner.",
        "A 120-attorney firm whose 'pipeline' is a shared inbox. They may "
        "not be ICP: too small a revenue team, no Salesforce, no forecast "
        "cadence to replace.",
        "If metrics and economic buyer are both missing after one more "
        "call, Jade Brooks wants this marked disqualified, not nurtured.",
    ),
    Deal(
        "Pinecone Analytics",
        "Lena Ortiz",
        "Harper Lin",
        None,
        "Closed lost",
        48_000,
        "2026-08-01",
        "SaaS",
        "Jon Park, VP Sales",
        "Jade Brooks and Lena Ortiz complete the win/loss by 2026-08-21. "
        "Do not re-open this quarter.",
        "Lost to doing nothing. Champion left mid-evaluation. Pricing was "
        "not the issue; urgency was. This is the enablement example in the "
        "Q3 playbook of a deal that should have been disqualified in week "
        "two.",
        "Already lost. The only remaining work is the write-up so Theo and "
        "Marcus do not repeat the stall pattern.",
    ),
    Deal(
        "Vellum Media",
        "Lena Ortiz",
        "Harper Lin",
        "Paul Singh",
        "Mutual close plan",
        180_000,
        "2026-09-25",
        "Media",
        "Samira Cole, CRO",
        "Mutual close planning 2026-08-24 with Lena, Harper, and Samira "
        "Cole. Legal paper is the Harborline paper, not theirs.",
        "A mid-market deal that is behaving like a small enterprise: CRO "
        "champion, mutual close plan, security review already done. This is "
        "Lena's number if Northwind slips a week.",
        "CRO is traveling the last week of September. If paper is not in "
        "signature by 2026-09-22 it slides a month. Paul Singh must be "
        "named in the close plan.",
    ),
    Deal(
        "Oakridge Industrial",
        "Theo Grant",
        "Harper Lin",
        "Paul Singh",
        "Pilot in progress",
        125_000,
        "2026-10-22",
        "Industrial",
        "Hank Voss, VP Commercial",
        "Pilot readout 2026-09-10. Success criteria are already written: "
        "weekly forecast submitted from Harborline OS, not the spreadsheet, "
        "for four consecutive weeks.",
        "Paid pilot converting to an annual. Theo structured this the way "
        "Noah Berg wants every mid-market pilot structured: paid, time-boxed, "
        "written success criteria, named CSM.",
        "If they miss two forecast Fridays, the pilot fails. Paul Singh is "
        "on the hook for that cadence more than Theo is.",
    ),
)

DEALS_BY_ACCOUNT: dict[str, Deal] = {d.account: d for d in DEALS}

PROJECTS: tuple[Project, ...] = (
    Project(
        "Q3 Enterprise Playbook",
        "Jade Brooks",
        "In progress",
        "2026-08-29",
        "Enablement",
        ("Jade Brooks", "Priya Raman", "Marcus Webb", "Harper Lin", "Jordan Hale"),
        "A living playbook for any Harborline deal above $200k: MEDDPICC as "
        "we actually run it, when to pull Maya Chen into an exec alignment, "
        "when Harper Lin owns the deal room, and when a deal is disqualified "
        "rather than nurtured. Pinecone Analytics is the worked example of "
        "the failure case. Meridian Health is the worked example of a deal "
        "that earned an exec sponsor.",
        "v1 read by every AE and SE, and used in the 2026-08-18 GTM standup "
        "to inspect Cobalt Financial and Redwood Clinics.",
    ),
    Project(
        "Salesforce two-way sync",
        "Sasha Klein",
        "In progress",
        "2026-09-08",
        "Product",
        ("Sasha Klein", "Luis Ortega", "Mei Huang", "Drew Patel", "Quinn Murphy"),
        "Bidirectional sync of accounts, opportunities, and activities "
        "between Salesforce and Harborline OS. Cobalt Financial made limited "
        "availability a close condition. Cam Diaz needs the same work for "
        "the AppExchange listing. Quinn Murphy is writing the field-mapping "
        "RevOps will actually support.",
        "Cobalt and Meridian running against production sandboxes, with a "
        "written rollback, by 2026-09-08.",
    ),
    Project(
        "Series B board pack",
        "Noah Berg",
        "In progress",
        "2026-09-28",
        "Finance",
        ("Noah Berg", "Maya Chen", "Jordan Hale", "Riley Park", "Nina Shah"),
        "October board: ARR bridge, GTM efficiency, hiring against plan, "
        "and one customer narrative. Maya wants Brightpath Education in the "
        "pack. Jordan wants a clean story on why enterprise coverage is "
        "1.6x and what Alex Okonkwo is doing about it. Riley wants the "
        "roadmap dates that are also customer commitments (sync, deal rooms) "
        "to match what sales is saying in rooms.",
        "Deck locked 2026-09-25, dry run 2026-09-28, board 2026-10-02.",
    ),
    Project(
        "Mid-market pricing refresh",
        "Quinn Murphy",
        "In progress",
        "2026-09-05",
        "RevOps",
        ("Quinn Murphy", "Noah Berg", "Priya Raman", "Lena Ortiz", "Theo Grant"),
        "Replace the 2025 mid-market price book. Northwind Logistics is the "
        "first deal that should quote the new band. Theo Grant's Brightpath "
        "close used the old one and is grandfathered. Noah Berg will not "
        "approve another one-off SKU while this is open.",
        "New band published, deal-desk rules updated, Northwind proposal "
        "on the new numbers.",
    ),
    Project(
        "Partner program launch",
        "Cam Diaz",
        "In progress",
        "2026-09-30",
        "Partnerships",
        ("Cam Diaz", "Alex Okonkwo", "Owen Frost", "Sasha Klein"),
        "First wave: two RevOps consultancies (Northstar RevOps, Fieldline "
        "Consulting) and an AppExchange listing that cannot go live before "
        "Salesforce two-way sync. Owen Frost will co-run two September "
        "webinars through the partner channel.",
        "Both partner agreements signed and a dated AppExchange submission "
        "plan, even if the listing itself slips to October.",
    ),
    Project(
        "Onboarding 2.0",
        "Ivy Chen",
        "In progress",
        "2026-09-15",
        "Customer Success",
        ("Ivy Chen", "Sam Torres", "Paul Singh", "Jade Brooks", "Drew Patel"),
        "Replace the Zoom-tour onboarding with a fourteen-day path to first "
        "forecast. Brightpath Education is still on the old path, which is "
        "why Paul Singh's QBR is at risk of becoming an implementation "
        "meeting. The first enterprise logo this quarter (likely Meridian "
        "or Cobalt) must land on 2.0.",
        "Time-to-first-forecast SLA of 14 days, a named CSM in every "
        "proposal past that stage, and a runbook Jade can train to.",
    ),
    Project(
        "SOC 2 Type II",
        "Dana Kim",
        "In progress",
        "2026-10-15",
        "Security",
        ("Dana Kim", "Chris Navarro", "Tess Nakamura", "Luis Ortega", "Noah Berg"),
        "Type I is in the data room. Type II evidence window is open. "
        "Cobalt Financial has asked for a date in writing. Redwood Clinics "
        "and Meridian Health will copy the question. Audit logging (Mei "
        "Huang / Tess Nakamura) is the engineering blocker; access reviews "
        "are the process blocker.",
        "Auditor has a complete evidence bundle, and Dana can put 2026-10-15 "
        "in writing in every live enterprise security pack.",
    ),
    Project(
        "Website relaunch",
        "Nina Shah",
        "In progress",
        "2026-09-12",
        "Marketing",
        ("Nina Shah", "Alex Okonkwo", "Owen Frost", "Riley Park"),
        "The current site still describes Harborline as a 'forecast tool'. "
        "The relaunch positions Harborline OS as the GTM operating system, "
        "with a Brightpath Education story and an ICP page AEs can send. "
        "Alex has already told the board this date.",
        "Live on 2026-09-12 with ICP, pricing philosophy (not the numbers), "
        "and one customer narrative.",
    ),
    Project(
        "Deal room templates",
        "Harper Lin",
        "In progress",
        "2026-08-25",
        "Sales",
        ("Harper Lin", "Drew Patel", "Mei Huang", "Jade Brooks", "Marcus Webb"),
        "AEs are building deal rooms in Google Drive. The product needs a "
        "cloneable template per opportunity, with a security-pack section "
        "Harper can reuse on Meridian, Cobalt, and Redwood. Drew Patel has "
        "this as Core Platform GA the week of 2026-08-25.",
        "Harper clones a template for Meridian and Cobalt instead of a "
        "Drive folder, and Jade documents that step in the playbook.",
    ),
    Project(
        "Forecast accuracy initiative",
        "Quinn Murphy",
        "In progress",
        "2026-10-01",
        "RevOps",
        ("Quinn Murphy", "Priya Raman", "Jordan Hale", "Lena Ortiz", "Marcus Webb"),
        "Commit missed by 18% last quarter because champion-identified deals "
        "sat in forecast. The initiative is process, not a feature: stage "
        "exit criteria, a Friday inspect with Priya, and Jordan treating "
        "Quinn's number as the commit, not the AE rollup.",
        "Miss under 10% exiting Q3, with Cascade Energy and Summit Legal "
        "kept out of commit until they earn it.",
    ),
)

PROJECTS_BY_NAME: dict[str, Project] = {p.name: p for p in PROJECTS}

MEETINGS: tuple[Meeting, ...] = (
    Meeting(
        "Weekly GTM standup",
        "2026-08-11",
        "Internal",
        (
            "Jordan Hale",
            "Priya Raman",
            "Alex Okonkwo",
            "Sam Torres",
            "Quinn Murphy",
            "Jade Brooks",
        ),
        None,
        "Forecast accuracy initiative",
        "Inspected the Q3 commit. Quinn Murphy flagged that Cascade Energy "
        "and Summit Legal were still leaking into AE forecasts. Priya Raman "
        "pulled both out of commit. Jordan Hale asked for a written next "
        "step on Meridian Health before Thursday's exec alignment.",
        (
            "Cascade Energy and Summit Legal are upside only, not commit.",
            "Meridian Health remains in commit, with Maya Chen attending "
            "the Thursday exec alignment.",
        ),
        (
            "Marcus Webb: send Meridian exec briefing to Maya Chen by "
            "Wednesday noon.",
            "Jade Brooks: bring playbook v0.9 to next standup.",
        ),
    ),
    Meeting(
        "Weekly GTM standup",
        "2026-08-18",
        "Internal",
        (
            "Jordan Hale",
            "Priya Raman",
            "Alex Okonkwo",
            "Sam Torres",
            "Quinn Murphy",
            "Jade Brooks",
            "Cam Diaz",
        ),
        "Cobalt Financial",
        "Q3 Enterprise Playbook",
        "Used playbook v0.9 to inspect Cobalt Financial and Redwood Clinics. "
        "Cobalt is in legal review and blocked on a written Type II date. "
        "Alex Okonkwo reported enterprise coverage still at 1.6x; Owen "
        "Frost's September webinars are the plan, not a current pipeline.",
        (
            "Dana Kim will put 2026-10-15 in writing for Cobalt counsel.",
            "Redwood Clinics technical validation stays on 2026-08-27.",
        ),
        (
            "Dana Kim: Type II date letter by 2026-08-20.",
            "Cam Diaz: partner webinar briefs to Owen Frost by Friday.",
        ),
    ),
    Meeting(
        "Q3 forecast review",
        "2026-08-14",
        "Internal",
        (
            "Maya Chen",
            "Jordan Hale",
            "Priya Raman",
            "Quinn Murphy",
            "Noah Berg",
        ),
        None,
        "Forecast accuracy initiative",
        "Quinn Murphy's commit is $4.1M. Best case $5.0M if Meridian and "
        "Cobalt both sign. Noah Berg asked what slips if both slip: hiring "
        "the Head of Data, not SOC 2. Maya Chen will attend Meridian's "
        "exec alignment personally.",
        (
            "Q3 commit stays $4.1M. Meridian and Cobalt are the only two "
            "enterprise deals in it.",
            "Head of Data req may slip; SOC 2 may not.",
        ),
        (
            "Quinn Murphy: republish the commit board with stage-exit "
            "criteria visible to AEs.",
            "Elena Voss: hold Head of Data until after Meridian's Thursday "
            "call.",
        ),
    ),
    Meeting(
        "Meridian Health exec alignment",
        "2026-08-21",
        "Customer",
        (
            "Maya Chen",
            "Jordan Hale",
            "Marcus Webb",
            "Harper Lin",
            "Ivy Chen",
        ),
        "Meridian Health",
        "Deal room templates",
        "Dr. Alicia Rowe brought their CFO and CISO. Maya Chen committed to "
        "a named CSM (Ivy Chen) and a BAA. Harper Lin walked the security "
        "questionnaire; two items still need Dana Kim. The CFO asked whether "
        "deal rooms would be live before signature. Drew Patel's GA date of "
        "the week of 2026-08-25 was given as the answer.",
        (
            "Ivy Chen is the named CSM on the paper.",
            "No milestone holdback if deal rooms GA that week.",
        ),
        (
            "Harper Lin: remaining security answers by 2026-08-22.",
            "Dana Kim: sign the BAA draft by 2026-08-25.",
            "Marcus Webb: send close plan with dates the same day.",
        ),
    ),
    Meeting(
        "Cobalt Financial legal and architecture",
        "2026-08-21",
        "Customer",
        (
            "Marcus Webb",
            "Harper Lin",
            "Luis Ortega",
            "Sasha Klein",
            "Dana Kim",
        ),
        "Cobalt Financial",
        "Salesforce two-way sync",
        "James Okada's RevOps lead and counsel. Luis Ortega walked the "
        "Salesforce sync architecture. Counsel asked for Type II timing; "
        "Dana Kim said 2026-10-15 and that Type I is already in the data "
        "room. Limited availability of sync by 2026-09-08 remains a close "
        "condition.",
        (
            "Type II date of 2026-10-15 is now in writing.",
            "Sync limited availability for Cobalt by 2026-09-08 is a close "
            "condition, not a nice-to-have.",
        ),
        (
            "Luis Ortega: sandbox connected by 2026-09-01.",
            "Sasha Klein: field mapping signed off with Quinn Murphy.",
            "Marcus Webb: push paper to signature contingent on that date.",
        ),
    ),
    Meeting(
        "Atlas Retail discovery workshop",
        "2026-08-20",
        "Customer",
        ("Marcus Webb", "Harper Lin", "Ben Choi"),
        "Atlas Retail Group",
        None,
        "Dana Whitfield walked four retail banners, each with its own "
        "Salesforce org. There is no single forecast owner today. Harper "
        "Lin mapped a Harborline OS layer above the four orgs and was "
        "honest that Sasha Klein has not scoped four-org sync. Ben Choi "
        "stayed for the first hour; this was his sourced meeting.",
        (
            "This stays a Q4 deal. Do not put it in Q3 commit.",
            "Follow-up is a scoped integration conversation, not a demo.",
        ),
        (
            "Sasha Klein: one-page on multi-org Salesforce by 2026-08-27.",
            "Marcus Webb: identify the economic buyer above Dana Whitfield.",
        ),
    ),
    Meeting(
        "Product x Sales weekly",
        "2026-08-12",
        "Internal",
        (
            "Riley Park",
            "Drew Patel",
            "Sasha Klein",
            "Priya Raman",
            "Harper Lin",
            "Quinn Murphy",
        ),
        None,
        "Deal room templates",
        "Drew Patel confirmed deal rooms GA the week of 2026-08-25. Harper "
        "Lin said that date is now a customer-facing date on Meridian. "
        "Sasha Klein flagged that Cobalt's sync close condition and deal "
        "rooms are landing on the same two engineers (Luis, Mei).",
        (
            "Deal rooms date does not move. Sync limited availability stays "
            "2026-09-08.",
            "Chris Navarro will protect Luis and Mei from unrelated sprint "
            "work.",
        ),
        (
            "Mei Huang: template clone path demoable by 2026-08-22.",
            "Chris Navarro: freeze platform sprint scope through 09-08.",
        ),
    ),
    Meeting(
        "Product x Sales weekly",
        "2026-08-19",
        "Internal",
        (
            "Riley Park",
            "Drew Patel",
            "Sasha Klein",
            "Priya Raman",
            "Harper Lin",
            "Nina Shah",
        ),
        "Meridian Health",
        "Website relaunch",
        "Nina Shah joined for messaging: the site still says 'forecast "
        "tool' while every live deal is sold as a GTM operating system. "
        "Harper asked that the relaunch not ship a pricing page with "
        "numbers; Quinn's mid-market refresh is still open. Drew reported "
        "deal rooms still on track.",
        (
            "Website relaunch ships ICP and philosophy, not list prices.",
            "Deal rooms remain on the 08-25 GA.",
        ),
        (
            "Nina Shah: send ICP page draft to Riley and Priya.",
            "Harper Lin: screenshot the cloneable deal room for the "
            "playbook.",
        ),
    ),
    Meeting(
        "Board prep working session",
        "2026-08-13",
        "Internal",
        ("Maya Chen", "Noah Berg", "Jordan Hale", "Riley Park"),
        None,
        "Series B board pack",
        "First pass of the October board story. Maya wants Brightpath "
        "Education as the customer narrative. Jordan wants to get in front "
        "of the 1.6x enterprise coverage number rather than have a board "
        "member find it. Riley wants customer-facing roadmap dates and "
        "internal dates to be the same document.",
        (
            "Brightpath is the narrative, contingent on a green QBR.",
            "Coverage gap is a GTM slide, not a footnote.",
        ),
        (
            "Nina Shah: case study outline by 2026-08-26.",
            "Noah Berg: ARR bridge v1 by 2026-08-22.",
        ),
    ),
    Meeting(
        "Enablement playbook kickoff",
        "2026-08-06",
        "Internal",
        (
            "Jade Brooks",
            "Priya Raman",
            "Marcus Webb",
            "Lena Ortiz",
            "Theo Grant",
            "Harper Lin",
        ),
        "Pinecone Analytics",
        "Q3 Enterprise Playbook",
        "Jade Brooks kicked off the playbook with the AEs. Pinecone "
        "Analytics is the failure case: no compelling event, champion left, "
        "deal lingered. Meridian Health is the success case: metrics, "
        "economic buyer, and an earned exec sponsor. Theo Grant asked for a "
        "mid-market appendix so the playbook does not read as enterprise-only.",
        (
            "Playbook v1 on 2026-08-29, with a mid-market appendix.",
            "Pinecone win/loss is an input, not a sidebar.",
        ),
        (
            "Lena Ortiz: first draft of the Pinecone win/loss by 2026-08-15.",
            "Theo Grant: mid-market close notes from Brightpath to Jade.",
        ),
    ),
    Meeting(
        "Redwood Clinics technical validation",
        "2026-08-27",
        "Customer",
        (
            "Marcus Webb",
            "Harper Lin",
            "Tess Nakamura",
            "Mei Huang",
            "Ivy Chen",
        ),
        "Redwood Clinics",
        "SOC 2 Type II",
        "Priya Nandakumar brought their security lead. Tess Nakamura demoed "
        "audit logging. Mei Huang walked retention. They will send Meridian's "
        "security questionnaire with their letterhead on it. Ivy Chen joined "
        "so a CSM was in the room before paper.",
        (
            "Redwood will accept Type I plus a Type II date, same as Cobalt.",
            "BAA is required; Dana Kim's draft can be reused.",
        ),
        (
            "Harper Lin: clone the Meridian security pack for Redwood.",
            "Tess Nakamura: p95 note in writing for their security lead.",
        ),
    ),
    Meeting(
        "Vellum Media mutual close planning",
        "2026-08-24",
        "Customer",
        ("Lena Ortiz", "Harper Lin", "Paul Singh"),
        "Vellum Media",
        "Onboarding 2.0",
        "Samira Cole (CRO) agreed to Harborline paper, a security review "
        "already complete, and a close date of 2026-09-25. She travels the "
        "last week of September, so signature has to happen by 2026-09-22. "
        "Paul Singh is named in the close plan as CSM. This is a mid-market "
        "deal run with enterprise hygiene.",
        (
            "Close date 2026-09-25, signature by 2026-09-22.",
            "Paul Singh is the named CSM on the order form.",
        ),
        (
            "Lena Ortiz: send the close plan the same day.",
            "Paul Singh: Onboarding 2.0 path attached to the proposal.",
        ),
    ),
    Meeting(
        "Brightpath Education QBR planning",
        "2026-08-15",
        "Internal",
        ("Paul Singh", "Sam Torres", "Theo Grant", "Nina Shah"),
        "Brightpath Education",
        "Onboarding 2.0",
        "QBR is 2026-09-04. Brightpath is still on the old onboarding path, "
        "so the meeting will include implementation residue. Nina Shah needs "
        "a quote for the case study. Theo Grant will join for expansion into "
        "the second campus.",
        (
            "QBR agenda: outcomes first, implementation second, expansion "
            "third.",
            "Case study quote is a goal of the meeting, not a follow-up.",
        ),
        (
            "Paul Singh: send the QBR brief to Morgan Ellison.",
            "Nina Shah: three quote prompts for the QBR.",
        ),
    ),
    Meeting(
        "Engineering sprint review",
        "2026-08-14",
        "Internal",
        (
            "Chris Navarro",
            "Tess Nakamura",
            "Luis Ortega",
            "Mei Huang",
            "Drew Patel",
            "Sasha Klein",
        ),
        None,
        "Salesforce two-way sync",
        "Platform sprint: deal-room clone path and audit log are the only "
        "committed items. Luis Ortega has a Salesforce sandbox, not yet "
        "production-shaped. Chris Navarro froze unrelated scope through "
        "2026-09-08 after the Product x Sales weekly.",
        (
            "Sprint freeze through 09-08 holds.",
            "Audit log is a SOC 2 evidence item, not a nice-to-have.",
        ),
        (
            "Luis Ortega: production-shaped sandbox by 2026-08-28.",
            "Mei Huang: clone-path behind a feature flag for Harper.",
        ),
    ),
    Meeting(
        "Pricing committee",
        "2026-08-08",
        "Internal",
        (
            "Noah Berg",
            "Quinn Murphy",
            "Priya Raman",
            "Lena Ortiz",
            "Theo Grant",
        ),
        "Northwind Logistics",
        "Mid-market pricing refresh",
        "Approved the new mid-market band. Brightpath Education is "
        "grandfathered. Northwind Logistics must quote the new band. Noah "
        "Berg restated that enterprise discounting caps at 22% of list, "
        "which matters for Meridian if they ask for a milestone holdback.",
        (
            "New mid-market band is in force as of 2026-08-08.",
            "No one-off SKUs while the refresh is open.",
        ),
        (
            "Quinn Murphy: publish the band and deal-desk rules.",
            "Lena Ortiz: rebuild the Northwind proposal on the new numbers.",
        ),
    ),
    Meeting(
        "Partner program working session",
        "2026-08-16",
        "Internal",
        ("Cam Diaz", "Alex Okonkwo", "Owen Frost", "Sasha Klein"),
        None,
        "Partner program launch",
        "Northstar RevOps is ready to sign. Fieldline Consulting wants the "
        "AppExchange listing as a condition, which Sasha Klein cannot "
        "honestly promise before sync limited availability. Owen Frost will "
        "run September webinars with whoever is signed, not wait for both.",
        (
            "Northstar can sign without AppExchange. Fieldline cannot.",
            "Webinars proceed with Northstar in September.",
        ),
        (
            "Cam Diaz: send Northstar paper this week.",
            "Sasha Klein: dated AppExchange plan, even if the date is October.",
        ),
    ),
    Meeting(
        "Northwind Logistics proposal review",
        "2026-08-19",
        "Customer",
        ("Lena Ortiz", "Harper Lin", "Paul Singh"),
        "Northwind Logistics",
        "Mid-market pricing refresh",
        "Chris Lang (Head of RevOps) walked the proposal with Lena and "
        "Harper. They asked for monthly billing; Lena held annual, per Noah "
        "Berg. Paul Singh presented the Onboarding 2.0 path as part of the "
        "proposal, which is the point of having a CSM in the room this "
        "early.",
        (
            "Annual billing. New mid-market band. Named CSM on the form.",
            "They will take it to their COO this week.",
        ),
        (
            "Lena Ortiz: send the revised proposal the same day.",
            "Paul Singh: 14-day onboarding plan as an appendix.",
        ),
    ),
    Meeting(
        "Helios Manufacturing demo recap",
        "2026-08-22",
        "Customer",
        ("Theo Grant", "Harper Lin"),
        "Helios Manufacturing",
        "Deal room templates",
        "Ruth Keller brought two SEs. Harper Lin showed deal rooms on a "
        "manufacturing-shaped pipeline instead of the SaaS demo org. Interest "
        "was real; budget is seasonal. Theo Grant was explicit that this is "
        "not a Q3 commit deal.",
        (
            "Follow-up is a commercial proposal in Q4, not a Q3 push.",
            "Deal rooms are the feature that mattered; forecast was not.",
        ),
        (
            "Theo Grant: recap and a Q4 proposal skeleton.",
            "Harper Lin: keep the manufacturing demo org, do not revert.",
        ),
    ),
    Meeting(
        "All-hands August",
        "2026-08-07",
        "Internal",
        (
            "Maya Chen",
            "Jordan Hale",
            "Riley Park",
            "Dana Kim",
            "Elena Voss",
            "Noah Berg",
        ),
        "Brightpath Education",
        "Series B board pack",
        "Maya Chen recapped Series B, named Brightpath Education as the "
        "first mid-market close of the quarter, and said enterprise coverage "
        "is the GTM problem of Q3. Dana Kim said SOC 2 Type II is a company "
        "project, not an engineering side quest. Elena Voss named the open "
        "reqs: three AEs, one Staff Engineer, Head of Data.",
        (
            "Company narrative: GTM operating system, not forecast tool.",
            "SOC 2 is a company project with a date.",
        ),
        (
            "Alex Okonkwo: all-hands clip into the relaunch messaging.",
            "Elena Voss: Staff Engineer spec with Chris Navarro this week.",
        ),
    ),
    Meeting(
        "1:1 Jordan Hale and Priya Raman",
        "2026-08-17",
        "1:1",
        ("Jordan Hale", "Priya Raman"),
        "Meridian Health",
        "Forecast accuracy initiative",
        "Priya Raman is worried Marcus Webb's book is four live enterprise "
        "deals and that Atlas Retail is stealing time from Meridian and "
        "Cobalt. Jordan Hale agreed: Atlas is Q4, and Marcus should spend "
        "the next two weeks only on Meridian, Cobalt, and Redwood. Lena and "
        "Theo are not covering enterprise overflow.",
        (
            "Marcus Webb's next two weeks: Meridian, Cobalt, Redwood only.",
            "Atlas Retail discovery follow-ups can wait a week.",
        ),
        (
            "Priya Raman: say this in the next GTM standup so it is not a "
            "side conversation.",
            "Jordan Hale: tell Maya why she is in the Meridian room and "
            "not Atlas.",
        ),
    ),
    Meeting(
        "1:1 Marcus Webb and Harper Lin",
        "2026-08-18",
        "1:1",
        ("Marcus Webb", "Harper Lin"),
        "Meridian Health",
        "Deal room templates",
        "Working session on the Meridian security pack and the Cobalt "
        "architecture review. Harper Lin is carrying both, plus Redwood's "
        "validation the following week. They agreed Harper owns the packs "
        "and Marcus owns the commercial paper, and that deal-room templates "
        "shipping this week is what keeps this from becoming a Drive-folder "
        "fire drill.",
        (
            "Harper owns security packs. Marcus owns paper.",
            "If deal rooms slip, they still close Meridian on a Drive room "
            "rather than slip the date.",
        ),
        (
            "Harper Lin: Meridian remaining answers 2026-08-22.",
            "Marcus Webb: close-plan email after Thursday's exec alignment.",
        ),
    ),
    Meeting(
        "SOC 2 kickoff",
        "2026-08-05",
        "Internal",
        (
            "Dana Kim",
            "Chris Navarro",
            "Tess Nakamura",
            "Luis Ortega",
            "Noah Berg",
        ),
        None,
        "SOC 2 Type II",
        "Opened the Type II evidence window. Auditor's list is known. "
        "Engineering blockers are audit logging and access reviews. Noah "
        "Berg confirmed SOC 2 is protected even if a deal slips. Dana Kim "
        "will be the person whose name is on every customer letter.",
        (
            "Evidence window is open. Date in customer letters is 2026-10-15.",
            "Audit log is a committed platform item, not best-effort.",
        ),
        (
            "Tess Nakamura: audit-log evidence folder for the auditor.",
            "Luis Ortega: access-review screenshots from the last quarter.",
            "Noah Berg: vendor list for the subservice narrative.",
        ),
    ),
)

TASKS: tuple[Task, ...] = (
    Task(
        "Playbook v1 to AEs",
        "Jade Brooks",
        "Q3 Enterprise Playbook",
        "In progress",
        "2026-08-29",
        "High",
        "Incorporate Pinecone win/loss and a mid-market appendix from Theo "
        "Grant's Brightpath close. Inspect Cobalt and Redwood against it in "
        "the 08-18 standup.",
    ),
    Task(
        "Pinecone Analytics win/loss",
        "Lena Ortiz",
        "Q3 Enterprise Playbook",
        "In progress",
        "2026-08-21",
        "High",
        "Write it with Jade Brooks. Failure mode is 'no compelling event', "
        "not price. This is the worked example in the playbook.",
    ),
    Task(
        "MEDDPICC one-pager for mid-market",
        "Jade Brooks",
        "Q3 Enterprise Playbook",
        "Not started",
        "2026-08-27",
        "Medium",
        "Theo Grant and Lena Ortiz need a shorter inspection, not the "
        "enterprise version. Summit Legal should be run through it this week.",
    ),
    Task(
        "Salesforce sandbox connected",
        "Luis Ortega",
        "Salesforce two-way sync",
        "In progress",
        "2026-09-01",
        "High",
        "Production-shaped sandbox for Cobalt Financial. Close condition "
        "for their architecture review follow-up.",
    ),
    Task(
        "Field mapping signed with RevOps",
        "Sasha Klein",
        "Salesforce two-way sync",
        "In progress",
        "2026-08-26",
        "High",
        "Quinn Murphy has to agree which opportunity fields are sacred. "
        "Do not invent a parallel forecast field.",
    ),
    Task(
        "Sync rollback note",
        "Luis Ortega",
        "Salesforce two-way sync",
        "Not started",
        "2026-09-05",
        "Medium",
        "Cobalt counsel will ask. One page, written with Sasha Klein.",
    ),
    Task(
        "AppExchange submission plan",
        "Sasha Klein",
        "Partner program launch",
        "Not started",
        "2026-09-12",
        "Medium",
        "Dated plan for Cam Diaz, even if the listing is October. Fieldline "
        "Consulting is blocked on this.",
    ),
    Task(
        "ARR bridge v1",
        "Noah Berg",
        "Series B board pack",
        "In progress",
        "2026-08-22",
        "High",
        "New logos, expansion, churn. Brightpath in new logos. Call out "
        "that Meridian and Cobalt are not booked.",
    ),
    Task(
        "Brightpath case study outline",
        "Nina Shah",
        "Series B board pack",
        "In progress",
        "2026-08-26",
        "High",
        "Depends on a quote from the 09-04 QBR. Outline can exist before "
        "the quote; the board pack cannot ship without it.",
    ),
    Task(
        "GTM coverage slide",
        "Jordan Hale",
        "Series B board pack",
        "Not started",
        "2026-09-10",
        "High",
        "Get in front of 1.6x enterprise coverage. Alex Okonkwo's webinars "
        "are the forward-looking answer, not a current fact.",
    ),
    Task(
        "Publish new mid-market band",
        "Quinn Murphy",
        "Mid-market pricing refresh",
        "In progress",
        "2026-08-20",
        "High",
        "Deal-desk rules included. Brightpath grandfathered. Northwind on "
        "the new numbers.",
    ),
    Task(
        "Rebuild Northwind proposal",
        "Lena Ortiz",
        "Mid-market pricing refresh",
        "In progress",
        "2026-08-19",
        "High",
        "New band, annual billing, Paul Singh named. Goes to their COO "
        "this week.",
    ),
    Task(
        "Northstar RevOps agreement",
        "Cam Diaz",
        "Partner program launch",
        "In progress",
        "2026-08-22",
        "High",
        "They can sign without AppExchange. Send paper this week.",
    ),
    Task(
        "September partner webinars",
        "Owen Frost",
        "Partner program launch",
        "Not started",
        "2026-09-05",
        "Medium",
        "Two webinars, Northstar as the partner face. Aimed at the "
        "enterprise coverage gap, not mid-market inbound.",
    ),
    Task(
        "Onboarding 2.0 runbook",
        "Ivy Chen",
        "Onboarding 2.0",
        "In progress",
        "2026-09-01",
        "High",
        "Fourteen days to first forecast. Jade Brooks will train AEs and "
        "CSMs on it. Brightpath stays on the old path; the next logo does "
        "not.",
    ),
    Task(
        "Named CSM on every proposal past Proposal stage",
        "Sam Torres",
        "Onboarding 2.0",
        "In progress",
        "2026-08-22",
        "High",
        "Ivy Chen for enterprise, Paul Singh for mid-market. Vellum and "
        "Northwind already have Paul; Meridian and Cobalt have Ivy.",
    ),
    Task(
        "Brightpath QBR brief",
        "Paul Singh",
        "Onboarding 2.0",
        "In progress",
        "2026-08-25",
        "Medium",
        "Agenda: outcomes, implementation residue, second-campus expansion. "
        "Nina Shah's quote prompts attached.",
    ),
    Task(
        "Type II date letter",
        "Dana Kim",
        "SOC 2 Type II",
        "In progress",
        "2026-08-20",
        "High",
        "2026-10-15, in writing, reusable for Cobalt, Meridian, and "
        "Redwood. Do not promise a report we do not have.",
    ),
    Task(
        "Audit-log evidence folder",
        "Tess Nakamura",
        "SOC 2 Type II",
        "In progress",
        "2026-08-28",
        "High",
        "Auditor package plus a customer-facing note Tess can give Redwood "
        "on 2026-08-27.",
    ),
    Task(
        "Access-review screenshots",
        "Luis Ortega",
        "SOC 2 Type II",
        "Not started",
        "2026-09-04",
        "Medium",
        "Last quarter's reviews. Blocks the evidence bundle more than any "
        "remaining code.",
    ),
    Task(
        "ICP page draft",
        "Nina Shah",
        "Website relaunch",
        "In progress",
        "2026-08-24",
        "High",
        "VP Sales and RevOps at 200–2,000 person B2B companies. Riley Park "
        "and Priya Raman review. No list prices on the page.",
    ),
    Task(
        "Relaunch copy from all-hands narrative",
        "Alex Okonkwo",
        "Website relaunch",
        "Not started",
        "2026-08-28",
        "Medium",
        "Maya Chen's line: GTM operating system, not forecast tool. Pull "
        "the all-hands clip.",
    ),
    Task(
        "Deal-room clone path",
        "Mei Huang",
        "Deal room templates",
        "In progress",
        "2026-08-22",
        "High",
        "Feature-flagged for Harper Lin. Meridian and Cobalt are the first "
        "two clones. This is the GA date customers have already heard.",
    ),
    Task(
        "Meridian security questionnaire",
        "Harper Lin",
        "Deal room templates",
        "In progress",
        "2026-08-22",
        "High",
        "Two remaining items need Dana Kim. This is on the Thursday exec "
        "alignment critical path.",
    ),
    Task(
        "Standard security-pack section in the template",
        "Harper Lin",
        "Deal room templates",
        "Not started",
        "2026-08-25",
        "Medium",
        "Reusable for Redwood Clinics the following week. Stop building "
        "these in Drive.",
    ),
    Task(
        "Republish commit board with stage-exit criteria",
        "Quinn Murphy",
        "Forecast accuracy initiative",
        "In progress",
        "2026-08-18",
        "High",
        "Cascade Energy and Summit Legal out of commit. AEs can see why.",
    ),
    Task(
        "Friday forecast inspect",
        "Priya Raman",
        "Forecast accuracy initiative",
        "In progress",
        "2026-08-21",
        "High",
        "Weekly. Jordan Hale treats Quinn's number as the commit. Marcus's "
        "book inspected against Meridian/Cobalt/Redwood focus.",
    ),
    Task(
        "Disqualify or recycle Summit Legal",
        "Theo Grant",
        "Forecast accuracy initiative",
        "Not started",
        "2026-08-22",
        "Medium",
        "Run MEDDPICC. If metrics and economic buyer are both missing, "
        "mark disqualified per Jade Brooks, do not nurture.",
    ),
    Task(
        "CFO meeting for Cascade Energy",
        "Lena Ortiz",
        "Forecast accuracy initiative",
        "In progress",
        "2026-09-05",
        "Medium",
        "If Omar Haddad cannot produce a CFO meeting, recycle. Sofia Reyes "
        "keeps the nurture. Not in commit either way.",
    ),
    Task(
        "Staff Engineer hiring spec",
        "Elena Voss",
        "SOC 2 Type II",
        "In progress",
        "2026-08-21",
        "Medium",
        "With Chris Navarro. This hire is how Salesforce sync and SOC 2 "
        "stop colliding on Luis Ortega.",
    ),
    Task(
        "Named CSM language in order form",
        "Ivy Chen",
        "Onboarding 2.0",
        "Not started",
        "2026-08-25",
        "High",
        "Meridian asked for it on the Thursday call. Reuse for Cobalt.",
    ),
    Task(
        "Multi-org Salesforce one-pager",
        "Sasha Klein",
        "Salesforce two-way sync",
        "Not started",
        "2026-08-27",
        "Low",
        "Atlas Retail Group has four Salesforce orgs. This is a Q4 scope "
        "note, not Q3 build.",
    ),
    Task(
        "Manufacturing demo org preserved",
        "Harper Lin",
        "Deal room templates",
        "Not started",
        "2026-08-26",
        "Low",
        "Helios Manufacturing responded to a plant-manager deal room, not "
        "the SaaS demo. Do not revert the org.",
    ),
    Task(
        "Mutual close plan to Vellum",
        "Lena Ortiz",
        "Onboarding 2.0",
        "In progress",
        "2026-08-24",
        "High",
        "Signature by 2026-09-22 because Samira Cole travels. Paul Singh "
        "named. Harborline paper.",
    ),
    Task(
        "Oakridge pilot forecast Fridays",
        "Paul Singh",
        "Onboarding 2.0",
        "In progress",
        "2026-09-10",
        "High",
        "Four consecutive weekly forecasts from Harborline OS, not the "
        "spreadsheet. This is the written success criterion. Miss two and "
        "the paid pilot fails.",
    ),
    Task(
        "BAA draft for healthcare deals",
        "Dana Kim",
        "SOC 2 Type II",
        "In progress",
        "2026-08-25",
        "High",
        "Meridian Health on Thursday, reusable for Redwood Clinics. Noah "
        "Berg has the vendor list for the subservice narrative.",
    ),
)


def reports_of(manager: str) -> tuple[Person, ...]:
    return tuple(p for p in PEOPLE if p.manager == manager)


def deals_for(person: str) -> tuple[Deal, ...]:
    return tuple(
        d
        for d in DEALS
        if person in (d.owner, d.se, d.csm)
    )


def projects_for(person: str) -> tuple[Project, ...]:
    return tuple(
        p for p in PROJECTS if person == p.owner or person in p.members
    )


def tasks_for(person: str) -> tuple[Task, ...]:
    return tuple(t for t in TASKS if t.assignee == person)


def tasks_in(project: str) -> tuple[Task, ...]:
    return tuple(t for t in TASKS if t.project == project)


def meetings_for(person: str) -> tuple[Meeting, ...]:
    return tuple(m for m in MEETINGS if person in m.attendees)


def meetings_for_deal(account: str) -> tuple[Meeting, ...]:
    return tuple(m for m in MEETINGS if m.related_deal == account)


def meetings_for_project(name: str) -> tuple[Meeting, ...]:
    return tuple(m for m in MEETINGS if m.related_project == name)


def money(arr: int) -> str:
    if arr >= 1_000_000:
        return f"${arr / 1_000_000:.1f}M"
    return f"${arr // 1_000}k"
