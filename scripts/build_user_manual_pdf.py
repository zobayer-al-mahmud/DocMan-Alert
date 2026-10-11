"""Build the DocMan Alert User Manual PDF.

    pip install reportlab pillow
    python scripts/build_user_manual_pdf.py

Writes docs/Ananta_DocMan_Alert_User_Manual.pdf. The screenshots in docs/img/ were
captured from the application running on synthetic demo data; replace them if the
screens change. Shared drawing helpers live in scripts/_pdf_kit.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.lib.colors import HexColor, white
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, NextPageTemplate, PageBreak, Paragraph, Spacer, Table, TableStyle

import _pdf_kit as K
from _pdf_kit import (
    CHIP, GOLD, INK, LINE, MUTED, NAVY, TEAL, CanvasFigure, Chip, Screenshot, bullets, callout,
    data_table, draw_arrow, draw_box, figure, hrule, make_toc, new_document, steps,
)

OUT = os.path.join(K.ROOT, "docs", "Ananta_DocMan_Alert_User_Manual.pdf")
IMG = os.path.join(K.ROOT, "docs", "img")
EDITION = "11 October 2026"
S = K.build_styles(body_size=9.8)
W = 178 * mm
LIGHT = HexColor("#EAF3F2")
PALE = HexColor("#F6F1E4")


def P(text, style="Body"):
    return Paragraph(text, S[style])


def H1(text):
    return [P(text, "H1"), hrule()]


def H2(text):
    return P(text, "H2")


def table(headers, rows, widths, **kw):
    return data_table(S, headers, rows, widths, **kw)


def how(items, width=W):
    return steps(S, items, width)


def shot(name, markers=None, crop=None, width=W, max_h=None):
    return Screenshot(os.path.join(IMG, name + ".png"), width, crop=crop, markers=markers, max_height=max_h)


def fig(name, caption, markers=None, crop=None, legend=None, width=W, max_h=112 * mm):
    return figure(S, shot(name, markers, crop, width, max_h), caption, legend, width=width)


def note(kind, text, title=None):
    return callout(S, kind, text, title=title, width=W)


def code(text):
    return "<font name='%s'>%s</font>" % (K.MONO, text)


# ================================================================= drawings
def draw_three_steps(c, w, h):
    items = [
        ("1  Record it", "Add the document, license or checkup with its expiry date and file."),
        ("2  The system watches", "Every day it works out how many days are left and colours the record."),
        ("3  The right people get an email", "A weekly reminder at 90, 60 and 30 days. An urgent one at 7 days and when expired."),
    ]
    gap = 9 * mm
    bw = (w - 2 * gap) / 3
    for i, (t, b) in enumerate(items):
        x = i * (bw + gap)
        draw_box(c, x, 1 * mm, bw, h - 2 * mm, t, b, fill=LIGHT if i != 1 else PALE,
                 stroke=TEAL if i != 1 else GOLD, tsize=9.2, bsize=7.8, body_lines=4)
        if i < 2:
            draw_arrow(c, x + bw, h / 2, x + bw + gap, h / 2)


def draw_timeline(c, w, h):
    segs = [
        ("Valid", "More than 90 days", "No email"),
        ("Expiring in 90 Days", "61 to 90 days", "Weekly email"),
        ("Expiring in 60 Days", "31 to 60 days", "Weekly email"),
        ("Expiring in 30 Days", "8 to 30 days", "Weekly email"),
        ("Expiring in 7 Days", "1 to 7 days", "Urgent email"),
        ("Expired", "0 days or less", "Urgent email"),
    ]
    n = len(segs)
    bw = w / n
    y = 20 * mm
    bh = 11 * mm
    for i, (status, rng, mail) in enumerate(segs):
        bg, fg = CHIP[status]
        c.setFillColor(HexColor(bg))
        c.setStrokeColor(white)
        c.setLineWidth(2)
        c.rect(i * bw, y, bw, bh, fill=1, stroke=1)
        c.setFillColor(HexColor(fg))
        c.setFont(K.SANS_B, 7.4)
        label = status.replace("Expiring in ", "")
        c.drawCentredString(i * bw + bw / 2, y + bh / 2 - 2.5, label)
        c.setFillColor(MUTED)
        c.setFont(K.SANS, 7.2)
        c.drawCentredString(i * bw + bw / 2, y - 4.2 * mm, rng)
        c.setFillColor(INK if i else MUTED)
        c.setFont(K.SANS_B, 7.4)
        c.drawCentredString(i * bw + bw / 2, y + bh + 3 * mm, mail)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.line(0, 3 * mm, w, 3 * mm)
    c.line(w, 3 * mm, w - 2.2 * mm, 4.7 * mm)
    c.line(w, 3 * mm, w - 2.2 * mm, 1.3 * mm)
    c.setFillColor(MUTED)
    c.setFont(K.SANS_I, 7.4)
    c.drawString(0, 7.5 * mm, "Time passes")
    c.drawRightString(w - 3 * mm, 7.5 * mm, "Expiry date")


def draw_email(c, w, h):
    c.setFillColor(white)
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.roundRect(0, 0, w, h, 2 * mm, fill=1, stroke=1)
    c.setFillColor(HexColor("#F0F3F7"))
    c.rect(0.5, h - 21 * mm, w - 1, 20.5 * mm, fill=1, stroke=0)
    rows = [("From", "document_tracker@ananta.com.bd"), ("To", "Farhana Akter"),
            ("Subject", "URGENT: Fire License 2026 Expires in 4 Days")]
    y = h - 6 * mm
    for label, val in rows:
        c.setFillColor(MUTED)
        c.setFont(K.SANS, 8)
        c.drawString(4 * mm, y, label)
        c.setFillColor(INK)
        c.setFont(K.SANS_B if label == "Subject" else K.SANS, 8.4)
        c.drawString(22 * mm, y, val)
        y -= 5.6 * mm
    y = h - 28 * mm
    c.setFillColor(INK)
    c.setFont(K.SANS, 8.4)
    c.drawString(4 * mm, y, "Dear Recipient,")
    y -= 5 * mm
    c.drawString(4 * mm, y, "Scheduled urgent alert for this document. It goes to the assigned person, or to the")
    y -= 4.2 * mm
    c.drawString(4 * mm, y, "document's department, or to the company when that department has no recipient.")
    y -= 6.2 * mm
    fields = [
        ("Document", "Fire License 2026"), ("Document Type", "Fire License"),
        ("Certificate / License No.", "FS/DHK/2026/1184"), ("Company", "Ananta Companies"),
        ("Factory", "Ananta Garments Ltd."), ("Department", "Compliance"),
        ("Issue Date", "11-Oct-2025"), ("Expiry Date", "15-Oct-2026"),
        ("Remaining", "4 Days"), ("Alert Status", "Expiring in 7 Days"),
    ]
    for label, val in fields:
        c.setFont(K.SANS_B, 8.2)
        c.drawString(4 * mm, y, label + ":")
        c.setFont(K.SANS, 8.2)
        c.drawString(46 * mm, y, val)
        y -= 4.3 * mm
    y -= 1.5 * mm
    c.setFont(K.SANS, 8.2)
    c.drawString(4 * mm, y, "This message is only about the record above. Please take the necessary action.")
    y -= 5 * mm
    c.drawString(4 * mm, y, "Regards,  Ananta DocMan Alert")


def draw_ladder(c, w, h):
    # who gets the email: three fallbacks + always-added
    bw = (w - 8 * mm) * 0.5
    items = [
        ("1  People ticked on the record", "Used first, if any of them is active, has an email and matches the record."),
        ("2  Otherwise: the department's recipients", "People linked to the record's department."),
        ("3  Otherwise: the company's recipients", "People linked to the company the factory belongs to."),
    ]
    bh = 15 * mm
    gap = 5 * mm
    top = h - 1 * mm
    for i, (t, b) in enumerate(items):
        y = top - (i + 1) * bh - i * gap
        draw_box(c, 0, y, bw, bh, t, b, tsize=8.4, bsize=7.3, body_lines=2)
        if i < 2:
            draw_arrow(c, bw / 2, y, bw / 2, y - gap)
    # right: always added
    x = bw + 8 * mm
    y = top - 3 * bh - 2 * gap
    draw_box(c, x, y, w - x, 3 * bh + 2 * gap, "Also added, whatever the steps above found",
             "The person who uploaded the record, if they match.  The employee's own email, if \"Send alerts to this "
             "employee\" is ticked and the address is valid.  The department's group mailbox.  The compliance group "
             "mailbox.  Head Office mailboxes for Head Office records.",
             fill=PALE, stroke=GOLD, tsize=8.6, bsize=7.6, body_lines=9)
    c.setFillColor(MUTED)
    c.setFont(K.SANS_I, 7.4)
    c.drawString(0, 0, "Everyone above must belong to the record's factory and department to be emailed.")


# ================================================================= story
def build():
    doc = new_document(
        OUT, "Ananta DocMan Alert - User Manual", "Ananta Companies ICT",
        K.cover_page_fn(
            "Ananta Companies  |  Information & Communication Technology",
            "DocMan Alert\nUser Manual",
            "Track documents, licenses and health checkups",
            ["A step-by-step guide for everyone who uses the system,",
             "with screens, examples and answers to common questions."],
            [("Document date", EDITION), ("Edition", "2.0"),
             ("For", "All DocMan Alert users, with an administrator section"),
             ("Classification", "Internal")],
        ),
        K.band_header_footer("Ananta Companies  |  DocMan Alert", "User Manual", f"Internal  |  {EDITION}"),
    )
    story = [NextPageTemplate("body"), PageBreak()]

    # ------------------------------------------------------------ contents + start here
    story += [P("Contents", "H1Plain"), hrule()]
    story += [make_toc(S), PageBreak()]

    story += H1("Start here")
    story.append(P(
        "DocMan Alert keeps your factory's compliance documents, employee licenses and health checkups in one place "
        "and emails the right people before anything expires. This manual shows each screen, tells you what to "
        "click, and explains what happens next.", "Lead"))
    story.append(H2("Your first ten minutes"))
    story.append(how([
        "<b>Sign in</b> with the username and password your administrator gave you (section 1.2).",
        "<b>Look at the Dashboard.</b> Red and amber tiles are the records that need you (section 2).",
        "<b>Open Documents</b> and find one of your records with the search box (section 3.1).",
        "<b>Open My Alerts</b> to see which records will email you, and why (section 5.4).",
        "<b>Click Logout</b> when you finish, especially on a shared computer.",
    ]))
    story.append(H2("I want to..."))
    story.append(table(
        ["I want to", "Go to"],
        [
            ["Add a new document", "Section 3.3"],
            ["Let the system read a scanned file and fill the form", "Section 3.4 (documents), 4.4 and 4.5 (employees)"],
            ["Renew a license that is about to expire", "Section 3.5 (documents), 4.6 (employee licenses and checkups)"],
            ["Email someone about a document right now", "Section 3.10"],
            ["Add an employee, a license or a health checkup", "Sections 4.2, 4.4 and 4.5"],
            ["Find out why I did, or did not, get an email", "Sections 5.4 to 5.6"],
            ["Add a user or change who gets alerts (administrators)", "Sections 6.2 to 6.4"],
            ["Take or download a backup (Super Admin)", "Section 6.7"],
            ["Fix a problem", "Section 8"],
        ],
        [100 * mm, W - 100 * mm]))
    story.append(H2("How the manual marks things"))
    legend = Table([
        [callout(S, "tip", "A shortcut or a habit that saves time.", width=82 * mm, keep=False),
         callout(S, "note", "Something worth knowing about how the system behaves.", width=82 * mm, keep=False)],
        [callout(S, "important", "Read this before you continue. A mistake here is easy to make.", width=82 * mm, keep=False),
         callout(S, "admin", "Only for administrators. Other roles will not see these screens.", width=82 * mm, keep=False)],
    ], colWidths=[W / 2, W / 2])
    legend.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(legend)
    story.append(Spacer(1, 6))
    story.append(P(
        "The screens in this manual use made-up demo data. Your own factory, people and records will look "
        "different, and your menu may show fewer items than the pictures, depending on your role."))
    story.append(PageBreak())

    # ------------------------------------------------------------ 1 Getting started
    story += H1("1. Getting started")
    story.append(H2("1.1 What DocMan Alert does"))
    story.append(P(
        "Ananta's factories hold many documents that expire: fire licenses, trade licenses, boiler certificates, "
        "environmental clearances, audit certificates. People also hold licenses of their own, such as an "
        "electrician's license, and take health checkups on a schedule. Miss one date and a factory can be fined or "
        "stopped. DocMan Alert exists so that does not happen."))
    story.append(K.Stack([CanvasFigure(W, 26 * mm, draw_three_steps),
                               P("Figure 1. The whole idea in three steps.", "Caption")]))
    story.append(P(
        "You can think of three kinds of record: <b>Factory Documents</b> (under Documents), <b>Employee Licenses</b> "
        "and <b>Health Checkups</b> (both under Employee Compliance). All three work the same way: an expiry date "
        "drives a colour-coded status and a schedule of emails."))

    story.append(H2("1.2 Signing in"))
    story.append(P(
        "Open the web address your ICT team gave you. If you are not signed in you will always land on this page."))
    story.append(fig("login", "Figure 2. The sign-in page.", crop=(340, 930, 760, 1470), width=75 * mm))
    story.append(how([
        "Type your <b>Username</b>.",
        "Type your <b>Password</b>. Passwords are case sensitive.",
        "Click <b>Login</b>. You arrive on the Dashboard.",
    ]))
    story.append(note(
        "note",
        "Five wrong passwords in a row lock your account for <b>10 minutes</b>. Wait and try again. Nobody needs to "
        "unlock it. The lock survives a restart of the system, so closing the browser does not help."))
    story.append(note(
        "tip",
        "Forgot your password? Ask your Factory Admin or the ICT team to set a new one. Passwords are never emailed; "
        "your administrator will tell you the new one directly."))
    story.append(P(
        "To leave, click <b>Logout</b> at the top right. Your session also ends when you close the browser. "
        "There is no automatic timeout while the browser stays open, so log out on shared computers."))

    story.append(H2("1.3 Finding your way around"))
    story.append(P(
        "Every page has the same frame: a menu on the left, a bar at the top, and the working area in the middle."))
    story.append(fig(
        "dashboard_manager", "Figure 3. The Dashboard as a Compliance Manager sees it.",
        crop=(0, 0, 1100, 660),
        markers=[(200, 251, 1), (765, 32, 2), (900, 115, 3), (455, 171, 4), (482, 358, 5), (420, 517, 6), (882, 517, 7)],
        legend=[
            (1, "<b>Menu.</b> Shows only the pages your role may open. The highlighted item is where you are."),
            (2, "<b>Who you are.</b> Your role and name, with the Logout button."),
            (3, "<b>Warning banner.</b> Appears when documents have expired or are expiring within 90 days."),
            (4, "<b>Factory Documents tiles.</b> How many documents are in each status."),
            (5, "<b>Employee Compliance tiles.</b> Active employees, and licenses and checkups that are expiring or expired."),
            (6, "<b>Factory-wise Status.</b> The same counts for each factory you can see."),
            (7, "<b>Needs Attention.</b> The most urgent records. Click a name to open it."),
        ]))
    story.append(P("What the menu items are for:"))
    story.append(table(
        ["Menu item", "Use it to"],
        [
            ["Dashboard", "See where things stand across your factories."],
            ["My Alerts", "See which records will email you, and why."],
            ["Alert History", "See every alert email the system has sent, or failed to send."],
            ["Documents", "Manage factory documents and certificates."],
            ["Employee Compliance", "Manage employees, their licenses and health checkups. "
                                    "Two shortcuts appear underneath: Employee Licenses and Health Checkups."],
            ["Companies, Factories, Departments, Dept. Alert Recipients, Users", "Administration (section 6)."],
            ["Categories, Regulatory Authorities, Services, Backups, System Logs", "Super Admin only (section 6)."],
        ],
        [64 * mm, W - 64 * mm], first_bold=True))

    story.append(H2("1.4 Your role decides what you can do"))
    story.append(P(
        "Every account has one <b>role</b> and, for most roles, one <b>factory</b>. The role decides which buttons "
        "exist. The factory decides which records you can see. A person at Ananta Garments will not see another "
        "factory's documents unless their role covers every factory."))
    story.append(table(
        ["Role", "Sees", "Can do"],
        [
            ["Super Admin", "All factories", "Everything: all records, users, factories, companies, regulatory lists, "
                                             "Excel import, backups and System Logs."],
            ["Administration", "All factories", "Add, edit, renew, archive and delete documents; add and edit "
                                                "employees and archive their licenses and checkups; link documents "
                                                "to a regulatory service; view companies and departments and set "
                                                "who receives their alerts."],
            ["Factory Admin", "Own factory", "Everything a Compliance Manager can do, plus manage users of the "
                                             "factory, departments and recipients."],
            ["Compliance Manager", "Own factory", "Add, edit, renew, archive, restore and delete documents; add, edit "
                                                  "and archive employee licenses and checkups."],
            ["Compliance User", "Own factory", "Add, edit and renew. Cannot archive or delete."],
            ["Compliance Viewer", "All factories", "Look at everything. Cannot change anything."],
            ["Transport Manager", "Own factory", "Same rights as Compliance Manager, for the Transport department."],
            ["Transport User", "Own factory", "Same rights as Compliance User, for the Transport department."],
        ],
        [34 * mm, 28 * mm, W - 62 * mm], first_bold=True))
    story.append(note(
        "tip",
        "A button or menu item you expected is missing? That is your role at work, not a fault. If you believe your "
        "role is wrong, ask your Factory Admin."))

    story.append(H2("1.5 What the status colours mean"))
    story.append(P(
        "Every record carries one of six statuses. The system works them out from the expiry date, in Bangladesh "
        "time, and refreshes them every morning and whenever you save or open the Dashboard."))
    rows = [
        [Chip("Valid"), "More than 90 days left, or the record has no expiry date.", "No email."],
        [Chip("Expiring in 90 Days"), "61 to 90 days left.", "Weekly reminder."],
        [Chip("Expiring in 60 Days"), "31 to 60 days left.", "Weekly reminder."],
        [Chip("Expiring in 30 Days"), "8 to 30 days left.", "Weekly reminder."],
        [Chip("Expiring in 7 Days"), "1 to 7 days left.", "Urgent email."],
        [Chip("Expired"), "The expiry date is today or has passed.", "Urgent email."],
    ]
    story.append(table(["Status", "What it means", "Email"], rows, [48 * mm, 90 * mm, W - 138 * mm]))
    story.append(P(
        "Colours are the same everywhere: green is fine, amber is coming up, red and pink need action now."))

    # ------------------------------------------------------------ 2 Dashboard
    story += H1("2. The Dashboard")
    story.append(P(
        "The Dashboard is the first page after sign-in and the best place to start each day. It counts only "
        "<b>active</b> records in the factories you can see. Archived records are left out."))
    story.append(table(
        ["Area", "What it shows"],
        [
            ["Warning banner", "How many documents have already expired and how many expire within 90 days."],
            ["Factory Documents", "Total, Valid, Expiring in 90, 60, 30 and 7 Days, and Expired, for factory documents."],
            ["Employee Compliance", "Active employees; employee licenses that are expiring or expired; health checkups "
                                    "that are expiring or expired. \"Expiring\" counts all four expiring bands together."],
            ["Factory-wise Status", "One row per factory with Total, Valid, Expiring and Expired documents."],
            ["Needs Attention", "Up to 12 records that are Expired or Expiring in 7 Days, soonest first. "
                                "Expired ones come first. Click a name to open the document or the employee."],
        ],
        [40 * mm, W - 40 * mm], first_bold=True))
    story.append(note(
        "note",
        "Opening the Dashboard refreshes the statuses, so the numbers are always up to date. It never sends an "
        "email."))

    # ------------------------------------------------------------ 3 Documents
    story += H1("3. Factory documents")
    story.append(P(
        "Documents are the certificates and licenses a factory or department holds: fire license, trade license, "
        "boiler certificate, environmental certificate, audit reports and so on. Each document has one current "
        "record and any number of stored files, one per version."))

    story.append(H2("3.1 Find a document"))
    story.append(fig(
        "documents_list", "Figure 4. The Documents page.",
        crop=(240, 60, 1360, 700),
        markers=[(388, 107, 1), (1165, 107, 2), (252, 180, 3), (1008, 336, 4), (1092, 336, 5), (1248, 336, 6)],
        legend=[
            (1, "<b>View Archived.</b> Switches to documents that were archived."),
            (2, "<b>+ Add Document.</b> Only shown if your role may add records."),
            (3, "<b>Search and filters.</b> Name, certificate number, text inside the file, factory, department, status, "
                "document type and an expiry date range."),
            (4, "<b>Status.</b> The coloured label from section 1.5."),
            (5, "<b>Download.</b> Gets the latest file."),
            (6, "<b>Actions.</b> Edit, Renew, Archive and Delete, depending on your role."),
        ]))
    story.append(bullets(S, [
        "The list shows 10 documents per page, soonest expiry first. Documents with no expiry come last.",
        "The search box also looks <b>inside</b> the text read from scanned files, so you can search for a licence "
        "number that only appears in the PDF.",
        "Click <b>Filter</b> to apply your choices. Your filters stay in the page links while you move between pages.",
        "You only see the factories your account covers.",
    ]))

    story.append(H2("3.2 Open a document"))
    story.append(P("Click a document's name. The detail page shows everything about it and who will be emailed."))
    story.append(fig(
        "document_detail", "Figure 5. A document's detail page.",
        crop=(240, 60, 1100, 800),
        markers=[(798, 104, 1), (464, 346, 2), (376, 452, 3), (424, 494, 4), (958, 765, 5)],
        legend=[
            (1, "<b>Action buttons.</b> Edit, Renew, Send alert and Delete."),
            (2, "<b>Status.</b> Worked out from the expiry date."),
            (3, "<b>OCR.</b> Run OCR reads the stored file and suggests details (section 3.9)."),
            (4, "<b>Alert Recipients.</b> Exactly who will be emailed for this document, and why."),
            (5, "<b>Files.</b> One line per version. View opens the file; Download saves it."),
        ]))

    story.append(H2("3.3 Add a document"))
    story.append(how([
        "On the Documents page click <b>+ Add Document</b>.",
        "Choose the <b>Factory</b> and the <b>Department</b>.",
        "Enter the <b>Document Name</b>, <b>Document Type</b> and <b>Certificate/License No.</b>",
        "Enter the <b>Issue Date</b> and <b>Expiry Date</b>, or tick <b>This document has no expiry date</b>.",
        "Fill in Issuing Authority and Responsible Person if you know them.",
        "Tick the people who should be emailed (optional, see section 5.3).",
        "Attach the file and click <b>Save</b>.",
    ]))
    story.append(fig(
        "document_add", "Figure 6a. The Add Document form, top half.",
        crop=(370, 70, 975, 720), width=125 * mm, max_h=135 * mm,
        markers=[(470, 123, 1), (856, 291, 2), (700, 374, 3), (790, 372, 4)],
        legend=[
            (1, "Factory and department. The recipient list further down depends on both."),
            (2, "Certificate or license number. The star means it is required."),
            (3, "Issue and expiry dates."),
            (4, "No expiry. Tick this for documents that never expire; the expiry box switches off."),
        ]))
    story.append(fig(
        "document_add", "Figure 6b. The same form, bottom half.",
        crop=(370, 715, 975, 1250), width=125 * mm, max_h=115 * mm,
        markers=[(515, 733, 5), (488, 908, 6), (598, 1086, 7)],
        legend=[
            (5, "Alert recipients. People of this factory and department, plus the department's group mailbox."),
            (6, "Upload the file here. PDF, JPG, PNG, DOCX or XLSX, up to 10 MB."),
            (7, "Or let the system read a file for you (section 3.4)."),
        ]))
    story.append(table(
        ["Field", "What to enter", "Needed?"],
        [
            ["Factory", "The factory that holds the document. You only see your own factory unless your role covers all.", "Yes"],
            ["Department", "The department responsible. Alerts are matched by department, so set it.", "Strongly advised"],
            ["Document Name", "A name people will recognise, for example Fire License 2026.", "Yes"],
            ["Document Type", "Start typing and pick a suggestion (Fire License, Trade License, Boiler Certificate, "
                              "Environmental Certificate, Factory License, Amfori BSCI, WRAP, Sedex, Other) "
                              "or type your own.", "Yes"],
            ["Certificate/License No.", "The number printed on the document.", "Yes"],
            ["Issue Date, Expiry Date", "Dates on the document. Expiry cannot be earlier than issue.", "Expiry or No expiry"],
            ["Issuing Authority", "The office that issued it.", "No"],
            ["Responsible Person", "The person who looks after it. Text only; this does not send email.", "No"],
            ["Renewal Status", "Not Started, In Progress or Completed. Use it to track a renewal.", "No"],
            ["Remarks", "Anything worth remembering.", "No"],
            ["Category, Authority, Service", "Links the document to a government office and service. "
                                             "Only Super Admin and Administration see these.", "No"],
            ["Alert Recipients", "Specific people to email (section 5.3).", "No"],
            ["Upload File", "The scan or file itself.", "Advised"],
        ],
        [38 * mm, W - 38 * mm - 28 * mm, 28 * mm], first_bold=True))
    story.append(note(
        "important",
        "Always set an expiry date <b>or</b> tick No expiry. If you leave both empty the document is saved as Valid "
        "with no expiry, and the system will never send an alert for it."))
    story.append(note(
        "tip",
        "Set the department every time. The system emails only people who belong to the document's factory <b>and</b> "
        "department, so a document with no department reaches far fewer people."))

    story.append(H2("3.4 Let the system fill the form for you"))
    story.append(P(
        "Typing every detail is slow. If you have a PDF, JPG or PNG of the document, the system can read it and "
        "suggest the name, number, authority and dates. It works with printed text in English and Bangla."))
    story.append(how([
        "On the <b>Add Document</b> page scroll to <b>Extract Data from Document</b>.",
        "Choose the file and click <b>Extract Data from Document</b>. Scans take a few seconds.",
        "A <b>Confirm Extracted Data</b> page opens with the form already filled in.",
        "Check every field. Correct anything that is wrong. Fill the ones left empty.",
        "Click <b>Confirm &amp; Save</b>, or <b>Cancel</b> to throw everything away.",
    ]))
    story.append(fig(
        "document_extract", "Figure 7. Confirming extracted data (top of the page). Grey \"Suggested\" notes show what "
                            "was read from the file; the extracted text sits below the form.",
        crop=(240, 60, 1100, 830), width=140 * mm, max_h=130 * mm))
    story.append(note(
        "important",
        "The suggestions are a starting point, not a result. Nothing is saved until you click Confirm &amp; Save, and "
        "you are responsible for the final values. A low confidence warning means the scan was hard to read: "
        "check every field slowly."))
    story.append(note(
        "note",
        "A date such as 05/06/2026 could be 5 June or 6 May, so the system will not guess. It leaves the date for you "
        "to type. Dates written like 12 Jan 2026 or 2026-01-12 are read correctly."))

    story.append(H2("3.5 Renew a document"))
    story.append(P(
        "When a new license arrives, do not create a second document. Renew the existing one so the history stays "
        "together."))
    story.append(how([
        "Open the document and click <b>Renew</b> (also available in the list).",
        "Enter the new <b>Issue Date</b> and <b>Expiry Date</b>, or tick No expiry.",
        "Update the <b>Certificate/License No.</b> if it changed, and add remarks if useful.",
        "Attach the new file and click <b>Create New Version</b>.",
    ]))
    story.append(fig("document_renew", "Figure 8. The Renew page.", crop=(240, 60, 1100, 689), width=130 * mm))
    story.append(P(
        "What happens: the new file becomes the next version, the old file stays available in the document's Files "
        "list, the status is recalculated from the new expiry date, and Renewal Status becomes <b>Completed</b>. "
        "Because the expiry date is new, alerts start fresh for it."))
    story.append(note(
        "important",
        "Fill in the new expiry date. A renewal saved with both the expiry date and No expiry left empty clears the "
        "expiry and the document shows Valid."))

    story.append(H2("3.6 Edit a document"))
    story.append(P(
        "Click <b>Edit</b>, change what you need and click <b>Save</b>. The status and future alerts update at "
        "once. If you attach a file while editing, it is stored as a new version and the old one is kept."))

    story.append(H2("3.7 View and download files"))
    story.append(P(
        "On the detail page, <b>View</b> opens the file in a new tab and <b>Download</b> saves it. Every version is "
        "listed with its issue date, expiry date and upload date, newest first. In the Documents list the "
        "Download button always gets the latest version."))

    story.append(H2("3.8 Archive, restore and delete"))
    story.append(table(
        ["Action", "What happens", "Can it be undone?", "Who"],
        [
            ["Archive", "The document leaves the active list and the Dashboard, and stops sending alerts. Files are kept.",
             "Yes: View Archived, then Restore.", "Managers and above"],
            ["Restore", "Brings an archived document back to the active list.", "-", "Managers and above"],
            ["Delete", "Removes the document, all its versions and files for good. Alert History keeps the "
                       "record of emails already sent.", "No.", "Managers and above"],
        ],
        [22 * mm, 82 * mm, 42 * mm, W - 146 * mm], first_bold=True))
    story.append(P(
        "\"Managers and above\" means Super Admin, Administration, Factory Admin, Compliance Manager and Transport "
        "Manager. Compliance User and Transport User cannot archive or delete."))
    story.append(fig("document_delete", "Figure 9. Delete asks you to type the exact document name.",
                     crop=(240, 60, 1100, 330), width=130 * mm))
    story.append(note(
        "warning",
        "Delete cannot be undone. If you only want a document out of the way, use Archive instead."))

    story.append(H2("3.9 Run OCR on a stored file"))
    story.append(P(
        "You can also read a file that is already attached. On the document's detail page, find the OCR line and "
        "click <b>Run OCR</b>. When it finishes click <b>View Extracted Text</b>."))
    story.append(how([
        "Read the <b>Suggested fields</b>: certificate number, issuing authority, issue date, expiry date.",
        "Tick <b>Apply</b> next to each value you want to save. Nothing is applied unless you tick it.",
        "Fix the extracted text if you wish; it is what the search box looks at.",
        "Click <b>Confirm</b>.",
    ]))
    story.append(P(
        "OCR works on PDF, JPG and PNG files up to 10 MB and 50 pages. If the reading engine is not installed on "
        "the server you will see \"OCR is currently unavailable\"; everything else keeps working."))

    story.append(H2("3.10 Send an alert right now"))
    story.append(P(
        "The system already emails people on its own schedule (section 5). Use <b>Send alert</b> when you want to "
        "nudge someone today, for example to chase a renewal."))
    story.append(how([
        "Open the document and click <b>Send alert</b>.",
        "Tick the people and the group mailbox you want to reach.",
        "Click <b>Send this alert</b>.",
    ]))
    story.append(fig(
        "document_send_alert", "Figure 10. Choosing who to alert.",
        crop=(240, 60, 1100, 690), width=140 * mm,
        markers=[(790, 411, 1), (790, 480, 2), (790, 531, 3), (505, 624, 4)],
        legend=[
            (1, "The department's group mailbox. Ticked by default."),
            (2, "A person in this document's factory and department. Ticked people receive the email."),
            (3, "An unticked person. They will not receive this one."),
            (4, "Send. The result shows at the top of the next page and in Alert History."),
        ], ))
    story.append(note(
        "note",
        "The people you tick are also saved as this document's alert recipients, replacing the earlier ones. Future "
        "automatic alerts will go to them first. A manual alert is an extra email; it does not cancel the automatic "
        "ones."))

    # ------------------------------------------------------------ 4 Employee compliance
    story += H1("4. Employee compliance")
    story.append(P(
        "This part tracks people: who works where, which licenses they hold, and when their health checkups "
        "expire. It follows the same ideas as documents, with one extra layer, the employee."))

    story.append(H2("4.1 The employee list"))
    story.append(fig(
        "employees_list", "Figure 11. Employee Compliance. Search by name or Employee ID, and filter by factory, section "
                          "or designation.", crop=(240, 60, 1360, 560)))
    story.append(P(
        "Inactive employees are hidden unless you tick <b>Include inactive</b>. The two buttons on the left take "
        "you to cross-employee lists of licenses and health checkups (section 4.8). <b>Import</b> appears for "
        "Super Admin only (section 4.9)."))

    story.append(H2("4.2 Add or edit an employee"))
    story.append(how([
        "Click <b>+ Add Employee</b>.",
        "Enter the <b>Employee ID</b>, <b>Name</b>, and, if you have them, Designation and Section.",
        "Choose the <b>Factory</b> and <b>Department</b> and enter the Date of Joining.",
        "Add an <b>Email</b> only if this person should receive alerts about their own records. Then tick "
        "<b>Send alerts to this employee</b>.",
        "Leave Status on <b>Active</b> and click <b>Save</b>.",
    ]))
    story.append(table(
        ["Field", "Notes"],
        [
            ["Employee ID", "The number from HR. Must be unique within the factory."],
            ["Department", "Used to decide who is emailed about this person's licenses and checkups."],
            ["Email and Send alerts", "The person's own address is used only when both are set and the address is valid. "
                                      "Otherwise their personal email is never used."],
            ["Status", "Set Inactive when someone leaves. Employees are never deleted, so history is kept."],
        ],
        [38 * mm, W - 38 * mm], first_bold=True))

    story.append(H2("4.3 The employee page"))
    story.append(fig(
        "employee_detail", "Figure 12. An employee with licenses and health checkups.",
        crop=(240, 60, 1100, 735),
        markers=[(948, 104, 1), (928, 322, 2), (882, 447, 3), (1048, 413, 4), (1058, 446, 5), (918, 600, 6)],
        legend=[
            (1, "<b>Edit Employee.</b>"),
            (2, "<b>+ Add License.</b>"),
            (3, "<b>Run OCR</b> reads the attached file and suggests details. Once run, this shows <b>OCR</b> to review the result."),
            (4, "<b>Edit</b> a license; use it to change dates or attach a newer file."),
            (5, "<b>Archive</b> hides a record that no longer applies."),
            (6, "<b>+ Add Checkup.</b>"),
        ]))

    story.append(H2("4.4 Add an employee license"))
    story.append(P(
        "Licenses are things a person holds personally: electrician license, medical license, boiler operator "
        "license. Open the employee and click <b>+ Add License</b>."))
    story.append(fig(
        "license_add", "Figure 13. The Add License form.",
        crop=(405, 70, 915, 1060), width=100 * mm, max_h=150 * mm,
        markers=[(580, 162, 1), (718, 328, 2), (622, 521, 3), (526, 694, 4), (615, 873, 5)],
        legend=[
            (1, "Compliance Type. Type your own or pick Electrician License, Medical License, Boiler Operator License."),
            (2, "Issue and expiry dates, or No expiry date."),
            (3, "Additional Alert Recipients. Optional. People of the employee's factory and department."),
            (4, "Upload the file."),
            (5, "Or extract the details from a PDF, JPG or PNG (works like section 3.4)."),
        ]))
    story.append(P(
        "Registration Number, Category and Issuing Authority are optional text fields. The recipients list works "
        "like it does for documents: people ticked here are emailed first; otherwise the employee's department, then "
        "the company (section 5.3)."))

    story.append(H2("4.5 Add a health checkup"))
    story.append(P(
        "Open the employee and click <b>+ Add Checkup</b>. Each exam is its own record, so one person can have "
        "four, each with its own expiry date."))
    story.append(table(
        ["Field", "What to enter"],
        [
            ["Exam Type", "CBC, Hearing / Widal, Chest X-Ray P/A or Urine Test."],
            ["Report Date", "The date on the medical report."],
            ["Expiry Date or No expiry", "When the checkup must be repeated."],
            ["PRE (Y/N)", "Pre-employment checkup: Y or N."],
            ["Test Center", "Where it was done."],
            ["Additional Alert Recipients, file", "Same as for licenses. You can also extract the details from the report."],
        ],
        [50 * mm, W - 50 * mm], first_bold=True))

    story.append(H2("4.6 Renew a license or a checkup"))
    story.append(P(
        "There is no separate Renew button here. Click <b>Edit</b> on the record, enter the new dates and attach the "
        "new file. The new file becomes the next version; links such as <b>v1</b> and <b>v2</b> appear in the File "
        "column so the earlier files stay reachable."))

    story.append(H2("4.7 Archive a license or checkup"))
    story.append(P(
        "Click <b>Archive</b> on the record. It disappears from the employee page, the lists and the Dashboard and "
        "stops sending alerts. There is no Restore button for these records, so ask your administrator or ICT if you "
        "archived one by mistake. Compliance Users see the button, but the system refuses the action for them."))

    story.append(H2("4.8 Licenses and checkups across all employees"))
    story.append(P(
        "<b>Employee Licenses</b> and <b>Health Checkups</b> (under Employee Compliance in the menu) list every "
        "record in your factories, soonest expiry first, 15 per page."))
    story.append(fig("licenses_list", "Figure 14. All employee licenses. Search by registration number, employee name or text in the file.",
                     crop=(240, 60, 1360, 560)))

    story.append(H2("4.9 Import many employees from Excel"))
    story.append(callout(S, "admin", "Super Admin only.", width=W))
    story.append(P(
        "Importing is for onboarding or moving data from spreadsheets. You always see a preview first, and "
        "nothing is saved until you confirm."))
    story.append(how([
        "Go to <b>Employee Compliance</b> and click <b>Import</b>.",
        "Choose an <b>Import Type</b>: Employees, Employee Licenses, Medical Licenses, Health Checkups, Alert "
        "Recipients, or Full Workbook.",
        "Choose your <b>.xlsx</b> file and click <b>Upload &amp; Preview</b>.",
        "Read the preview. Valid rows will be imported; warnings are skipped duplicates; failed rows say why.",
        "Click <b>Confirm Import</b>, or Cancel.",
    ]))
    story.append(fig("import_preview", "Figure 15. An import preview: 2 rows ready, 1 skipped duplicate, 1 failed.",
                     crop=(240, 60, 1100, 700), width=140 * mm))
    story.append(table(
        ["Import type", "Columns it needs"],
        [
            ["Employees", "Employee ID, Employee Name, Factory. Optional: Designation, Section, Company, Department, "
                          "Date of Joining, Email."],
            ["Employee Licenses, Medical Licenses", "Name, ID No (or Employee ID), Factory. Optional: Designation, "
                                                    "Section, Registration No, Category, Issue Date, Expiry Date, Remarks."],
            ["Health Checkups", "Employee ID, Name, Factory, plus report and expiry columns for CBC, Hearing / Widal, "
                                "Chest X-Ray and Urine Test. Optional: PRE Y/N, Test Center, DOJ."],
            ["Alert Recipients", "Company, Recipient Scope (COMPANY or DEPARTMENT) and Username or Email. "
                                 "Department is needed for scope DEPARTMENT."],
            ["Full Workbook", "One file with sheets named Employees, Employee Licenses, Medical Licenses, "
                              "Health Checkups and Alert Recipients."],
        ],
        [50 * mm, W - 50 * mm], first_bold=True))
    story.append(bullets(S, [
        "Companies, factories, departments and users must already exist. Unknown ones are reported and never created.",
        "Dates can be real Excel dates, 2026-01-12, 12-01-2026 or 12-Jan-2026. Cells that say Till Now, N/A, "
        "Permanent or a dash become <i>no expiry</i>.",
        "Rows that already exist are skipped, so importing the same file twice does no harm.",
    ]))

    # ------------------------------------------------------------ 5 Alerts and emails
    story += H1("5. Alerts and emails")
    story.append(P(
        "You never have to remember to send reminders. The system does it. This section explains when, to whom, "
        "and where you can check afterwards."))
    story.append(H2("5.1 When emails are sent"))
    story.append(K.Stack([CanvasFigure(W, 38 * mm, draw_timeline),
                               P("Figure 16. A record moves from left to right as its expiry date approaches.", "Caption")]))
    story.append(table(
        ["Email", "When", "Which records", "Repeats?"],
        [
            ["Weekly reminder", "Every Saturday at 09:00", "Expiring in 90, 60 or 30 Days",
             "Every week while the record stays in those bands."],
            ["Urgent alert", "Every day at 09:00", "Expiring in 7 Days, or Expired",
             "Once for each status. You get one when it reaches 7 days and one when it expires, not one every morning."],
            ["Manual alert", "When someone clicks Send alert", "One document", "Each time it is sent."],
        ],
        [30 * mm, 38 * mm, 42 * mm, W - 110 * mm], first_bold=True))
    story.append(P(
        "One email is about <b>one record</b>. If three of your records are expiring this week you will receive "
        "three separate emails, each easy to act on and to file."))
    story.append(note(
        "note",
        "After a renewal the expiry date is new, so the urgent alert can fire again in the next cycle. Times are "
        "Bangladesh Standard Time."))

    story.append(H2("5.2 What an email looks like"))
    story.append(K.Stack([CanvasFigure(W, 110 * mm, draw_email),
                               P("Figure 17. An urgent alert. Weekly and manual emails use the same layout.", "Caption")]))
    story.append(P(
        "Subjects tell you the situation at a glance: <b>Compliance Alert: ... - Expiring in 30 Days</b> for the "
        "weekly reminder, <b>URGENT: ... Expires in 4 Days</b> or <b>URGENT: ... Expired</b> for urgent alerts, and "
        "<b>Compliance Notice: ...</b> for a manual one."))

    story.append(H2("5.3 Who gets the email"))
    story.append(K.Stack([CanvasFigure(W, 72 * mm, draw_ladder),
                               P("Figure 18. How the system chooses recipients for a record.", "Caption")]))
    story.append(P(
        "The system goes down the three boxes on the left and stops at the first one that has at least one valid "
        "person. Then it adds the extras on the right. A person is only emailed when their account is active, has a "
        "valid email address, and belongs to the same <b>factory and department</b> as the record."))
    story.append(note(
        "tip",
        "Two quick checks explain most surprises: does the person's account have the right factory and department, "
        "and does it have an email address?"))
    story.append(P(
        "An example: a Fire License belongs to Ananta Garments, department Compliance. Farhana uploaded it and is "
        "ticked on it. Imran is a Compliance department recipient. The email goes to Farhana and to the compliance "
        "group mailbox. Imran is skipped, because a person was ticked on the document. Untick everyone and the "
        "email goes to the department's recipients instead, which includes Imran."))

    story.append(H2("5.4 My Alerts"))
    story.append(P(
        "<b>My Alerts</b> answers \"what am I responsible for?\" It lists the records that will really send you an "
        "email, and says why."))
    story.append(fig(
        "my_alerts", "Figure 19. My Alert Responsibilities.", crop=(240, 60, 1360, 480),
        markers=[(402, 164, 1), (1068, 214, 2), (1252, 214, 3)],
        legend=[
            (1, "Switch between records that need attention and <b>all</b> your responsibilities."),
            (2, "The record's current status."),
            (3, "<b>Alert source</b>: Document-specific, Department, Company or Uploader. This is the reason you are on the list."),
        ]))
    story.append(P(
        "My Alerts is not an inbox. It shows up to 200 records per kind, soonest expiry first. Messages already "
        "sent are in Alert History."))

    story.append(H2("5.5 Alert History"))
    story.append(P(
        "<b>Alert History</b> lists every alert email the system handed to the mail server, including the ones that "
        "failed. You see the factories you can access."))
    story.append(fig(
        "alert_history", "Figure 20. Alert History.", crop=(240, 60, 1360, 610),
        markers=[(252, 162, 1), (500, 233, 2), (842, 233, 3), (1328, 233, 4)],
        legend=[
            (1, "Filter by type (Weekly, Urgent, Manual), by result, or search a recipient, subject or document name."),
            (2, "<b>Type</b> of alert."),
            (3, "<b>Subject.</b> Click it to see which record the email covered and why this person received it."),
            (4, "<b>Result.</b> Sent, or Failed if the mail server could not deliver."),
        ]))

    story.append(H2("5.6 \"I did not get an email\""))
    story.append(P("Work through this list in order. Most cases are solved by the first three."))
    story.append(how([
        "<b>Check the status.</b> Weekly mail only goes for Expiring in 90, 60 and 30 Days. Urgent mail only for 7 "
        "Days and Expired. A Valid record sends nothing.",
        "<b>Check the day.</b> Weekly reminders go out on Saturday morning; urgent ones at 09:00 each day.",
        "<b>Open Alert History</b> and search for your name or the document. It shows who the system tried to "
        "reach and whether it worked.",
        "<b>Open My Alerts</b> with Show all responsibilities. If the record is not listed, you are not a recipient.",
        "<b>Check your account.</b> It needs a valid email address, to be active, and the same factory and department "
        "as the record. Ask your Factory Admin to check.",
        "<b>Check the junk folder.</b> Mail comes from document_tracker@ananta.com.bd.",
        "If History says <b>Failed</b>, the mail server was unreachable. Tell ICT.",
    ]))

    # ------------------------------------------------------------ 6 Administrators
    story += H1("6. Administrator guide")
    story.append(callout(
        S, "admin",
        "This section is for Super Admin, Administration and Factory Admin. Some screens belong to Super Admin only; "
        "each heading says who may use it.", width=W))
    story.append(fig("dashboard_admin", "Figure 21. The menu of a Super Admin. Administrators see more of it than other roles.",
                     crop=(0, 0, 1100, 720), max_h=105 * mm))
    story.append(table(
        ["Screen", "Super Admin", "Administration", "Factory Admin"],
        [
            ["Users", "All", "-", "Own factory"],
            ["Companies", "Add, edit, delete, recipients", "View, recipients", "-"],
            ["Factories", "All", "-", "-"],
            ["Departments", "Add, edit, delete", "View", "Add, edit, delete"],
            ["Dept. Alert Recipients", "All factories", "All factories", "Own factory"],
            ["Regulatory Directory", "All", "-", "-"],
            ["Backups, System Logs, Import", "Yes", "-", "-"],
        ],
        [50 * mm, 46 * mm, 40 * mm, W - 136 * mm], first_bold=True))

    story.append(H2("6.1 Plan before you add anything"))
    story.append(P(
        "Set things up in this order on a new site, because each step needs the one before it:"))
    story.append(how([
        "<b>Company</b> and <b>Factories</b> (Super Admin).",
        "<b>Departments</b>, with a group email where a shared mailbox exists.",
        "<b>Users</b>, each with a role, a factory, a department and an email.",
        "<b>Alert recipients</b> for companies and for factory and department pairs.",
        "Then the people at the factories can start adding documents and employees.",
    ]))

    story.append(H2("6.2 Users"))
    story.append(fig(
        "users_list", "Figure 22. The Users page.", crop=(240, 60, 1360, 720),
        markers=[(1100, 107, 1), (1282, 75, 2), (772, 161, 3), (948, 161, 4), (1128, 140, 5), (1292, 161, 6)],
        legend=[
            (1, "Search by name, email, role, factory or department."),
            (2, "<b>+ Add User.</b>"),
            (3, "<b>Role.</b> Decides what the person can do."),
            (4, "<b>Factory.</b> \"Global (All Factories)\" means no single factory."),
            (5, "<b>Department.</b> Needed for alerts to reach this person."),
            (6, "<b>Edit</b> and <b>Delete</b>."),
        ]))
    story.append(P("To add a user, click <b>+ Add User</b> and fill in:"))
    story.append(table(
        ["Field", "Notes"],
        [
            ["Username", "Unique. Used to sign in. Cannot be reused."],
            ["Full Name", "Shown in the top bar and on lists."],
            ["Email", "Needed for the person to receive alerts. Without it they show as \"No valid email\" and cannot be ticked."],
            ["Role", "See section 1.4. A Factory Admin can give Compliance Manager, Compliance User, Compliance Viewer, "
                     "Transport Manager and Transport User."],
            ["Factory", "The person's own factory. \"None (Global Access)\" is for Super Admin, Administration and Compliance "
                        "Viewer, or for someone who should receive a department's alerts for every factory."],
            ["Department", "Their department. Alerts are matched on factory and department, so set it."],
            ["Password, Confirm", "At least 6 characters. Tell the person directly; the system does not email it."],
            ["Status", "Active or Inactive. Inactive people cannot sign in and receive no alerts."],
        ],
        [34 * mm, W - 34 * mm], first_bold=True))
    story.append(note(
        "important",
        "A user with no factory whose role is not Super Admin, Administration or Compliance Viewer receives alerts for "
        "their department in every factory, but sees no records in the application. This suits a manager who only "
        "needs the emails. Give such a person the Compliance Viewer role if they also need to look at records."))
    story.append(P(
        "<b>Editing.</b> Leave the password boxes empty to keep the current password. You cannot change your own "
        "role or active status; ask another administrator. Only a Super Admin can move a user to another factory."))
    story.append(P(
        "<b>Deleting.</b> Click Delete and type the exact username. The person's past documents, versions and "
        "alert history stay; only the account and the links to it go. You cannot delete yourself, the built-in "
        "<i>admin</i> account, or the last Super Admin. A Factory Admin can delete ordinary users of their own "
        "factory but not Administration, Factory Admin or Super Admin accounts. To stop someone signing in without "
        "removing them, set Status to Inactive instead."))

    story.append(H2("6.3 Companies, factories and departments"))
    story.append(fig("companies_list", "Figure 23. Companies. The Alert Recipients button sets who is emailed for the whole company.",
                     crop=(240, 60, 1100, 420), width=140 * mm))
    story.append(bullets(S, [
        "<b>Companies</b> hold factories. Only Super Admin adds, edits or deletes them. A company with factories "
        "cannot be deleted.",
        "<b>Factories</b> belong to one company. A factory with documents, employees or users cannot be deleted.",
        "<b>Departments</b> are one shared list for the whole company, not a copy per factory. Each can have a "
        "<b>Group Email</b>, a shared mailbox such as compliance_dept@ or transport_agl@. That mailbox receives alerts "
        "for every record in that department, whatever the factory. A department that is still used by documents or "
        "employees cannot be deleted.",
    ]))

    story.append(H2("6.4 Who receives alerts: recipients"))
    story.append(P(
        "There are three places to say who is emailed. The system uses them as the fallback ladder in section 5.3."))
    story.append(table(
        ["Where", "Who it covers", "Where to set it"],
        [
            ["On a document, license or checkup", "That one record", "The Alert Recipients ticks on its form"],
            ["Department, per factory", "Records of that department in that factory",
             "Dept. Alert Recipients (menu), or Departments, then Alert Recipients"],
            ["Company", "Every record of the company's factories", "Companies, then Alert Recipients"],
        ],
        [50 * mm, 60 * mm, W - 110 * mm], first_bold=True))
    story.append(fig(
        "dept_recipients", "Figure 24. Dept. Alert Recipients: pick a factory and a department, then tick who receives the alerts.",
        crop=(240, 60, 1100, 640),
        markers=[(350, 243, 1), (702, 243, 2), (745, 385, 3), (612, 433, 4), (436, 578, 5)],
        legend=[
            (1, "Choose the <b>Factory</b>."),
            (2, "Choose the <b>Department</b>, then click Show."),
            (3, "The department's group mailbox. It is always emailed, so you do not tick it."),
            (4, "People who belong to exactly this factory and department. Tick the ones who should receive alerts."),
            (5, "<b>Save recipients.</b> People of other factories keep their settings."),
        ]))
    story.append(note(
        "tip",
        "Only users of the chosen factory and department are listed. If someone is missing, open their user account "
        "and check the factory and department."))

    story.append(H2("6.5 Regulatory Directory"))
    story.append(callout(S, "admin", "Super Admin manages the lists. Super Admin and Administration can use them on documents.", width=W))
    story.append(P(
        "The directory is a three-level list of government offices: <b>Category</b> (for example Environment), "
        "<b>Regulatory Authority</b> (for example the Department of Environment) and <b>Service</b> (for example "
        "Environmental Clearance Certificate renewal). On the document form, choosing a category narrows the "
        "authority list, and choosing an authority narrows the services. A category with authorities, or a service "
        "linked to a document, cannot be deleted."))
    story.append(fig("reg_services", "Figure 25. Services, with the authority and category they belong to.",
                     crop=(240, 60, 1100, 520), width=140 * mm))

    story.append(H2("6.6 Alert recipients for many people at once"))
    story.append(P(
        "To link many users to companies or departments in one go, use the <b>Alert Recipients</b> import "
        "(section 4.9). Duplicate links are skipped."))

    story.append(H2("6.7 Backups"))
    story.append(callout(S, "admin", "Super Admin only.", width=W))
    story.append(fig("backups", "Figure 26. Backups. Each zip holds the database and every uploaded file.",
                     crop=(240, 60, 1100, 440), width=140 * mm))
    story.append(bullets(S, [
        "A backup is made <b>automatically every day at 18:30</b>. Backups older than the retention period "
        "(30 days by default) are removed.",
        "Click <b>Create backup now</b> before a risky change, such as a large import.",
        "Click <b>Download</b> to keep a copy off the server.",
        "Restoring is done by ICT with the restore script. Ask ICT, and say which backup you want.",
    ]))

    story.append(H2("6.8 System Logs"))
    story.append(callout(S, "admin", "Super Admin only.", width=W))
    story.append(P(
        "System Logs record what the <b>system</b> did: sign-ins, scheduled jobs, backups, mail results, imports and "
        "OCR runs. They are for troubleshooting. They do not say who changed which document."))
    story.append(fig("system_logs", "Figure 27. System Logs with level and event filters.",
                     crop=(240, 60, 1360, 520)))
    story.append(table(
        ["Event", "Meaning"],
        [
            ["LOGIN_SUCCESS, LOGIN_FAILURE", "A sign-in, a sign-out or a wrong password."],
            ["ACCOUNT_SECURITY_EVENT", "An account was locked, or someone tried a locked account."],
            ["JOB_START, JOB_SUCCESS, JOB_FAILURE", "A scheduled job ran, and how it ended."],
            ["EMERGENCY_ALERT_SENT, ALERT_SENT, EMAIL_SUCCESS", "Urgent, manual or weekly mail was sent."],
            ["ALERT_FAILED, EMAIL_FAILURE, EMERGENCY_ALERT_FAILED", "Some mail could not be sent. Check Alert History."],
            ["ALERT_RECIPIENT_SKIPPED", "A user was skipped because their email is missing or invalid."],
            ["BACKUP_SUCCESS, BACKUP_FAILURE", "A backup finished or failed."],
            ["XLSX_IMPORT_*", "An Excel import started, finished or failed."],
            ["OCR_STARTED, OCR_COMPLETED, OCR_FAILED", "Reading a file."],
            ["UNHANDLED_EXCEPTION", "Something unexpected went wrong. Give the time to ICT."],
        ],
        [70 * mm, W - 70 * mm], first_bold=True, mono_cols=(0,)))

    story.append(H2("6.9 A routine for administrators"))
    story.append(table(
        ["How often", "What to check"],
        [
            ["Every morning", "Dashboard: anything red? Needs Attention is the work list. "
                               "Alert History: any Failed results from the 09:00 run?"],
            ["Every week", "After Saturday 09:00, look at Alert History for failures. Review expired records "
                           "that nobody has renewed."],
            ["When people join or leave", "Add the user with factory, department and email, or set them Inactive. "
                                          "Update department recipients."],
            ["Every month", "Backups: is the daily backup there? Download one copy. "
                            "Users: are there accounts with no email or no department?"],
        ],
        [40 * mm, W - 40 * mm], first_bold=True))

    # ------------------------------------------------------------ 7 Good habits
    story += H1("7. Good habits")
    story.append(bullets(S, [
        "<b>Renew, do not duplicate.</b> Use Renew so the new file sits next to the old ones and the history is complete.",
        "<b>Always set the department.</b> It decides who is emailed.",
        "<b>Give every user an email address.</b> Users without one cannot be chosen as recipients.",
        "<b>Name documents so people can recognise them</b> at a glance: type, year, site if needed.",
        "<b>Attach the file.</b> A record without a file cannot be checked later and cannot be read by OCR.",
        "<b>Tick No expiry only when it is true.</b> Such records never alert.",
        "<b>Use Archive, not Delete,</b> when a record is simply out of date.",
        "<b>Review My Alerts</b> after you change jobs or departments, to see that your responsibilities still match.",
    ], after=4))

    # ------------------------------------------------------------ 8 Troubleshooting
    story += H1("8. Troubleshooting and questions")
    story.append(H2("8.1 Problems and fixes"))
    story.append(table(
        ["What you see", "Why", "What to do"],
        [
            ["I cannot see a factory's documents.", "Your account belongs to one factory.",
             "Ask your Factory Admin if you should have access to another."],
            ["A menu item or button is missing.", "Your role does not include it.", "Ask your Factory Admin."],
            ["Access denied (403).", "The record is in another factory, or your role may not open that page.",
             "Go back to the Dashboard. Ask an administrator if you need access."],
            ["Account locked.", "Five wrong passwords.", "Wait 10 minutes and try again."],
            ["File too large, or file not allowed.", "Limit is 10 MB. Allowed: PDF, JPG, PNG, DOCX, XLSX.",
             "Compress the file or save it as a PDF."],
            ["Expiry date must be on or after the issue date.", "The two dates are the wrong way round.", "Check both dates."],
            ["\"Select a factory and a department to see matching users\".", "The list needs both.", "Choose a factory and a department."],
            ["\"No users belong to this factory and department\".", "No account has that pair.",
             "Ask an administrator to set the factory and department on the users."],
            ["A person is greyed out with \"No valid email\".", "Their account has no usable address.",
             "An administrator should add the email to the user account."],
            ["OCR is currently unavailable.", "The reading engine is not installed or is switched off.",
             "Fill the form by hand and tell ICT."],
            ["Suggested date is empty or marked ambiguous.", "The format could be read two ways.",
             "Type it as 05 Jan 2026 or 2026-01-05."],
            ["A document vanished from the list.", "It may be archived, or a filter is on.",
             "Clear the filters, or click View Archived."],
            ["The status did not change after I edited.", "Status follows the expiry date.",
             "Check the expiry date and the No expiry box."],
            ["The employee gets no alerts.", "Both an email and \"Send alerts to this employee\" are needed.",
             "Edit the employee and set both."],
            ["Import row failed: unknown factory, company or department.", "Names must match existing records.",
             "Fix the spelling in the sheet, or add the record first."],
        ],
        [58 * mm, 52 * mm, W - 110 * mm]))

    story.append(H2("8.2 Common questions"))
    qa = [
        ("Do I need to click anything for the reminders to go out?",
         "No. Weekly and urgent emails are automatic. Send alert is only for an extra, one-off message."),
        ("Why did I get five emails today?",
         "Each email covers one record. Five expiring records give five emails."),
        ("Can I turn off an email for myself?",
         "Not personally. Ask an administrator to remove you from the record, department or company recipients."),
        ("I entered the wrong date. What now?",
         "Open the record, Edit, correct it and Save. The status and future alerts update straight away."),
        ("Can I undo a delete?",
         "No. An archived document can be restored, but a deleted one cannot, which is why Delete asks you to type the name."),
        ("Why was I signed out?",
         "You clicked Logout, or you closed the browser. Nothing signs you out while the browser stays open."),
        ("What time zone is used?",
         "Bangladesh Standard Time (UTC+6) for statuses, schedules and every time you see."),
        ("What happens to a document's old files after a renewal?",
         "They stay. Open the document and use the Files list."),
        ("Who can see my documents?",
         "People whose role covers your factory, and administrators. Other factories cannot."),
        ("Where do I get help?",
         "Your Factory Admin for access and roles. The ICT team for anything technical: a locked account that stays "
         "locked, an email that Alert History shows as Failed, or a page that will not load."),
    ]
    for q, a in qa:
        story.append(K.Stack([P("<b>%s</b>" % q, "BodyTight"), P(a, "Body")]))

    # ------------------------------------------------------------ 9 Quick reference
    story += H1("9. Quick reference")
    story.append(H2("Emails at a glance"))
    story.append(table(
        ["Email", "When", "Records", "Subject starts with"],
        [
            ["Weekly reminder", "Saturday 09:00", "90, 60, 30 days left", "Compliance Alert:"],
            ["Urgent alert", "Daily 09:00", "7 days left, Expired", "URGENT:"],
            ["Manual alert", "When sent", "One document", "Compliance Notice:"],
        ],
        [36 * mm, 40 * mm, 48 * mm, W - 124 * mm], first_bold=True))
    story.append(H2("Files"))
    story.append(table(
        ["Rule", "Value"],
        [
            ["Allowed types", "PDF, JPG, JPEG, PNG, DOCX, XLSX"],
            ["Largest file", "10 MB"],
            ["OCR (reading a file)", "PDF, JPG, PNG. Up to 10 MB and 50 pages"],
            ["Excel import", ".xlsx only, Super Admin"],
        ],
        [50 * mm, W - 50 * mm], first_bold=True))
    story.append(H2("Where do I click?"))
    story.append(table(
        ["To", "Go to"],
        [
            ["Add a document", "Documents, then + Add Document"],
            ["Renew a document", "Documents, then Renew on the row"],
            ["Archive or restore a document", "Documents, then Archive; View Archived, then Restore"],
            ["Email someone now", "Open the document, then Send alert"],
            ["Add a license or checkup", "Employee Compliance, open the employee, then + Add License or + Add Checkup"],
            ["See my responsibilities", "My Alerts"],
            ["See what was sent", "Alert History"],
            ["Change who gets alerts", "Dept. Alert Recipients, Companies or the record's own form"],
        ],
        [60 * mm, W - 60 * mm], first_bold=True))

    # ------------------------------------------------------------ 10 Glossary
    story += H1("10. Glossary")
    story.append(table(
        ["Term", "Meaning"],
        [
            ["Alert", "An email the system sends about one record."],
            ["Archive", "Hide a record from active lists while keeping its files. Documents can be restored."],
            ["BST", "Bangladesh Standard Time, UTC+6. All dates and schedules use it."],
            ["Department", "A department of the company, shared by all factories, such as Compliance or Transport."],
            ["Global Access", "A user account with no factory. Used by Super Admin and similar roles."],
            ["Group mailbox", "A shared email address set on a department that always receives its alerts."],
            ["OCR", "Reading text from a scanned file so the system can suggest field values."],
            ["Recipient", "A person who is emailed for a record."],
            ["Renew", "Record a new issue and expiry date with a new file, keeping the old files."],
            ["Role", "The set of things an account may do."],
            ["Status", "Valid, Expiring in 90/60/30/7 Days, or Expired. Worked out from the expiry date."],
            ["Version", "One uploaded file of a record. Renewing adds a version."],
        ],
        [36 * mm, W - 36 * mm], first_bold=True))
    story.append(Spacer(1, 8))
    story.append(P(
        "This manual matches DocMan Alert as of %s. If a screen on your computer looks different, ICT may have "
        "updated the system; the ideas in this manual still apply." % EDITION, "Small"))

    doc.multiBuild(story)
    return OUT


if __name__ == "__main__":
    print(build())








