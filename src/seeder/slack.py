"""Slack fixtures for Harborline.

Creates public and private channels, then posts threads that name the same
people, deals, meetings, projects, and tasks the Drive and Notion corpora do.
The bot posts as itself — seeded employees are names in the text, not Slack
users — so a workspace that is not full of fake accounts still reads as one
company talking.

Channel names are prefixed (`hl-`) so they do not collide with a real #general.
Seeding is idempotent: channels are reused by name, messages by exact text.
`--reset` does not archive Slack channels; names stay occupied even when
archived, so reset would strand the seeder. Re-run is a no-op instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from connectors.slack.client import SlackError
from connectors.slack.models import Channel
from connectors.slack.writer import SlackWriter
from seeder.company import SLACK_PREFIX
from seeder.cross import titles_for_channel

log = logging.getLogger("seeder.slack")


@dataclass(frozen=True)
class Thread:
    parent: str
    replies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelSpec:
    slug: str
    private: bool
    topic: str
    purpose: str
    threads: tuple[Thread, ...]

    @property
    def name(self) -> str:
        return f"{SLACK_PREFIX}{self.slug}"


CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        "general",
        False,
        "Harborline company-wide. GTM operating system, not a forecast tool.",
        "All-hands follow-ups, hiring, and anything that is not a deal room.",
        (
            Thread(
                "Maya Chen recap from all-hands (2026-08-07): we sell Harborline OS as a GTM operating system, not a forecast tool. Brightpath Education is the customer story for the October board if Paul Singh's 2026-09-04 QBR is green. SOC 2 Type II is a company project with a date (2026-10-15), owned by Dana Kim.",
                (
                    "Elena Voss: open reqs are three AEs (Priya Raman), one Staff Engineer (Tess Nakamura / Chris Navarro), and Head of Data (Dana Kim). Noah Berg will slip Head of Data before he slips SOC 2 if Meridian Health and Cobalt Financial both move.",
                    "Alex Okonkwo: Nina Shah is pulling that narrative into the website relaunch (due 2026-09-12). No list prices on the site — Quinn Murphy's mid-market pricing refresh is still open.",
                    "Jordan Hale: enterprise coverage is 1.6x. That is the GTM problem of Q3, not a surprise for the board. Owen Frost and Cam Diaz's September webinars are the plan.",
                ),
            ),
            Thread(
                "Operating cadence reminder from Riley Park: Product x Sales weekly is where customer-facing dates get set. Deal rooms GA the week of 2026-08-25 is now a Meridian Health date, not an internal one. Salesforce two-way sync limited availability 2026-09-08 is a Cobalt Financial close condition.",
                (
                    "Chris Navarro froze unrelated platform sprint scope through 2026-09-08. Luis Ortega and Mei Huang are on both of those dates.",
                    "Priya Raman: Friday forecast inspect still happens. Quinn Murphy's number is the commit. Cascade Energy and Summit Legal stay out.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "gtm",
        False,
        "Jordan Hale's GTM org. Standup follow-ups land here, not in DMs.",
        "Sales, marketing, CS, partnerships. Deals have their own channels when they go private.",
        (
            Thread(
                "GTM standup 2026-08-11: Quinn Murphy pulled Cascade Energy and Summit Legal out of commit. No economic buyer, no commit. Meridian Health stays in; Maya Chen is in the Thursday exec alignment.",
                (
                    "Priya Raman: Marcus Webb sends Maya the Meridian briefing by Wednesday noon. Atlas Retail Group is not that briefing.",
                    "Jade Brooks: playbook v0.9 at next standup. Pinecone Analytics is the failure case in it — Lena Ortiz's win/loss is due 2026-08-21.",
                    "Sam Torres: named CSM on every proposal past that stage. Ivy Chen for enterprise, Paul Singh for mid-market. Vellum Media and Northwind Logistics already have Paul.",
                ),
            ),
            Thread(
                "GTM standup 2026-08-18: used playbook v0.9 on Cobalt Financial and Redwood Clinics. Cobalt is in legal review, blocked on a Type II date in writing. Dana Kim will put 2026-10-15 on a letter by 2026-08-20.",
                (
                    "Alex Okonkwo: coverage still 1.6x. September webinars with Northstar RevOps (Cam Diaz) are forward-looking, not current pipeline.",
                    "Cam Diaz: Northstar can sign without AppExchange. Fieldline Consulting cannot. Sasha Klein owes a dated AppExchange plan even if the listing is October.",
                    "Redwood Clinics technical validation stays 2026-08-27. Harper Lin, Tess Nakamura, Mei Huang, Ivy Chen in the room.",
                ),
            ),
            Thread(
                "Q3 forecast review 2026-08-14 with Maya Chen, Jordan Hale, Priya Raman, Quinn Murphy, Noah Berg. Commit stays $4.1M. Best case $5.0M if Meridian Health and Cobalt Financial both sign. If both slip, Head of Data slips, SOC 2 does not.",
                (
                    "Quinn Murphy republishing the commit board with stage-exit criteria visible to AEs. Forecast accuracy initiative is the project, not a vibe.",
                    "Jordan Hale: I will not overlay a second number on Quinn's. AEs proposing, Priya inspecting, Quinn committing.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "sales",
        False,
        "AE / SDR / SE working channel. Private deal rooms are #hl-meridian and #hl-cobalt.",
        "Marcus, Lena, Theo, Aisha, Harper, Jade, Quinn.",
        (
            Thread(
                "Priya Raman / Jordan Hale 1:1 outcome (2026-08-17): Marcus Webb's next two weeks are Meridian Health, Cobalt Financial, and Redwood Clinics only. Atlas Retail Group discovery follow-ups wait. This is staffing, not a Marcus performance note — Elena Voss is hiring three AEs because of it.",
                (
                    "Marcus Webb: understood. Harper Lin and I will still do the Atlas one-pager ask to Sasha Klein (four Salesforce orgs) so it does not die, but I am not in another Atlas workshop this fortnight.",
                    "Ben Choi: I sourced Atlas. I can keep Dana Whitfield warm. Tell me if that is actually useful or just noise.",
                    "Harper Lin: I am in Meridian Thursday, Cobalt Thursday, Helios Friday, Vellum Monday, Redwood Wednesday. Deal room templates shipping this week is the only way that is not a Drive-folder fire drill.",
                ),
            ),
            Thread(
                "Lena Ortiz: Northwind Logistics proposal review 2026-08-19. Chris Lang asked for monthly billing. I held annual, per Noah Berg and the 2026-08-08 pricing committee. New mid-market band, Paul Singh named as CSM, Onboarding 2.0 appendix.",
                (
                    "Quinn Murphy: correct. Brightpath Education is grandfathered. Northwind is not a second Brightpath.",
                    "Paul Singh: 14-day onboarding plan attached. If they close I am on the hook for forecast Fridays, same SLA as Oakridge Industrial's pilot.",
                    "Theo Grant: using the same appendix on anything I take to proposal. Helios Manufacturing is Q4, not a free pilot.",
                ),
            ),
            Thread(
                "Theo Grant: Summit Legal qualification this week against Jade Brooks's mid-market MEDDPICC one-pager. Elena Brooks is RevOps, not a buyer. No Salesforce, no forecast cadence. If both metrics and economic buyer are still missing after this call I am marking disqualified, not nurtured.",
                (
                    "Jade Brooks: please do. Pinecone Analytics lingered and then lost to doing nothing. The playbook needs a live example of a clean disqualify, not another nurture.",
                    "Ben Choi: I still have a thread into their managing partner. Say the word and I drop it.",
                    "Aisha Patel: drop it if Theo disqualifies. Do not run a second motion around an AE no.",
                ),
            ),
            Thread(
                "Sofia Reyes: Cascade Energy nurture is alive on my side. Omar Haddad still cannot produce a CFO meeting. Lena's recycle date is 2026-09-05. Not in commit either way.",
                (
                    "Lena Ortiz: confirmed. Champion identified is not a stage Quinn will put in the number. I will ask Omar once more and then recycle.",
                    "Owen Frost: this came from the June webinar. I would like the win/loss on why inbound like this stalls, even if the deal is small.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "deals",
        False,
        "Public deal chatter. Anything counsel or a CISO would not want in here goes to #hl-legal, #hl-meridian, or #hl-cobalt.",
        "Stage changes, next steps, who is in the room. Not redlines.",
        (
            Thread(
                "Meridian Health ($420k, Negotiation, close 2026-09-12). Exec alignment 2026-08-21: Maya Chen, Jordan Hale, Marcus Webb, Harper Lin, Ivy Chen. Champion is Dr. Alicia Rowe. They are bringing CFO and CISO. Harper has two security-questionnaire items still on Dana Kim.",
                (
                    "Ivy Chen: I am the named CSM on the paper. Onboarding 2.0, not the Brightpath Zoom tour. Task is on me for the order-form language by 2026-08-25.",
                    "Drew Patel: deal rooms GA week of 2026-08-25 is the answer if they ask about a 15% milestone holdback. Maya's line is no holdback if we hit that date.",
                    "Dana Kim: BAA draft by 2026-08-25, reusable for Redwood Clinics. Type II letter (2026-10-15) goes out 2026-08-20 so we are not inventing a date in the room.",
                ),
            ),
            Thread(
                "Vellum Media ($180k, Mutual close plan, close 2026-09-25). Lena Ortiz, Harper Lin, Paul Singh with Samira Cole (CRO) on 2026-08-24. Harborline paper. She travels the last week of September so signature has to happen by 2026-09-22.",
                (
                    "Lena Ortiz: this is mid-market money run with enterprise hygiene. I am not moving it to Marcus's book.",
                    "Paul Singh: named on the close plan. Same onboarding path as Northwind.",
                    "Quinn Murphy: in commit. Has a buyer, a date, and paper. Unlike Cascade.",
                ),
            ),
            Thread(
                "Oakridge Industrial ($125k, Pilot in progress). Written success criteria: four consecutive weekly forecasts submitted from Harborline OS, not the spreadsheet. Pilot readout 2026-09-10. Paul Singh owns the Friday cadence more than Theo Grant does.",
                (
                    "Theo Grant: this is the pilot shape Noah Berg wants every time. Paid, time-boxed, written, named CSM. Helios does not get a free version of this.",
                    "Paul Singh: if they miss two Fridays I escalate to Sam Torres before the readout, not at it.",
                ),
            ),
            Thread(
                "Helios Manufacturing demo recap 2026-08-22. Ruth Keller brought two SEs. They cared about deal rooms for plant managers who are not in Salesforce, not about forecast. Q4 proposal, not Q3 commit.",
                (
                    "Harper Lin: do not revert the manufacturing demo org. I will keep it as a task on Deal room templates.",
                    "Theo Grant: recap and a Q4 skeleton going out. No unpaid pilot.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "product",
        False,
        "Riley Park. Core Platform is Drew Patel. Integrations is Sasha Klein.",
        "Product x Sales weekly notes and customer-facing dates.",
        (
            Thread(
                "Product x Sales 2026-08-12: deal rooms GA week of 2026-08-25. Harper Lin said that date is now customer-facing on Meridian Health. Sasha Klein flagged that Cobalt Financial's sync close condition and deal rooms land on Luis Ortega and Mei Huang.",
                (
                    "Chris Navarro: freeze is in place through 2026-09-08. Do not bring a third committed item to platform this sprint.",
                    "Mei Huang: clone path demoable 2026-08-22, feature-flagged for Harper. Meridian and Cobalt are the first two clones.",
                    "Drew Patel: I will be in the Meridian room only if they ask about the object model. Otherwise Harper runs it.",
                ),
            ),
            Thread(
                "Product x Sales 2026-08-19: Nina Shah joined. The website still says 'forecast tool' while every live deal is sold as a GTM operating system. Relaunch ships ICP and philosophy, not list prices — Quinn's band is still moving.",
                (
                    "Nina Shah: ICP page draft to Riley Park and Priya Raman by 2026-08-24. VP Sales and RevOps at 200–2,000 person B2B. Summit Legal is the not-ICP example.",
                    "Sasha Klein: field mapping with Quinn Murphy due 2026-08-26. We are not inventing a parallel forecast field in Salesforce.",
                    "Harper Lin: I will screenshot the cloneable deal room for Jade Brooks's playbook once Mei flips the flag.",
                ),
            ),
            Thread(
                "Atlas Retail Group has four Salesforce orgs. Dana Whitfield walked them in discovery 2026-08-20. Sasha Klein: one-pager on multi-org by 2026-08-27. This is Q4 scope, not a Q3 build, and not a reason to slip Cobalt's 2026-09-08 limited availability.",
                (
                    "Marcus Webb: I need that one-pager so I have something honest to send. I am not scoping it myself.",
                    "Riley Park: correct. Do not let Atlas become a second sync project. Luis Ortega is already the named architect on Cobalt Financial.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "engineering",
        False,
        "Chris Navarro / Tess Nakamura. Sprint freeze through 2026-09-08 is real.",
        "Deal rooms, Salesforce sync, audit log. SOC 2 evidence talk stays concrete.",
        (
            Thread(
                "Sprint review 2026-08-14: committed items are deal-room clone path (Mei Huang) and audit log (Tess Nakamura / Mei Huang). Luis Ortega has a Salesforce sandbox, not yet production-shaped. Freeze through 2026-09-08 after Product x Sales.",
                (
                    "Luis Ortega: production-shaped sandbox for Cobalt by 2026-08-28, connected by 2026-09-01. Rollback note with Sasha Klein by 2026-09-05 because counsel will ask.",
                    "Tess Nakamura: audit-log evidence folder for the auditor plus a customer-facing note I can hand Redwood Clinics on 2026-08-27. p95 on deal rooms still has to be under 400ms.",
                    "Chris Navarro: Staff Engineer spec with Elena Voss this week. That hire is how sync and SOC 2 stop sharing Luis.",
                ),
            ),
            Thread(
                "SOC 2 kickoff 2026-08-05 recap for anyone who missed it. Evidence window is open. Customer-facing date Dana Kim will put in letters is 2026-10-15. Type I is already in the data room. Engineering blockers: audit logging and access reviews.",
                (
                    "Luis Ortega: access-review screenshots from last quarter, due 2026-09-04. This blocks the bundle more than remaining code.",
                    "Noah Berg: vendor list for the subservice narrative is on me. SOC 2 does not slip if a deal does. I already said that in forecast review.",
                    "Dana Kim: I am the name on every customer letter. Do not have AEs freelance a date in #hl-deals.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "cs",
        False,
        "Sam Torres. Ivy Chen (enterprise) / Paul Singh (mid-market).",
        "Onboarding 2.0, QBRs, named CSM on paper.",
        (
            Thread(
                "Onboarding 2.0: fourteen days to first forecast. Brightpath Education is still on the old Zoom-tour path — that is why Paul Singh's 2026-09-04 QBR has to be steered to outcomes, not implementation. The next logo (Meridian Health, Cobalt Financial, or Vellum Media) lands on 2.0 or this project is theater.",
                (
                    "Ivy Chen: runbook due 2026-09-01. Jade Brooks will train to it. Named-CSM language for Meridian's order form due 2026-08-25.",
                    "Paul Singh: Brightpath QBR brief going to Morgan Ellison. Nina Shah's three quote prompts attached so we leave with a case-study sentence Maya can use.",
                    "Sam Torres: my task is the rule — named CSM on every proposal past that stage. Already true on Vellum, Northwind, Meridian, Cobalt.",
                ),
            ),
            Thread(
                "Brightpath Education QBR planning 2026-08-15. Agenda: outcomes first, implementation residue second, second-campus expansion third. Theo Grant joins for expansion. Nina Shah needs a quote in the room, not as a follow-up.",
                (
                    "Nina Shah: if the quote is good it goes in the Series B board pack. If the QBR is about onboarding pain, Maya does not have a customer narrative.",
                    "Theo Grant: second campus VP was not in the original evaluation. I will not forecast expansion in Q3.",
                ),
            ),
            Thread(
                "Oakridge + Northwind + Vellum are all Paul Singh. Oakridge pilot fails if they miss two forecast Fridays. Please do not add Helios Manufacturing to this list as a free implementation.",
                (
                    "Theo Grant: Helios is a Q4 proposal. Not asking CS to staff a ghost pilot.",
                    "Ivy Chen: once Meridian or Cobalt signs I am on first-thirty-days with Sasha Klein in the thread if sync is on the paper.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "marketing",
        False,
        "Alex Okonkwo. Website relaunch 2026-09-12. Coverage gap is 1.6x enterprise.",
        "Nina Shah content, Owen Frost demand gen, Cam Diaz when partners touch pipeline.",
        (
            Thread(
                "Website relaunch. Current site still describes Harborline as a forecast tool. Relaunch copy is Maya's all-hands line: GTM operating system. ICP page, no list prices, Brightpath story if the QBR holds. Due 2026-09-12 — Alex already told the board this date.",
                (
                    "Nina Shah: ICP draft 2026-08-24. Case study outline 2026-08-26, quote after 09-04. Riley and Priya review ICP.",
                    "Owen Frost: two September webinars with Northstar RevOps, aimed at enterprise coverage, not more mid-market inbound Sofia is already covering.",
                    "Riley Park: please do not ship a pricing page. Quinn's mid-market band is in force internally and not ready to be a public number.",
                ),
            ),
            Thread(
                "Competitive landscape doc is up for AEs. The real competitor is the status quo. Pinecone Analytics lost to doing nothing. When they say they will just do this in Salesforce, the answer is two-way sync, not rip-and-replace.",
                (
                    "Jade Brooks: that sentence is going in the playbook. Thank you for not writing a 40-row feature matrix.",
                    "Harper Lin: Meridian has Gong. We do not ask them to rip it out. I put that in the security pack so nobody invents a Gong replacement on Thursday.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "enablement",
        False,
        "Jade Brooks. Q3 Enterprise Playbook v1 due 2026-08-29.",
        "MEDDPICC as we run it, win/loss, AE runbooks.",
        (
            Thread(
                "Playbook kickoff 2026-08-06 recap. Meridian Health is the success case (metrics, economic buyer, earned exec sponsor). Pinecone Analytics is the failure case (no compelling event, champion left, lingered). Theo Grant asked for a mid-market appendix so this does not read as enterprise-only.",
                (
                    "Lena Ortiz: Pinecone win/loss first draft was due 2026-08-15, polishing with Jade for 2026-08-21. Failure mode is urgency, not price.",
                    "Theo Grant: Brightpath close notes sent. Mid-market runbook should say: new band, annual, named CSM, Harper only when the deal earns an SE.",
                    "Marcus Webb: enterprise runbook should say Maya Chen is an exec sponsor only when Priya says the deal earned it. Atlas has not. Meridian has.",
                ),
            ),
            Thread(
                "MEDDPICC as Harborline runs it is stricter than the textbook because last quarter's 18% miss was champion-identified deals in forecast. Directors of Sales Ops are champions, not buyers. Omar Haddad (Cascade Energy) and Elena Brooks (Summit Legal) are the live examples.",
                (
                    "Quinn Murphy: stage-exit criteria doc matches this. Proposal / mutual close / negotiation / legal / paid pilot may sit in commit. Champion identified may not.",
                    "Priya Raman: Friday inspect will use this list. If an AE argues a champion-identified deal into the number I will send them here.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "wins",
        False,
        "Closed-won only. Brightpath Education is the one in this corpus so far.",
        "Do not put live deals in here because you feel good about them.",
        (
            Thread(
                "Brightpath Education. $72k ARR, Theo Grant, closed 2026-07-22. Champion Morgan Ellison (VP Enrollment). First mid-market close of Q3. Grandfathered on the 2025 price band — not a precedent. Paul Singh is CSM. Nina Shah is writing the case study off the 2026-09-04 QBR.",
                (
                    "Theo Grant: they replaced a spreadsheet forecast and a neglected HubSpot pipeline. Expansion into the second campus is real and not Q3 commit.",
                    "Maya Chen: this is the narrative I want in the October pack if the QBR is about outcomes. If it is about onboarding, we do not have a story yet.",
                    "Jade Brooks: worked example in the mid-market appendix. Copy the close. Do not copy the onboarding path.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "forecast",
        False,
        "Quinn Murphy's commit board. Jordan Hale treats this number as the number.",
        "Stage-exit arguments happen here. AE rollups that disagree come here, not to Maya.",
        (
            Thread(
                "Commit snapshot 2026-08-17. Target $4.1M. In or next to commit: Meridian Health $420k Negotiation, Cobalt Financial $540k Legal review, Northwind Logistics $96k Proposal, Brightpath Education $72k Closed won, Vellum Media $180k Mutual close plan, Oakridge Industrial $125k paid pilot.",
                (
                    "Upside / Q4 / lost: Atlas Retail Group $310k Discovery (Q4), Helios Manufacturing $110k Demo (Q4), Redwood Clinics $275k Tech validation (coverage, not the $4.1M), Cascade Energy $85k Champion identified (out), Summit Legal $64k Qualification (out), Pinecone Analytics $48k Closed lost.",
                    "Marcus Webb concentration: four live enterprise deals. Next two weeks = Meridian, Cobalt, Redwood. That is already in #hl-sales.",
                    "If you think your deal is in the wrong bucket, reply with the economic buyer and the dated next step, not a feeling.",
                ),
            ),
            Thread(
                "Stage-exit reminder. Discovery without a buyer meeting recycles. Champion identified without a dated CFO/CRO step recycles (Cascade: 2026-09-05). Demo is a date, not a number (Helios). Proposal requires the new mid-market band or enterprise discounting rules, a named CSM, annual billing.",
                (
                    "Lena Ortiz: Northwind is on the new band, annual, Paul named. Vellum has a buyer and a signature date. Cascade is not in this thread as a number.",
                    "Theo Grant: Oakridge is a paid pilot with written criteria so it may sit in commit. Helios may not.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "partners",
        False,
        "Cam Diaz. First wave: Northstar RevOps, Fieldline Consulting, AppExchange plan.",
        "Not a reseller motion. Not a second AE team.",
        (
            Thread(
                "Partner working session 2026-08-16. Northstar RevOps is ready to sign without AppExchange — paper this week. Fieldline Consulting wants the listing as a condition, which we cannot honestly promise before Salesforce two-way sync limited availability (2026-09-08).",
                (
                    "Sasha Klein: dated AppExchange plan by 2026-09-12 even if the listing is October. That unblocks Fieldline conversations without lying about Q3.",
                    "Owen Frost: September webinars proceed with Northstar. Aimed at the 1.6x enterprise coverage gap Alex keeps naming.",
                    "Alex Okonkwo: partners may say ICP, MEDDPICC, deal rooms, forecast as system of record. They may not quote the mid-market band from memory and they may not promise Type II before 2026-10-15.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "allhands",
        False,
        "All-hands artifacts. August 2026-08-07.",
        "Clips and follow-ups. Policy lives in the Company docs, not only here.",
        (
            Thread(
                "August all-hands follow-ups. Company narrative: GTM operating system. Customer we will talk about: Brightpath Education, contingent. GTM problem: 1.6x enterprise coverage. Trust problem: SOC 2 Type II with a date. Hiring: three AEs, Staff Engineer, Head of Data (slips first).",
                (
                    "Alex Okonkwo: pulling a clip for relaunch messaging, task due 2026-08-28.",
                    "Elena Voss: Staff Engineer spec with Chris Navarro due 2026-08-21.",
                    "Dana Kim: please stop calling SOC 2 an engineering side quest in other channels. It is a company project. Noah already protected the date against deal slip.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "exec",
        True,
        "Maya, Jordan, Riley, Dana, Elena, Noah. Not a deal room.",
        "Private. Board-level tradeoffs. Do not paste this into #hl-general.",
        (
            Thread(
                "Thursday is two customer rooms on the same day. Meridian Health exec alignment (Maya, Jordan, Marcus, Harper, Ivy) and Cobalt Financial legal/architecture (Marcus, Harper, Luis, Sasha, Dana). Atlas Retail Group does not get a third room this week.",
                (
                    "Maya Chen: I am in Meridian because Priya says it earned an exec sponsor. I am not in Atlas. If the CFO asks for a 15% holdback tied to deal rooms, the answer is no holdback if Drew hits the 08-25 GA.",
                    "Jordan Hale: Marcus's book is the concentration risk. Three AE reqs are the fix. This fortnight is the patch.",
                    "Noah Berg: if both Meridian and Cobalt slip, Head of Data slips, SOC 2 does not, and I will not approve a Meridian holdback that becomes a Northwind monthly that becomes a second grandfathered SKU.",
                ),
            ),
            Thread(
                "October board (2026-10-02). Pack locked 2026-09-25, dry run 2026-09-28. Brightpath narrative (Nina Shah, Paul Singh QBR 09-04). ARR bridge (Noah, v1 08-22). Coverage slide that names 1.6x instead of hiding it (Jordan). Roadmap dates that match sales rooms (Riley).",
                (
                    "Riley Park: deal rooms 08-25 and sync 09-08 are in customer rooms. The board pack does not get to say different dates.",
                    "Elena Voss: I will not open Head of Data as a board promise this month.",
                    "Dana Kim: Type II date in the pack is 2026-10-15, same letter Cobalt is getting.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "legal",
        True,
        "Paper, DPA, BAA, Type II letters. Dana Kim + Noah Berg + the AE who owns the account.",
        "Private. No screenshots into public deal chatter.",
        (
            Thread(
                "Cobalt Financial DPA is in redline. Type I is in the data room. Dana Kim's Type II letter (2026-10-15) goes out 2026-08-20 so counsel is not hearing a date for the first time in the architecture review tomorrow. Sync limited availability 2026-09-08 stays a close condition.",
                (
                    "Marcus Webb: I will not let paper drift off Harborline paper. James Okada's counsel is slow; that is not a reason to switch templates.",
                    "Luis Ortega: I am in the architecture review. Rollback note is a task for 2026-09-05. I will not promise four-org sync because Atlas came up in another channel.",
                    "Sasha Klein: field mapping with Quinn Murphy 2026-08-26. Sacred Salesforce fields stay sacred.",
                ),
            ),
            Thread(
                "Healthcare BAA. Meridian Health needs it Thursday. Redwood Clinics will reuse it. Dana Kim draft due 2026-08-25. Named CSM (Ivy Chen) on the Meridian form is a commercial fact that belongs on the order form, not only in Slack.",
                (
                    "Ivy Chen: order-form language is my task, 2026-08-25. Copying it to Cobalt.",
                    "Harper Lin: remaining Meridian questionnaire items tomorrow. After that I clone the pack for Redwood before 08-27.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "meridian",
        True,
        "Meridian Health deal room. $420k, Negotiation, close 2026-09-12. AE Marcus Webb.",
        "Private. Champion Dr. Alicia Rowe, VP Revenue Cycle. CISO and CFO in Thursday's room.",
        (
            Thread(
                "Close plan after Thursday's exec alignment. Named CSM: Ivy Chen. BAA: Dana Kim by 2026-08-25. Security questionnaire: Harper Lin remaining answers 2026-08-22. Deal rooms: Drew Patel / Mei Huang GA week of 2026-08-25, no 15% holdback if we hit it. Salesforce stays CRM of record; Sasha Klein's sync is how. Gong stays; we do not replace it.",
                (
                    "Marcus Webb: I send the close-plan email the same day as the meeting. Maya is in the room. Jordan is in the room. Do not add Riley unless they ask about the object model — then Drew joins.",
                    "Harper Lin: two questionnaire items are on Dana. After they land I will clone this room from the product template, not from a Drive folder, if Mei's flag is on.",
                    "Ivy Chen: Onboarding 2.0 path attached so they do not inherit Brightpath's Zoom tour. First thirty days I want Sasha in the thread if sync is on the paper.",
                    "Quinn Murphy: this deal stays in commit. Buyer in the room, date, paper. That is the playbook working.",
                ),
            ),
            Thread(
                "They will ask whether deal rooms are live before signature. The honest answer is GA week of 2026-08-25. If Mei slips, we still close on a Drive room rather than move the date — Marcus and Harper already agreed that in their 2026-08-18 1:1. Prefer the product. Do not slip paper for it.",
                (
                    "Mei Huang: flag-flipped clone path 2026-08-22 is still the plan. I will post in #hl-engineering if that moves.",
                    "Dana Kim: I will not be in this channel for product dates. I am here for BAA and the Type II letter.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "cobalt",
        True,
        "Cobalt Financial deal room. $540k, Legal review, close 2026-09-30. AE Marcus Webb.",
        "Private. Champion James Okada, CRO. Close condition: Salesforce two-way sync limited availability 2026-09-08.",
        (
            Thread(
                "Architecture review 2026-08-21. Luis Ortega walks sync. Sasha Klein on mapping. Dana Kim on Type II date (2026-10-15, in writing). Counsel is slow; paper stays Harborline paper. This is the largest live deal and the one that funds Q3 if Meridian slips.",
                (
                    "Luis Ortega: sandbox connected 2026-09-01. I am the named architect. Atlas's four orgs are not this review.",
                    "Sasha Klein: limited availability for Cobalt (and Meridian) by 2026-09-08 is a close condition, not a nice-to-have. Chris's freeze exists so that remains true.",
                    "Marcus Webb: I push paper contingent on that date. Ivy Chen named as CSM. If sync slips we do not quietly keep the close date and hope.",
                    "Jordan Hale: if this is the deal that makes the enterprise motion real, it is also the deal we do not lie to. Date moves, commit moves.",
                ),
            ),
            Thread(
                "RevOps at Cobalt currently forecasts in three regional spreadsheets. Harborline OS becomes the operating system for a 70-person revenue team. That is the thesis. Do not shrink it to 'a Salesforce overlay' in the room tomorrow.",
                (
                    "Harper Lin: security pack has Type I in it today. Type II letter lands 08-20. I am not putting Atlas multi-org language in this pack.",
                    "Ivy Chen: first thirty days, Onboarding 2.0, Sasha in the week-one thread because sync is on the paper.",
                ),
            ),
        ),
    ),
    ChannelSpec(
        "board",
        True,
        "October board pack. Owner Noah Berg. Dry run 2026-09-28. Board 2026-10-02.",
        "Private. Customer names in the pack are only the ones Maya has approved to tell.",
        (
            Thread(
                "Pack structure. ARR bridge (Noah, v1 2026-08-22) — new logos, expansion, churn, Meridian and Cobalt called out as not booked. GTM efficiency and 1.6x coverage (Jordan, slide due 2026-09-10). Roadmap dates matching sales rooms (Riley). Brightpath narrative (Nina, quote after 09-04 QBR). Hiring against plan (Elena) with Head of Data not promised.",
                (
                    "Nina Shah: outline 2026-08-26. If Paul Singh's QBR does not produce a quote, we do not invent one. Maya would rather have no customer story than a soft one.",
                    "Jordan Hale: I would rather put 1.6x on a slide than have a Northpeak partner find it. Owen's webinars are the forward-looking answer, not current pipeline — the slide will say that.",
                    "Riley Park: I will not let this pack show different dates than Harper is saying to Meridian and Cobalt.",
                    "Maya Chen: Brightpath is the story we tell if it is true. Enterprise motion is the story we tell if Meridian or Cobalt actually signs. Until then those are pipeline.",
                ),
            ),
        ),
    ),
)


async def _ensure_channel(
    writer: SlackWriter, spec: ChannelSpec, existing: dict[str, Channel]
) -> Channel | None:
    channel = existing.get(spec.name)
    if channel is None:
        try:
            channel = await writer.create_channel(spec.name, private=spec.private)
            existing[spec.name] = channel
            log.info("channel #%s created (%s)", spec.name, "private" if spec.private else "public")
        except SlackError as exc:
            if exc.error == "name_taken":
                listed = {c.name: c for c in await writer.list_channels()}
                channel = listed.get(spec.name)
                if channel is None:
                    log.warning("channel #%s name_taken but not visible to the bot", spec.name)
                    return None
                existing[spec.name] = channel
            elif exc.error == "missing_scope":
                log.warning(
                    "channel #%s: missing_scope (needed=%s, provided=%s). "
                    "Add channels:manage (public), groups:write (private), "
                    "chat:write, channels:join; reinstall the app; re-run "
                    "`python -m seeder --source slack`",
                    spec.name,
                    exc.response.get("needed"),
                    exc.response.get("provided"),
                )
                return None
            else:
                log.warning("channel #%s: %s", spec.name, exc.error)
                return None
    else:
        log.info("channel #%s exists", spec.name)
        if not spec.private:
            try:
                await writer.join(channel.id)
            except SlackError as exc:
                log.warning("join #%s: %s", spec.name, exc.error)

    try:
        await writer.set_topic(channel.id, spec.topic)
        await writer.set_purpose(channel.id, spec.purpose)
    except SlackError as exc:
        log.warning("topic/purpose #%s: %s", spec.name, exc.error)
    return channel


async def _seed_channel(writer: SlackWriter, spec: ChannelSpec, channel: Channel) -> int:
    posted = 0
    parents = {m.text: m.ts for m in await writer.history(channel.id)}
    for thread in spec.threads:
        ts = parents.get(thread.parent)
        if ts is None:
            ts = await writer.post(channel.id, thread.parent)
            parents[thread.parent] = ts
            posted += 1
        existing_replies = {m.text for m in await writer.replies(channel.id, ts)}
        for reply in thread.replies:
            if reply in existing_replies:
                continue
            await writer.post(channel.id, reply, thread_ts=ts)
            existing_replies.add(reply)
            posted += 1
    log.info("#%s: %d messages posted (others already present)", spec.name, posted)
    return posted


def _cross_thread(
    slug: str, drive_urls: dict[str, str], notion_urls: dict[str, str]
) -> Thread | None:
    replies: list[str] = []
    for title in titles_for_channel(slug):
        drive = drive_urls.get(title)
        notion = notion_urls.get(title)
        if not drive and not notion:
            continue
        lines = [title]
        if drive:
            lines.append(drive)
        if notion:
            lines.append(notion)
        replies.append("\n".join(lines))
    if not replies:
        return None
    return Thread(
        "Canonical Drive and Notion docs for this channel:",
        tuple(replies),
    )


async def seed(
    writer: SlackWriter,
    *,
    reset: bool = False,
    drive_urls: dict[str, str] | None = None,
    notion_urls: dict[str, str] | None = None,
) -> int:
    """Create channels and threads. Returns messages posted this run."""
    if reset:
        log.info(
            "slack: --reset does not archive channels (archived names stay occupied); "
            "re-run is idempotent by message text"
        )

    auth = await writer.auth_test()
    log.info("slack: seeding as %s in %s", auth.get("user"), auth.get("team"))

    existing = {c.name: c for c in await writer.list_channels()}
    created_or_found: list[tuple[ChannelSpec, Channel]] = []
    for spec in CHANNELS:
        channel = await _ensure_channel(writer, spec, existing)
        if channel is not None:
            created_or_found.append((spec, channel))

    drive_urls = drive_urls or {}
    notion_urls = notion_urls or {}
    specs: list[tuple[ChannelSpec, Channel]] = []
    for spec, channel in created_or_found:
        extra = _cross_thread(spec.slug, drive_urls, notion_urls)
        if extra:
            spec = ChannelSpec(
                spec.slug,
                spec.private,
                spec.topic,
                spec.purpose,
                spec.threads + (extra,),
            )
        specs.append((spec, channel))

    posted = 0
    sem = asyncio.Semaphore(4)

    async def _one(spec: ChannelSpec, channel: Channel) -> int:
        async with sem:
            return await _seed_channel(writer, spec, channel)

    results = await asyncio.gather(
        *(_one(spec, channel) for spec, channel in specs)
    )
    posted = sum(results)

    if not created_or_found:
        log.warning(
            "slack: no channels seeded. The bot cannot create channels until "
            "channels:manage and groups:write are granted and the app is "
            "reinstalled. Then: docker compose --profile seed run --rm seeder "
            "python -m seeder --source slack"
        )
    log.info(
        "slack: %d channels, %d messages posted this run",
        len(created_or_found),
        posted,
    )
    return posted
