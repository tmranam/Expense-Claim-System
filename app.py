import streamlit as st
import re
import json
import os
import io
import uuid
import copy
import time
import hmac
import hashlib
import shutil
import base64
import csv
from datetime import datetime, date, timedelta
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps
import pytesseract
import numpy as np
import cv2
import pandas as pd
try:                                    # lets phones that send HEIC/HEIF photos upload them
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
import pypdf
from pdf2image import convert_from_bytes
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

# -------------------------------------------------------------------------
# DEFENSIVE LINUX PATH ANCHORING FOR STREAMLIT CLOUD INFRASTRUCTURE
# -------------------------------------------------------------------------
detected_path = shutil.which("tesseract")
if detected_path:
    pytesseract.pytesseract.tesseract_cmd = detected_path
else:
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

CONFIG_FILE = "admin_config.json"
LOCAL_TZ = ZoneInfo("Australia/Sydney")

STATUS_PENDING = "Pending"
STATUS_HOLD = "On Hold"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_BYPASSED = "Bypassed"      # stage skipped (e.g. Admin skipped the Nazim e Ala)

ROLE_ADMIN = "admin"
ROLE_NAZIM = "nazim"
ROLE_PRESIDENT = "president"

REGISTER_NAME = "claims_register.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def now_local():
    return datetime.now(LOCAL_TZ)


DEFAULT_CONFIG = {
    "regions": ["National", "Region North", "Region South"],
    "regional_majlis": {
        "National": ["Head Office", "Finance Dept"],
        "Region North": ["Youth Majlis North", "Local Chapter A"],
        "Region South": ["Youth Majlis South", "Local Chapter B"]
    },
    "regional_accounts": {
        "National": [
            "NAT-1000 - National HQ Operations",
            "NAT-2000 - Central Tabligh Campaigns",
            "NAT-3000 - Australia-Wide Tarbiyat Initiatives",
            "NAT-4000 - Head Office Administrative Overhead"
        ],
        "General_Regional": [
            "REG-5000 - Local Majlis Operational Funds",
            "REG-6000 - Regional ljtema Expense Allocation",
            "REG-7000 - Local Welfare & Khidmat-e-Khalq",
            "REG-8000 - Regional Sports & Local Youth Engagement"
        ]
    },
    "drive_folder_link": "",
    "roles": {
        "president": {"username": "President", "salt": "", "hash": ""},
        "nazims": {}
    },
    "emails": ["finance@example.com", "approvals@example.com"],
    "admin_password": "123",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "claimsubmissionmaaa@gmail.com",
    "smtp_password": "",
    "qaid_mal_name": "Tahir Mahmood Rana",
    "sadr_maa_name": "Abdul Jamil Mubbashar",
    "regional_nazims": {
        "National": {"name": "National Representative", "email": "nationalnazim@example.com"},
        "Region North": {"name": "Nazim North", "email": "northnazim@example.com"},
        "Region South": {"name": "Nazim South", "email": "southnazim@example.com"}
    }
}

# -------------------------------------------------------------------------
# SETTINGS STORAGE
# -------------------------------------------------------------------------
def load_admin_settings():
    """Loads admin configurations from a local JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key not in data:
                        data[key] = copy.deepcopy(DEFAULT_CONFIG[key])
                    elif not data[key] and key != "drive_folder_link":
                        data[key] = copy.deepcopy(DEFAULT_CONFIG[key])
                return data
        except Exception:
            return copy.deepcopy(DEFAULT_CONFIG)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_admin_settings(config_data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)


if 'admin_lists' not in st.session_state:
    st.session_state.admin_lists = load_admin_settings()

# -------------------------------------------------------------------------
# GOOGLE DRIVE LAYER (PDF storage + claims register)
# -------------------------------------------------------------------------
def _secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return None


def extract_folder_id(link_or_id):
    """Accepts a Drive folder URL or a bare folder ID and returns the ID."""
    text = (link_or_id or "").strip()
    if not text:
        return None
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", text) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    return None


def get_drive_folder_id():
    link = st.session_state.admin_lists.get("drive_folder_link", "") or _secret("drive_folder_link") or ""
    return extract_folder_id(link)


def get_service_account_email():
    info = _secret("gcp_service_account")
    try:
        return info["client_email"] if info else None
    except Exception:
        return None


def get_drive_auth_mode():
    """'oauth' = saves as the admin's own Google account (recommended), 'service_account' = fallback."""
    if _secret("google_oauth"):
        return "oauth"
    if _secret("gcp_service_account"):
        return "service_account"
    return None


def get_drive_service():
    oauth = _secret("google_oauth")
    if oauth:
        creds = OAuthCredentials(
            token=None,
            refresh_token=oauth["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth["client_id"],
            client_secret=oauth["client_secret"],
            scopes=DRIVE_SCOPES,
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    info = _secret("gcp_service_account")
    if not info:
        return None
    creds = service_account.Credentials.from_service_account_info(dict(info), scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_pdf_to_drive(service, folder_id, filename, pdf_bytes):
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    meta = {"name": filename, "parents": [folder_id]}
    created = service.files().create(
        body=meta, media_body=media, fields="id, webViewLink", supportsAllDrives=True
    ).execute()
    return created["id"], created["webViewLink"]


def _find_register_id(service, folder_id):
    q = f"name = '{REGISTER_NAME}' and '{folder_id}' in parents and trashed = false"
    res = service.files().list(
        q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def load_register(service, folder_id):
    """Returns (list_of_claims, register_file_id_or_None)."""
    fid = _find_register_id(service, folder_id)
    if not fid:
        return [], None
    raw = service.files().get_media(fileId=fid, supportsAllDrives=True).execute()
    return [normalize_claim(c) for c in json.loads(raw.decode("utf-8"))], fid


def save_register(service, folder_id, claims, fid):
    body_bytes = json.dumps(claims, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(body_bytes), mimetype="application/json", resumable=False)
    if fid:
        service.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
    else:
        service.files().create(
            body={"name": REGISTER_NAME, "parents": [folder_id]},
            media_body=media, fields="id", supportsAllDrives=True
        ).execute()


def add_claim_to_register(service, folder_id, record):
    claims, fid = load_register(service, folder_id)
    claims.append(record)
    save_register(service, folder_id, claims, fid)


def stamp_voucher_number(pdf_bytes, voucher_no):
    """Writes the voucher number into the 'Voucher No:' cell on page 1 of the claim PDF.
    Any number stamped earlier is covered first, so the number can be changed later."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    first_page = reader.pages[0]

    found = {}

    def visitor(text, cm, tm, font_dict, font_size):
        if "Voucher No" in text and "pos" not in found:
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            found["pos"] = (x, y)

    first_page.extract_text(visitor_text=visitor)
    if "pos" not in found:
        raise RuntimeError("Could not find the 'Voucher No:' box on the PDF.")

    label_x, base_y = found["pos"]
    # Measured layout: label text sits 6pt inside its 100pt-wide cell; value cell is 170pt wide.
    # The row runs from base_y - 5 (bottom border) to base_y + 12 (top border).
    cell_left = label_x - 6 + 100
    cell_right = cell_left + 170
    row_bottom, row_top = base_y - 5, base_y + 12
    page_w = float(first_page.mediabox.width)
    page_h = float(first_page.mediabox.height)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    # Cover only the text area, staying clear of the borders
    c.setFillColor(colors.white)
    c.rect(cell_left + 3, base_y - 4, 170 - 6, 14, stroke=0, fill=1)
    if voucher_no:
        font_size = 10
        while font_size > 6 and pdfmetrics.stringWidth(voucher_no, "Helvetica-Bold", font_size) > 158:
            font_size -= 0.5
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", font_size)
        c.drawString(cell_left + 6, base_y - 1.5, voucher_no)
    # Redraw the cell borders so any earlier stamp that covered them is repaired
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(cell_left, row_bottom, cell_right, row_bottom)
    c.line(cell_left, row_top, cell_right, row_top)
    c.line(cell_right, row_bottom, cell_right, row_top)
    c.showPage()
    c.save()
    buf.seek(0)

    first_page.merge_page(PdfReader(buf).pages[0])

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def update_pdf_in_drive(service, file_id, pdf_bytes, new_name=None):
    """Replaces the PDF in Drive (same file, same link). When new_name is given the file is renamed too."""
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    kwargs = {"fileId": file_id, "media_body": media, "supportsAllDrives": True}
    if new_name:
        kwargs["body"] = {"name": new_name}
    service.files().update(**kwargs).execute()


def _fn_part(text, limit):
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limit].strip()


def build_drive_filename(voucher_no, name, event, submitted_on):
    """Drive file name:  VoucherNo_Claimant name_Description_Date.pdf
    (the voucher part is left out until the Admin has entered a Voucher No.)"""
    day = re.sub(r"[^0-9]+", "-", str(submitted_on or "")).strip("-") or now_local().strftime("%d-%m-%Y")
    parts = [_fn_part(voucher_no, 20), _fn_part(name, 40), _fn_part(event, 60), day]
    return "_".join(p for p in parts if p) + ".pdf"


# ---- members list (auto-fill of the claim form) ---------------------------------------------
MEMBERS_NAME = "members.csv"


def _norm_name(text):
    return " ".join(str(text or "").lower().split())


def load_members(service, folder_id):
    """Rows of members.csv in the Drive folder (columns: Name, Email, Region, Majlis, optional Account)."""
    fid = _find_file_id(service, folder_id, MEMBERS_NAME)
    if not fid:
        return []
    raw = service.files().get_media(fileId=fid, supportsAllDrives=True).execute()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]


def members_csv_from_upload(filename, data):
    """Turns an uploaded .csv or .xlsx into CSV bytes. Must have a Name column."""
    if filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
        data = df.to_csv(index=False).encode("utf-8")
    first_line = data.decode("utf-8-sig", errors="replace").split("\n")[0].lower()
    headers = [h.strip().strip('"').strip() for h in first_line.split(",")]
    if "name" not in headers:
        raise ValueError("The first row must contain column headings, including one called Name.")
    return data


def save_members(service, folder_id, data):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="text/csv", resumable=False)
    fid = _find_file_id(service, folder_id, MEMBERS_NAME)
    if fid:
        service.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
    else:
        service.files().create(body={"name": MEMBERS_NAME, "parents": [folder_id]},
                               media_body=media, fields="id", supportsAllDrives=True).execute()


def lookup_person(name, members, claims):
    """Email, region, majlis and account for a name: from the members list first, then from that person's
    most recent earlier claim. Bank details are never returned."""
    key = _norm_name(name)
    if len(key) < 3:
        return {}
    info = {}
    for m in members:
        if _norm_name(m.get("name")) == key:
            info = {"email": m.get("email", ""), "region": m.get("region", ""), "majlis": m.get("majlis", ""),
                    "account": m.get("account", "") or m.get("account code", "")}
            break
    mine = sorted((c for c in claims if _norm_name(c.get("name")) == key),
                  key=lambda c: c.get("submitted_ts", ""), reverse=True)
    if mine:
        c = mine[0]
        for k, v in (("email", c.get("claimant_email")), ("region", c.get("region")),
                     ("majlis", c.get("majlis")), ("account", c.get("account"))):
            if not info.get(k) and v:
                info[k] = v
    return {k: v for k, v in info.items() if v}


# -------------------------------------------------------------------------
# APPROVAL WORKFLOW:  Nazim e Ala  ->  Admin  ->  President
# Each claim carries one status per stage: nazim_status, admin_status, president_status
# -------------------------------------------------------------------------
def _stage_done(status):
    return status in (STATUS_APPROVED, STATUS_BYPASSED)


def normalize_claim(c):
    """Fills in workflow fields. Claims saved before roles existed skip the Nazim stage."""
    c.setdefault("voucher_no", "")
    if "nazim_status" not in c:
        legacy = c.get("status", STATUS_PENDING)
        c["nazim_status"] = STATUS_BYPASSED
        c["admin_status"] = legacy if legacy in (STATUS_PENDING, STATUS_HOLD, STATUS_APPROVED, STATUS_REJECTED) else STATUS_PENDING
        c["president_status"] = STATUS_BYPASSED if legacy == STATUS_APPROVED else STATUS_PENDING
    c.setdefault("admin_status", STATUS_PENDING)
    c.setdefault("president_status", STATUS_PENDING)
    c.setdefault("paid", False)
    c.setdefault("complete", False)  # Admin ticks this when nothing more is needed on the claim
    c.setdefault("history", [])
    c.setdefault("comments", [])
    c.setdefault("receipts", [])     # receipt lines + fingerprints (claims saved from now on)
    c.setdefault("flags", [])        # possible duplicate receipts found at submission
    return c


def _claim_stage_core(c):
    """Returns (stage_key, human readable status) for a claim."""
    if c.get("merged_into"):
        return "merged", "Merged into a consolidated claim"
    n, a, p = c["nazim_status"], c["admin_status"], c["president_status"]
    nazim = f"Nazim e Ala {c.get('region', '')}".strip()

    if n == STATUS_REJECTED:
        return "rejected", f"Rejected by {nazim}"
    if n == STATUS_HOLD:
        return "hold", f"On hold by {nazim}"
    if n == STATUS_PENDING:
        return "await_nazim", f"Waiting for {nazim}"

    if a == STATUS_REJECTED:
        return "rejected", "Rejected by Admin"
    if a == STATUS_HOLD:
        return "hold", "On hold by Admin"
    if a == STATUS_PENDING:
        if n == STATUS_APPROVED:
            return "to_process", f"Approved by {nazim}"
        return "to_process", "Nazim e Ala skipped by Admin" if c.get("nazim_by") == "Admin" else "Ready for Admin"

    if p == STATUS_REJECTED:
        return "rejected", "Rejected by President"
    if p == STATUS_HOLD:
        return "hold", "On hold by President"
    if p == STATUS_PENDING:
        return "await_president", "Waiting approval from President"
    if c.get("paid"):
        return "paid", f"Paid on {c.get('paid_on', '')}".strip()
    if p == STATUS_BYPASSED:
        return "approved", "President skipped by Admin"
    return "approved", "Approved by President" if p == STATUS_APPROVED else "Approved by Admin"


def claim_stage(c):
    """(stage_key, status text). A claim the Admin has not ticked 'complete' is an open claim: it keeps moving through
    approval and payment as usual, and its status text says so, e.g. 'Open claim (Waiting approval from President)'."""
    key, text = _claim_stage_core(c)
    if not c.get("complete") and key not in ("rejected", "merged"):
        text = f"Open claim ({text})"
    return key, text


def actions_for(role, c):
    """Buttons a role may use on a claim, as (label, status_field, new_value)."""
    stage, _ = claim_stage(c)
    full = lambda f: [("Approve", f, STATUS_APPROVED), ("Reject", f, STATUS_REJECTED), ("Hold", f, STATUS_HOLD)]
    after_hold = lambda f: [("Approve", f, STATUS_APPROVED), ("Reject", f, STATUS_REJECTED)]

    if role == ROLE_NAZIM:
        if stage == "await_nazim":
            return full("nazim_status")
        if stage == "hold" and c["nazim_status"] == STATUS_HOLD:
            return after_hold("nazim_status")
        return []

    if role == ROLE_PRESIDENT:
        if stage == "await_president":
            return full("president_status")
        if stage == "hold" and c["president_status"] == STATUS_HOLD:
            return after_hold("president_status")
        return []

    # Admin
    if stage == "await_nazim":
        return [("Skip Nazim", "nazim_status", STATUS_BYPASSED)]
    if stage == "to_process":
        return full("admin_status")
    if stage == "hold":
        if c["nazim_status"] == STATUS_HOLD:
            return [("Skip Nazim", "nazim_status", STATUS_BYPASSED)]
        if c["admin_status"] == STATUS_HOLD:
            return after_hold("admin_status")
        if c["president_status"] == STATUS_HOLD:
            return [("Skip President", "president_status", STATUS_BYPASSED)]
        return []
    if stage == "await_president":
        return [("Skip President", "president_status", STATUS_BYPASSED), ("Recall", "admin_status", STATUS_PENDING)]
    if stage == "approved":
        return [("Mark Paid", "paid", True), ("Reopen", "president_status", STATUS_PENDING)]
    if stage == "paid":
        return [("Undo Paid", "paid", False)]
    if stage == "rejected":
        for field in ("nazim_status", "admin_status", "president_status"):
            if c[field] == STATUS_REJECTED:
                return [("Reopen", field, STATUS_PENDING)]
    return []


MAX_COMMENT_LEN = 1000
VOUCHER_END_MARKER = "Hand written Receipt approved"


def _log(claim, actor, text):
    """Adds one line to the claim's history."""
    claim.setdefault("history", []).append(
        {"when": now_local().strftime('%d/%m/%Y %H:%M'), "by": actor, "what": str(text).replace("\n", " ")}
    )


def _change_text(field, value):
    if field == "paid":
        return "Marked as paid" if value else "Payment mark removed (back to Approved)"
    if field == "complete":
        return "Marked as complete (removed from Open Claims)" if value else "Marked as not complete (back in Open Claims)"
    who = {"nazim_status": "Nazim e Ala", "admin_status": "Admin", "president_status": "President"}.get(field, field)
    if value == STATUS_BYPASSED:
        return "President stage skipped (approved by Admin without the President)" if field == "president_status" \
            else "Nazim e Ala stage skipped"
    if value == STATUS_PENDING:
        return f"{who} decision reopened"
    if field == "admin_status" and value == STATUS_APPROVED:
        return "Approved by Admin and sent to President"
    return f"{value} by {who}"


def can_view_claim(role, claim, region=None):
    """Who is allowed to see (and comment on) a claim."""
    if role == ROLE_ADMIN:
        return True
    if role == ROLE_NAZIM:
        return bool(region) and claim.get("region") == region
    if role == ROLE_PRESIDENT:
        return _stage_done(claim["nazim_status"]) and _stage_done(claim["admin_status"])
    return False


def _add_comment(claim, actor, role, text, decision=""):
    """Stores a comment on the claim (visible to all three roles) and notes it in the history."""
    text = (text or "").strip()[:MAX_COMMENT_LEN]
    if not text:
        return False
    claim.setdefault("comments", []).append({
        "when": now_local().strftime('%d/%m/%Y %H:%M'), "by": actor, "role": role,
        "text": text, "decision": decision,
    })
    short = text.replace("\n", " ")
    _log(claim, actor, f'Comment: "{short[:150]}{"…" if len(short) > 150 else ""}"')
    return True


def update_claim(service, folder_id, claim_id, role, actor, changes=None, voucher_no=None, region=None, comment=None,
                 fy=None):
    """Applies a status change, a voucher number and/or a comment to one claim, after checking the role is allowed.
    changes = {"nazim_status": "Approved"} etc. Every change is written to the claim's history, and a comment
    typed together with a decision is saved with that decision.
    Returns a warning message ('' if all fine)."""
    changes = changes or {}
    fy = None if fy is None or role != ROLE_ADMIN else str(fy).strip()
    if fy is not None and not re.fullmatch(r"20\d{2}", fy):
        raise ValueError("Choose a valid year (for example 2026).")
    claims, fid = load_register(service, folder_id)
    claim = next((c for c in claims if c["id"] == claim_id), None)
    if claim is None:
        raise ValueError("That claim was not found. Please refresh the page.")

    if not can_view_claim(role, claim, region):
        raise PermissionError("You are not allowed to change this claim.")

    allowed_fields = {
        ROLE_NAZIM: {"nazim_status"},
        ROLE_PRESIDENT: {"president_status"},
        ROLE_ADMIN: {"nazim_status", "admin_status", "president_status", "paid", "complete"},
    }.get(role, set())

    # The voucher number the claim will have once this request is saved
    if voucher_no is not None and role == ROLE_ADMIN:
        effective_voucher = voucher_no.strip()[:20]
    else:
        effective_voucher = (claim.get("voucher_no") or "").strip()

    for field, value in changes.items():
        if field not in allowed_fields:
            raise PermissionError("You are not allowed to make that change.")
        if field == "complete":
            continue
        if role == ROLE_ADMIN and field == "admin_status" and value == STATUS_APPROVED and not effective_voucher:
            raise PermissionError("Enter a Voucher No. before approving this claim.")
        if role == ROLE_ADMIN and field == "president_status" and value == STATUS_BYPASSED:
            if not (_stage_done(claim["nazim_status"]) and claim["admin_status"] == STATUS_APPROVED
                    and claim["president_status"] in (STATUS_PENDING, STATUS_HOLD)):
                raise PermissionError("The President stage can only be skipped while the claim is waiting for the President. Please refresh.")
            continue
        if field == "paid":
            if value and claim_stage(claim)[0] != "approved":
                raise PermissionError("Only approved claims can be marked as paid. Please refresh.")
            if not value and not claim.get("paid"):
                raise PermissionError("This claim is not marked as paid. Please refresh.")
            continue
        if role != ROLE_ADMIN and value not in (STATUS_APPROVED, STATUS_REJECTED, STATUS_HOLD):
            raise PermissionError("You are not allowed to make that change.")
        # Make sure the claim is still waiting on this person (the page may be out of date)
        if role == ROLE_NAZIM and claim["nazim_status"] not in (STATUS_PENDING, STATUS_HOLD):
            raise PermissionError("This claim is no longer waiting for your decision. Please refresh.")
        if role == ROLE_PRESIDENT and claim["president_status"] not in (STATUS_PENDING, STATUS_HOLD):
            raise PermissionError("This claim is no longer waiting for your decision. Please refresh.")
        if role == ROLE_ADMIN and field == "admin_status" and value != STATUS_PENDING and not _stage_done(claim["nazim_status"]):
            raise PermissionError("This claim is still with the Nazim e Ala.")

    changed = False
    warning = ""

    # Voucher number first, so an approval is never saved without the number being on the PDF
    if voucher_no is not None and role == ROLE_ADMIN:
        new_no = effective_voucher
        old_no = claim.get("voucher_no", "")
        if new_no != old_no:
            if not new_no and "admin_status" not in changes and claim.get("admin_status") == STATUS_APPROVED:
                raise PermissionError("The Voucher No. cannot be removed from a claim the Admin has approved. "
                                      "Recall or reopen the claim first.")
            try:
                pdf_bytes = service.files().get_media(fileId=claim["file_id"], supportsAllDrives=True).execute()
                new_file_name = build_drive_filename(new_no, claim.get("name"), claim.get("event"), claim.get("submitted_on"))
                update_pdf_in_drive(service, claim["file_id"], stamp_voucher_number(pdf_bytes, new_no), new_file_name)
                claim["file_name"] = new_file_name
                claim["voucher_no"] = new_no
                if not old_no:
                    _log(claim, actor, f"Voucher No. set to {new_no}")
                elif not new_no:
                    _log(claim, actor, f"Voucher No. {old_no} removed")
                else:
                    _log(claim, actor, f"Voucher No. changed from {old_no} to {new_no}")
                changed = True
            except Exception as e:
                if changes.get("admin_status") == STATUS_APPROVED:
                    raise ValueError(f"Not approved: the voucher number could not be written onto the PDF in Drive ({e}).")
                warning = f"The voucher number could not be written onto the PDF in Drive ({e})."

    now = now_local()
    today = now.strftime('%d/%m/%Y')
    labels = []
    if fy is not None and str(claim.get("fy") or "") != fy:
        old_fy = str(claim.get("fy") or "")
        claim["fy"] = fy
        _log(claim, actor, f"Year set to {fy}" if not old_fy else f"Year changed from {old_fy} to {fy}")
        changed = True
    for field, value in changes.items():
        if field == "paid":
            claim["paid"] = bool(value)
            claim["paid_by"] = actor if value else ""
            claim["paid_on"] = today if value else ""
            claim["paid_ts"] = now.isoformat() if value else ""
        elif field == "complete":
            claim["complete"] = bool(value)
            claim["complete_by"] = actor if value else ""
            claim["complete_on"] = today if value else ""
        else:
            stage = field.replace("_status", "")
            claim[field] = value
            claim[f"{stage}_by"] = actor if value != STATUS_PENDING else ""
            claim[f"{stage}_on"] = today if value != STATUS_PENDING else ""
        text = _change_text(field, value)
        labels.append(text)
        _log(claim, actor, text)
        changed = True

    if _add_comment(claim, actor, role, comment, decision="; ".join(labels)):
        changed = True

    if changed:
        save_register(service, folder_id, claims, fid)
    return warning


def delete_claim(service, folder_id, claim_id, role):
    """Admin only. Moves the claim's PDF to the Google Drive Trash and removes the claim from the register."""
    if role != ROLE_ADMIN:
        raise PermissionError("Only the Admin can delete claims.")
    claims, fid = load_register(service, folder_id)
    claim = next((c for c in claims if c["id"] == claim_id), None)
    if claim is None:
        raise ValueError("That claim was not found. Please refresh the page.")
    try:
        service.files().update(fileId=claim["file_id"], body={"trashed": True}, supportsAllDrives=True).execute()
    except HttpError as e:
        if getattr(e.resp, "status", None) != 404:      # 404 = the file is already gone, carry on
            raise
    save_register(service, folder_id, [c for c in claims if c["id"] != claim_id], fid)


# -------------------------------------------------------------------------
# AMEND PDF (Admin): rebuild the voucher page(s) of a submitted claim, keep the receipt pages
# -------------------------------------------------------------------------
def complete_claims(service, folder_id, claim_ids, role, actor):
    """Admin only. Marks several claims as complete in one go (they leave Open Claims). Returns how many changed."""
    if role != ROLE_ADMIN:
        raise PermissionError("Only the Admin can mark claims as complete.")
    claims, fid = load_register(service, folder_id)
    wanted = set(claim_ids)
    n = 0
    for c in claims:
        if c["id"] in wanted and not c.get("complete") and claim_stage(c)[0] not in ("rejected", "merged"):
            c["complete"] = True
            c["complete_by"] = actor
            c["complete_on"] = now_local().strftime('%d/%m/%Y')
            _log(c, actor, _change_text("complete", True))
            n += 1
    if n:
        save_register(service, folder_id, claims, fid)
    return n


def merge_claims(service, folder_id, claim_ids, role, actor, event, voucher_no="", keep_first_pages=True):
    """Admin only. Merges several OPEN claims of one claimant (same event) into one consolidated claim:
    page 1 = a voucher page listing every receipt line of every claim, followed by each claim's pages.
    The merged PDF is saved in Drive as VoucherNo_Claimant_Event_Date.pdf and becomes a new claim waiting for the
    Admin to approve; the original claims are marked as merged and leave the approval flow."""
    if role != ROLE_ADMIN:
        raise PermissionError("Only the Admin can merge claims.")
    event = (event or "").strip()
    voucher_no = (voucher_no or "").strip()[:20]
    ids = list(dict.fromkeys(claim_ids))
    if not event:
        raise ValueError("Enter the event description for the merged claim.")
    if len(ids) < 2:
        raise ValueError("Select at least two claims to merge.")

    claims, fid = load_register(service, folder_id)
    picked = [c for c in claims if c["id"] in ids]
    if len(picked) != len(ids):
        raise ValueError("One of the selected claims was not found. Please refresh and select again.")
    picked.sort(key=lambda c: c.get("submitted_ts", ""))
    for c in picked:
        if claim_stage(c)[0] != "to_process" or c.get("complete"):
            raise ValueError(f"The claim from {c.get('name', '')} ({c.get('event', '')}) is no longer an open claim. Please refresh.")
    for field, label in (("name", "claimants"), ("region", "regions"), ("majlis", "majlis"), ("account", "account codes"),
                         ("bsb", "BSB numbers"), ("acc_num", "account numbers")):
        if len({_norm_name(c.get(field)) for c in picked}) > 1:
            raise ValueError(f"These claims have different {label}, so they cannot be merged. Use Amend PDF to make them match first.")

    first = picked[0]
    lines = []
    for c in picked:
        recs = c.get("receipts") or []
        if recs:
            lines += [dict(r) for r in recs]
        else:       # claims saved before receipt lines were stored: one line with the claim totals
            lines.append({"date": c.get("submitted_on", ""), "desc": c.get("event", ""),
                          "amount": round(float(c.get("total_claim") or 0), 2), "gst": round(float(c.get("gst") or 0), 2)})
    total = round(sum(r["amount"] for r in lines), 2)
    gst_total = round(sum(r["gst"] for r in lines), 2)
    advance = round(sum(float(c.get("advance") or 0) for c in picked), 2)
    if advance > total + 0.001:
        raise ValueError("The advances on these claims add up to more than their total.")

    form = {"name": first.get("name", ""), "region": first.get("region", ""), "majlis": first.get("majlis", ""),
            "account": first.get("account", ""), "event": event, "bsb": first.get("bsb", ""),
            "acc_num": first.get("acc_num", ""), "advance": advance}
    vbuf = io.BytesIO()
    generate_official_voucher_pdf(vbuf, build_voucher_payload(form, lines), lines)
    cons_bytes = vbuf.getvalue()
    if voucher_no:
        cons_bytes = stamp_voucher_number(cons_bytes, voucher_no)
    cons_reader = PdfReader(io.BytesIO(cons_bytes))

    writer = PdfWriter()
    for page in cons_reader.pages:
        writer.add_page(page)
    for c in picked:
        data = service.files().get_media(fileId=c["file_id"], supportsAllDrives=True).execute()
        reader = PdfReader(io.BytesIO(data))
        skip = 0 if keep_first_pages else _voucher_page_count(reader, c.get("voucher_pages", 1))
        for page in reader.pages[skip:]:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)

    now = now_local()
    drive_name = build_drive_filename(voucher_no, first.get("name"), event, now.strftime("%d/%m/%Y"))
    new_file_id, new_link = upload_pdf_to_drive(service, folder_id, drive_name, out.getvalue())
    new_id = uuid.uuid4().hex[:10]
    record = {
        "id": new_id, "submitted_on": now.strftime("%d/%m/%Y"), "submitted_ts": now.isoformat(),
        "name": first.get("name", ""), "claimant_email": first.get("claimant_email", ""), "event": event,
        "region": first.get("region", ""), "majlis": first.get("majlis", ""), "account": first.get("account", ""),
        "bsb": first.get("bsb", ""), "acc_num": first.get("acc_num", ""),
        "total_claim": total, "gst": gst_total, "advance": advance, "amount_payable": round(total - advance, 2),
        "file_name": drive_name, "file_id": new_file_id, "file_link": new_link, "voucher_no": voucher_no,
        "voucher_pages": len(cons_reader.pages), "receipts": lines, "flags": [], "comments": [], "paid": False,
        "complete": True, "merged_from": [c["id"] for c in picked],
        "nazim_status": STATUS_BYPASSED, "nazim_by": "Merged", "admin_status": STATUS_PENDING,
        "president_status": STATUS_PENDING, "history": [],
    }
    _log(record, actor, f"Consolidated claim created by merging {len(picked)} open claims "
                        f"({len(lines)} receipt line{'s' if len(lines) != 1 else ''}, ${total:,.2f})")
    for c in picked:
        c["merged_into"] = new_id
        c["complete"] = True
        _log(c, actor, f"Merged into consolidated claim {voucher_no or new_id}")
    claims.append(record)
    save_register(service, folder_id, claims, fid)
    return record


def build_voucher_payload(form, receipts, request_date=None):
    """The dictionary generate_official_voucher_pdf() needs, built from the claim form fields."""
    cfg = st.session_state.admin_lists
    nazim_info = cfg.get("regional_nazims", {}).get(form["region"], {"name": "Not Configured", "email": ""})
    return {
        "name": form["name"],
        "region": form["region"],
        "majlis": form["majlis"],
        "account": form["account"],
        "expense_type": form["event"],
        "total_receipts": len(receipts),
        "total_amount": sum(r["amount"] for r in receipts),
        "total_gst": sum(r["gst"] for r in receipts),
        "bsb": form["bsb"],
        "acc_num": form["acc_num"],
        "advance": float(form.get("advance") or 0.0),
        "qaid_mal": cfg.get("qaid_mal_name", "Tahir Mahmood Rana"),
        "sadr_maa": cfg.get("sadr_maa_name", "Abdul Jamil Mubbashar"),
        "nazim_name": nazim_info.get("name", "Not Configured"),
        "request_date": request_date,
    }


def _voucher_page_count(reader, fallback=1):
    """How many pages at the start of a claim PDF belong to the voucher (the rest are receipts).
    The voucher always ends with the 'Hand written Receipt approved...' comments line."""
    for i, page in enumerate(reader.pages[:10]):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if VOUCHER_END_MARKER in text:
            return i + 1
    return max(1, int(fallback or 1))


def parse_voucher_fields(pdf_bytes):
    """Best-effort: reads the form fields and receipt lines back from page 1 of a voucher PDF.
    Used to prefill 'Amend PDF' for claims saved before the form fields were stored. Returns a dict or None."""
    try:
        text = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        labels = ["Payee Name:", "Voucher No:", "Expense Type:", "BSB:", "Account:", "Account No:",
                  "Region:", "Total Claim:", "Majlis:", "Advance Issued:"]

        def after(label):
            if label not in lines:
                return ""
            out = []
            for ln in lines[lines.index(label) + 1:]:
                if ln in labels or ln == "#":
                    break
                out.append(ln)
            return " ".join(out).strip()

        advance_txt = after("Advance Issued:").replace("$", "").replace(",", "").strip()
        try:
            advance = float(advance_txt) if advance_txt else 0.0
        except ValueError:
            advance = 0.0

        rows = []
        if "GST" in lines:
            i = lines.index("GST") + 1
            expected = 1
            money = re.compile(r"^-?\$\s?\d[\d,]*\.\d{2}$")
            while i < len(lines) and lines[i] not in ("Less: Advance Settled", "Total"):
                if lines[i] == str(expected):
                    j = i + 1
                    chunk = []
                    while j < len(lines) and not (lines[j] == str(expected + 1) and
                                                  (len(chunk) == 0 or (len(chunk) >= 2 and money.match(chunk[-1])))):
                        if lines[j] in ("Less: Advance Settled", "Total"):
                            break
                        chunk.append(lines[j])
                        j += 1
                    if len(chunk) >= 3 and money.match(chunk[-1]) and money.match(chunk[-2]):
                        gst_v = float(chunk[-1].replace("$", "").replace(",", ""))
                        amt_v = float(chunk[-2].replace("$", "").replace(",", ""))
                        body = chunk[:-2]
                        rows.append({"date": body[0] if body else "", "desc": " ".join(body[1:]),
                                     "amount": amt_v, "gst": gst_v})
                    expected += 1
                    i = j
                else:
                    i += 1
        return {
            "name": after("Payee Name:"), "event": after("Expense Type:"), "bsb": after("BSB:"),
            "account": after("Account:"), "acc_num": after("Account No:"), "region": after("Region:"),
            "majlis": after("Majlis:"), "advance": advance, "rows": rows,
        }
    except Exception:
        return None


def amend_claim(service, folder_id, claim_id, role, actor, form, rows, extra_files=None):
    """Admin only. Rebuilds the voucher page(s) of a claim's PDF from the corrected form fields and receipt lines,
    keeps the receipt pages (plus any extra files), re-stamps the voucher number, replaces the PDF in Drive
    (same file, same link) and updates the claim record and its history."""
    if role != ROLE_ADMIN:
        raise PermissionError("Only the Admin can amend a claim's PDF.")

    name = (form.get("name") or "").strip()
    event = (form.get("event") or "").strip()
    if not name or not event:
        raise ValueError("Name and event description are required.")
    if not rows:
        raise ValueError("At least one receipt line is needed.")

    clean = []
    for r in rows:
        amount = round(float(r.get("amount") or 0), 2)
        gst = round(float(r.get("gst") or 0), 2)
        if amount < 0 or gst < 0:
            raise ValueError("Amounts cannot be negative.")
        line = {"date": str(r.get("date", "")).strip(), "desc": str(r.get("desc", "")).strip(),
                "amount": amount, "gst": gst}
        for k in ("exact", "sig", "date_detected"):
            if k in r:
                line[k] = r[k]
        clean.append(line)
    total = round(sum(r["amount"] for r in clean), 2)
    gst_total = round(sum(r["gst"] for r in clean), 2)
    advance = round(float(form.get("advance") or 0), 2)
    if advance > total + 0.001:
        raise ValueError("The advance cannot be more than the total claim.")
    form = dict(form, name=name, event=event, advance=advance)

    claims, fid = load_register(service, folder_id)
    claim = next((c for c in claims if c["id"] == claim_id), None)
    if claim is None:
        raise ValueError("That claim was not found. Please refresh the page.")

    old_bytes = service.files().get_media(fileId=claim["file_id"], supportsAllDrives=True).execute()
    old_reader = PdfReader(io.BytesIO(old_bytes))
    n_old = _voucher_page_count(old_reader, claim.get("voucher_pages", 1))

    payload = build_voucher_payload(form, clean, request_date=claim.get("submitted_on"))
    voucher_buf = io.BytesIO()
    generate_official_voucher_pdf(voucher_buf, payload, clean)
    new_voucher = PdfReader(io.BytesIO(voucher_buf.getvalue()))

    writer = PdfWriter()
    for page in new_voucher.pages:
        writer.add_page(page)
    for page in old_reader.pages[n_old:]:
        writer.add_page(page)
    for k, (fname, data) in enumerate(extra_files or []):
        if os.path.splitext(fname)[1].lower() == ".pdf":
            extra_reader = PdfReader(io.BytesIO(data))
        else:
            extra_reader = PdfReader(_image_to_pdf_buffer(io.BytesIO(data), f"Added receipt {k + 1}"))
        for page in extra_reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    new_bytes = out.getvalue()

    if claim.get("voucher_no"):
        new_bytes = stamp_voucher_number(new_bytes, claim["voucher_no"])
    new_file_name = build_drive_filename(claim.get("voucher_no"), name, event, claim.get("submitted_on"))
    update_pdf_in_drive(service, claim["file_id"], new_bytes, new_file_name)

    # What changed (for the history)
    changed = []
    for key, label, new_val in [("name", "Name", name), ("event", "Event", event), ("region", "Region", form["region"]),
                                ("majlis", "Majlis", form["majlis"]), ("account", "Account", form["account"]),
                                ("claimant_email", "Email", form.get("claimant_email", "")),
                                ("bsb", "BSB", form["bsb"]), ("acc_num", "Account number", form["acc_num"])]:
        if (claim.get(key) or "") != (new_val or ""):
            changed.append(label)
    if round(float(claim.get("advance") or 0), 2) != advance:
        changed.append("Advance")
    old_lines = [(r.get("date", ""), r.get("desc", ""), round(r.get("amount", 0), 2), round(r.get("gst", 0), 2))
                 for r in claim.get("receipts", [])]
    new_lines = [(r["date"], r["desc"], r["amount"], r["gst"]) for r in clean]
    if old_lines != new_lines:
        changed.append("Receipt lines")
    if extra_files:
        changed.append(f"{len(extra_files)} receipt file(s) added")
    old_total = round(float(claim.get("total_claim") or 0), 2)
    if old_total != total:
        changed.append(f"Total ${old_total:.2f} to ${total:.2f}")

    claim.update({
        "name": name, "event": event, "region": form["region"], "majlis": form["majlis"],
        "account": form["account"], "claimant_email": form.get("claimant_email", ""),
        "bsb": form["bsb"], "acc_num": form["acc_num"], "advance": advance,
        "total_claim": total, "gst": gst_total, "amount_payable": round(total - advance, 2),
        "receipts": clean, "voucher_pages": len(new_voucher.pages), "file_name": new_file_name,
    })
    _log(claim, actor, "PDF amended by Admin" + (": " + ", ".join(changed) if changed else " (no field changes)"))
    save_register(service, folder_id, claims, fid)


# -------------------------------------------------------------------------
# ROLE LOGINS (passwords are stored as salted hashes, never as plain text)
# -------------------------------------------------------------------------
def hash_password(password, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, hash_hex):
    if not salt_hex or not hash_hex:
        return False
    _, digest = hash_password(password, salt_hex)
    return hmac.compare_digest(digest, hash_hex)


def get_roles_config():
    roles = st.session_state.admin_lists.get("roles") or {}
    roles.setdefault("president", {"username": "President", "salt": "", "hash": ""})
    roles.setdefault("nazims", {})
    return roles


def region_has_nazim_login(region):
    return bool((get_roles_config()["nazims"].get(region) or {}).get("hash"))


def authenticate(role, username, password):
    """Returns a session dict for a valid login, otherwise None."""
    if role == ROLE_ADMIN:
        if password == st.session_state.admin_lists.get("admin_password", "123"):
            return {"role": ROLE_ADMIN, "name": "Admin"}
        return None

    roles = get_roles_config()
    username = (username or "").strip().lower()
    if not username or not password:
        return None

    if role == ROLE_PRESIDENT:
        pres = roles["president"]
        if username == (pres.get("username") or "President").strip().lower() \
                and verify_password(password, pres.get("salt"), pres.get("hash")):
            return {"role": ROLE_PRESIDENT, "name": "President"}
        return None

    for region, info in roles["nazims"].items():
        if region not in st.session_state.admin_lists.get("regions", []):
            continue
        if username == (info.get("username") or region).strip().lower() \
                and verify_password(password, info.get("salt"), info.get("hash")):
            return {"role": ROLE_NAZIM, "name": f"Nazim e Ala {region}", "region": region}
    return None

# -------------------------------------------------------------------------
# OCR ENGINE FOR IMAGE & PDF RECEIPTS (one receipt per image, or one per PDF page)
# -------------------------------------------------------------------------
MAX_PDF_PAGES = 80
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
AMOUNT_RE = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)[.,](\d{2})(?!\d|[.,]\d)')
KNOWN_VENDORS = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking', 'fuel', 'stationery', 'food', 'meals',
                 'woolworths', 'coles', 'aldi', 'kmart', 'target', 'taxi', 'petrol', 'mcdonald', 'kfc',
                 'subway', 'dominos', 'pizza', 'hotel', 'qantas', 'virgin', 'toll', 'linkt']
NOT_A_VENDOR = ('tax invoice', 'receipt', 'invoice', 'abn', 'acn', 'ph', 'phone', 'tel', 'www', 'http', 'date',
                'time', 'store', 'welcome', 'thank', 'customer', 'copy', 'eftpos', 'card', 'page')


def load_receipt_image(data, max_side=2200):
    """Opens a photo with low memory use: JPEGs are decoded at reduced size, rotated, shrunk."""
    img = Image.open(io.BytesIO(data))
    img.draft("RGB", (max_side, max_side))      # big memory saving for JPEG, ignored otherwise
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_side, max_side))
    return img.convert("RGB")


def _prep_for_ocr(img):
    gray = ImageOps.autocontrast(img.convert("L"))
    if max(gray.size) < 1800:                   # small text needs roughly 300 dpi
        f = 1800 / max(gray.size)
        gray = gray.resize((int(gray.width * f), int(gray.height * f)), Image.LANCZOS)
    arr = cv2.medianBlur(np.array(gray), 3)
    arr = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    return Image.fromarray(arr)


def _ocr_image(img):
    """Tries the plain and the cleaned-up image with two layouts, keeps the result with most amounts found."""
    best, best_score = "", -1
    for candidate in (ImageOps.autocontrast(img.convert("L")), _prep_for_ocr(img)):
        for psm in (4, 6):
            try:
                txt = pytesseract.image_to_string(candidate, config=f"--oem 1 --psm {psm}", timeout=25)
            except pytesseract.TesseractNotFoundError:
                raise
            except Exception:
                continue                        # timed out or failed: try the next option
            score = len(_amounts_in(txt)) * 10 + min(len(txt), 400) / 40
            if score > best_score:
                best, best_score = txt, score
    return best


AI_PROMPT = (
    "This is a photo of a receipt or tax invoice (Australia). Read it carefully and reply with ONLY a JSON object:\n"
    '{"vendor": string, "date": "DD/MM/YYYY" or null, "total": number, "gst": number or null, "description": string}\n'
    "total = the final amount actually paid, including GST. gst = the GST/tax amount printed on the receipt "
    "(0 if it says GST free, null if not shown). description = 2-5 words, e.g. 'Officeworks stationery'. "
    "Do not guess: use null for anything you cannot read."
)


def ai_read_receipt(img):
    """Reads date/total/GST with Claude vision. Returns a dict like parse_receipt_text(), or None to fall back."""
    key = _secret("anthropic_api_key")
    if not key:
        return None
    try:
        import anthropic
        small = img.copy()
        small.thumbnail((1800, 1800))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=85)
        client = anthropic.Anthropic(api_key=key, timeout=45, max_retries=1)
        msg = client.messages.create(
            model=_secret("anthropic_model") or "claude-sonnet-5-5", max_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": AI_PROMPT}]}])
        d = json.loads(re.search(r"\{.*\}", msg.content[0].text, re.S).group(0))
        total = round(float(d.get("total") or 0), 2)
        gst = round(float(d.get("gst") or 0), 2)
        if total <= 0:
            return None
        if gst > total:
            gst = 0.0
        rdate = d.get("date")
        ok_date = bool(rdate and re.fullmatch(r"\d{2}/\d{2}/\d{4}", rdate))
        return {"desc": (d.get("description") or d.get("vendor") or "General Expense Purchases")[:60],
                "amount": total, "gst": gst,
                "date": rdate if ok_date else now_local().strftime('%d/%m/%Y'),
                "date_detected": ok_date}
    except Exception:
        return None


def _page_images(page):
    """Pictures embedded in a PDF page (a scanned page is normally one big picture). Needs no extra programs."""
    out = []
    try:
        for im in page.images:
            out.append(im.image)
    except Exception:
        pass
    return out


def _ocr_pdf_page(data, page, idx):
    """OCR for a PDF page with no text layer. Tries the picture inside the page first, then rendering the page.
    Returns (text, problem) where problem explains a failure (or '')."""
    problem = ""
    for img in sorted(_page_images(page), key=lambda i: i.size[0] * i.size[1], reverse=True)[:1]:
        try:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((2400, 2400))
            return _ocr_image(img), ""
        except pytesseract.TesseractNotFoundError:
            return "", "the text-recognition program (Tesseract) is not installed on the server"
        except Exception as e:
            problem = str(e)
    try:
        imgs = convert_from_bytes(data, dpi=200, first_page=idx + 1, last_page=idx + 1)
        if imgs:
            return _ocr_image(imgs[0]), ""
    except pytesseract.TesseractNotFoundError:
        return "", "the text-recognition program (Tesseract) is not installed on the server"
    except Exception as e:
        problem = problem or f"could not turn the page into a picture ({str(e)[:80]}); the server may be missing poppler-utils"
    return "", problem or "no readable text or picture found on the page"


def extract_page_texts(data, ext, progress=None, errors=None):
    """Text of each receipt: one entry for an image, one entry per page for a PDF.
    Text-based PDFs are read directly, scanned pages are read with OCR. Returns (texts, total_pages).
    Pages that could not be read are described in `errors` (a list) when one is given."""
    if errors is None:
        errors = []
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        limit = min(total, MAX_PDF_PAGES)
        texts = []
        for idx, page in enumerate(reader.pages[:limit]):
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if len(txt.strip()) < 15:      # no real text layer: a scanned page, read it with OCR
                txt, problem = _ocr_pdf_page(data, page, idx)
                if len(txt.strip()) < 15:
                    errors.append(f"Page {idx + 1}: {problem or 'almost no text could be read'}")
            texts.append(txt)
            if progress:
                progress(idx + 1, limit)
        return texts, total
    image = load_receipt_image(data)
    try:
        text = _ocr_image(image)
    except pytesseract.TesseractNotFoundError:
        errors.append("the text-recognition program (Tesseract) is not installed on the server")
        text = ""
    return [text], 1


def _amounts_in(line):
    out = []
    for m in AMOUNT_RE.finditer(line):
        try:
            out.append(float(m.group(1).replace(",", "") + "." + m.group(2)))
        except ValueError:
            pass
    return out


def _find_date(raw_text):
    """First plausible date in the text, as DD/MM/YYYY, or None."""
    found = []
    for m in re.finditer(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b', raw_text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        found.append((m.start(), d, mo, y))
    for m in re.finditer(r'\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?[\s\-]+(\d{2,4})\b',
                         raw_text, flags=re.IGNORECASE):
        found.append((m.start(), int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))))
    for _, d, mo, y in sorted(found):
        if y < 100:
            y += 2000
        if 1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2100:
            return f"{d:02d}/{mo:02d}/{y}"
    return None


def _guess_vendor(raw_text):
    for ln in raw_text.split("\n"):
        ln = ln.strip()
        letters = sum(ch.isalpha() for ch in ln)
        if len(ln) < 4 or letters < 4 or letters / len(ln) < 0.6:
            continue
        if ln.lower().startswith(NOT_A_VENDOR):
            continue
        return ln.title()[:40]
    return ""


def parse_receipt_text(raw_text):
    """Reads the date, total, GST and a short description out of one receipt's text."""
    lower = raw_text.lower()
    cleaned = re.sub(r'(?<=\d)[ \t]*([.,])[ \t]*(?=\d{2}\b)', r'\1', lower)
    lines = cleaned.split("\n")

    all_amounts, total_line_amounts = [], []
    for ln in lines:
        vals = _amounts_in(ln)
        all_amounts += vals
        if re.search(r'\b(total|amount due|balance due|amount payable)\b', ln) and not re.search(
                r'sub\s*-?\s*total|total\s+(gst|tax|savings|discount)|gst\s+total|change|cash|tender', ln):
            total_line_amounts += vals
    pool = total_line_amounts or all_amounts
    total_amount = max(pool) if pool else 0.0

    gst_amount = 0.0
    if "gst" in cleaned or "tax" in cleaned:
        candidates = []
        for ln in lines:
            if "gst" in ln or "tax" in ln:
                candidates += [a for a in _amounts_in(ln) if 0 < a <= total_amount]
        if candidates:
            gst_amount = candidates[0]
        elif re.search(r'gst[\s\-]*free|no gst|gst exempt', cleaned):
            gst_amount = 0.0
        else:
            gst_amount = round(total_amount / 11, 2)

    found_date = _find_date(raw_text)
    keywords = [i.title() for i in KNOWN_VENDORS if i in cleaned]
    description = ", ".join(keywords) if keywords else (_guess_vendor(raw_text) or "General Expense Purchases")

    return {
        "desc": description,
        "amount": round(total_amount, 2),
        "gst": round(gst_amount, 2),
        "date": found_date or now_local().strftime('%d/%m/%Y'),
        "date_detected": found_date is not None,
    }


# ---- duplicate detection --------------------------------------------------
def _tokens(text):
    t = re.sub(r'[^a-z0-9]+', ' ', text.lower())
    return {w for w in t.split() if len(w) >= 3 or (len(w) >= 2 and any(ch.isdigit() for ch in w))}


def text_signature(text, k=64):
    """A compact fingerprint of a receipt's wording (the k smallest word hashes) used to spot the same receipt twice."""
    hashes = sorted({int(hashlib.md5(w.encode()).hexdigest()[:6], 16) for w in _tokens(text)})
    return [format(h, "x") for h in hashes[:k]]


def _sig_similarity(a, b):
    if not a or not b or len(a) < 12 or len(b) < 12:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def _same_receipt(a, b):
    """How strongly two receipt lines look like the same receipt: (rank, reason), or (0, None).
    Wording alone is not enough (two receipts from one shop share most words), so a good wording match
    must be backed by the same amount, unless the wording is almost identical."""
    if a.get("exact") and a.get("exact") == b.get("exact"):
        return 3, "identical file or page"
    similarity = _sig_similarity(a.get("sig"), b.get("sig"))
    amount_a, amount_b = (a.get("amount") or 0), (b.get("amount") or 0)
    same_amount = amount_a > 0 and abs(amount_a - amount_b) < 0.005
    if similarity >= 0.9 or (similarity >= 0.7 and same_amount):
        return 2, "very similar content"
    if (a.get("date_detected") and b.get("date_detected") and same_amount
            and (a.get("date") or "") == (b.get("date") or "")):
        return 1, "same date and amount"
    return 0, None


def find_duplicate_receipts(rows, earlier_claims=None, skip_claim_id=None):
    """Flags receipt lines that look like another line in the same claim, or a line in an earlier claim
    (the strongest match is reported for each line). rows: [{"date","amount","exact","sig","date_detected"}].
    Rejected claims are ignored, since a corrected claim may legitimately contain the same receipts."""
    flags = []
    earlier = [c for c in (earlier_claims or []) if c.get("id") != skip_claim_id and claim_stage(c)[0] != "rejected"
               and not c.get("merged_into")]
    for j, row in enumerate(rows):
        best_rank, hit = 0, None
        for i in range(j):
            rank, reason = _same_receipt(row, rows[i])
            if rank > best_rank:
                best_rank, hit = rank, {"line": j + 1, "other_line": i + 1, "other_claim": None, "reason": reason}
        for c in earlier:
            for k, other in enumerate(c.get("receipts", [])):
                rank, reason = _same_receipt(row, other)
                if rank > best_rank:
                    best_rank = rank
                    hit = {"line": j + 1, "other_line": k + 1, "other_claim": c.get("id"), "reason": reason,
                           "other_name": c.get("name", ""), "other_date": c.get("submitted_on", ""),
                           "other_event": c.get("event", "")}
        if hit:
            flags.append(hit)
    return flags


def describe_flag(flag, role=ROLE_ADMIN):
    """One readable sentence for a duplicate flag. Only the Admin sees the other claimant's name."""
    line, other = flag.get("line"), flag.get("other_line")
    reason = flag.get("reason", "")
    if flag.get("other_claim"):
        who = f" by {flag.get('other_name')}" if role == ROLE_ADMIN and flag.get("other_name") else ""
        return (f"Receipt {line} looks like receipt {other} of an earlier claim{who} "
                f"submitted {flag.get('other_date', '')} ({reason})")
    return f"Receipt {line} looks like receipt {other} in this same claim ({reason})"


def analyse_upload(name, data, split_pages, progress=None):
    """Reads an uploaded receipt file. A PDF with several pages is split into one receipt per page when
    split_pages is true. Returns {"pages": n, "read_pages": n, "items": [receipt dicts]}."""
    ext = os.path.splitext(name)[1].lower()
    problems = []
    texts, total_pages = extract_page_texts(data, ext, progress, problems)
    file_hash = hashlib.sha256(data).hexdigest()[:20]
    items = []
    if ext == ".pdf" and split_pages and len(texts) > 1:
        for idx, text in enumerate(texts):
            norm = re.sub(r'[^a-z0-9]', '', text.lower())
            exact = (hashlib.sha256(norm.encode()).hexdigest()[:20] if len(norm) >= 30
                     else hashlib.sha256(data + str(idx).encode()).hexdigest()[:20])
            items.append({**parse_receipt_text(text), "exact": exact, "sig": text_signature(text), "text": text})
    else:
        full = "\n".join(texts)
        fields = None
        if ext != ".pdf":
            fields = ai_read_receipt(load_receipt_image(data))
        items.append({**(fields or parse_receipt_text(full)), "exact": file_hash,
                      "sig": text_signature(full), "text": full})
    return {"pages": total_pages, "read_pages": len(texts), "items": items, "problems": problems}


# -------------------------------------------------------------------------
# VOUCHER PDF BUILDER
# -------------------------------------------------------------------------
def generate_official_voucher_pdf(filename, data, receipts):
    """Compiles the Majlis Ansarullah Australia expense voucher."""
    E = lambda value: xml_escape(str(value if value is not None else ""))   # keeps &, < and > in typed text from breaking the PDF
    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []

    header_title_style = ParagraphStyle('HeadTitle', fontName='Helvetica-Bold', fontSize=14, alignment=1, spaceAfter=2)
    header_subtitle_style = ParagraphStyle('HeadSub', fontName='Helvetica-Bold', fontSize=12, alignment=1, spaceAfter=2)
    dept_style = ParagraphStyle('Dept', fontName='Helvetica', fontSize=11, alignment=1, spaceAfter=2)
    top_line_style = ParagraphStyle('TopLine', fontName='Helvetica-Oblique', fontSize=9, leading=11, alignment=1, spaceAfter=4)
    cell_style = ParagraphStyle('Cell', fontName='Helvetica', fontSize=9, leading=11)
    cell_bold_style = ParagraphStyle('CellB', fontName='Helvetica-Bold', fontSize=9, leading=11)
    cell_red_bold_style = ParagraphStyle('CellRB', fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=colors.red)

    story.append(Paragraph("In the name of Allah, the gracious, the merciful", top_line_style))
    story.append(Paragraph("MAJLIS ANSARULLAH AUSTRALIA", header_title_style))
    story.append(Paragraph("Department of Mal", dept_style))
    story.append(Paragraph("Expense Payment Voucher", header_subtitle_style))
    story.append(Spacer(1, 10))

    header_data = [
        [Paragraph("<b>Payee Name:</b>", cell_style), Paragraph(E(data['name']), cell_style), Paragraph("<b>Voucher No:</b>", cell_style), Paragraph("", cell_style)],
        [Paragraph("<b>Expense Type:</b>", cell_style), Paragraph(E(data['expense_type']), cell_style), Paragraph("<b>BSB:</b>", cell_style), Paragraph(E(data['bsb']), cell_style)],
        [Paragraph("<b>Account:</b>", cell_style), Paragraph(E(data['account']), cell_style), Paragraph("<b>Account No:</b>", cell_style), Paragraph(E(data['acc_num']), cell_style)],
        [Paragraph("<b>Region:</b>", cell_style), Paragraph(E(data['region']), cell_style), Paragraph("<b>Total Claim:</b>", cell_bold_style), Paragraph(f"<b>$ {data['total_amount']:.2f}</b>", cell_bold_style)],
        [Paragraph("<b>Majlis:</b>", cell_style), Paragraph(E(data['majlis']), cell_style), Paragraph("<b>Advance Issued:</b>", cell_style), Paragraph(f"$ {data['advance']:.2f}" if data['advance'] > 0 else "", cell_red_bold_style)]
    ]

    t_header = Table(header_data, colWidths=[100, 170, 100, 170])
    t_header.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 15))

    receipt_table_data = [[Paragraph("<b>#</b>", cell_bold_style), Paragraph("<b>Date</b>", cell_bold_style), Paragraph("<b>Description</b>", cell_bold_style), Paragraph("<b>Total amount</b>", cell_bold_style), Paragraph("<b>GST</b>", cell_bold_style)]]

    advance = data.get('advance', 0.0)
    net_total = data['total_amount'] - advance

    max_rows = max(10, len(receipts))
    for idx in range(max_rows):
        if idx < len(receipts):
            r = receipts[idx]
            receipt_table_data.append([
                Paragraph(str(idx + 1), cell_style),
                Paragraph(E(r['date']), cell_style),
                Paragraph(E(r['desc']), cell_style),
                Paragraph(f"${r['amount']:.2f}", cell_style),
                Paragraph(f"${r['gst']:.2f}", cell_style)
            ])
        else:
            receipt_table_data.append([Paragraph(str(idx + 1), cell_style), "", "", "", ""])

    if advance > 0:
        receipt_table_data.append([
            "", "",
            Paragraph("Less: Advance Settled", cell_red_bold_style),
            Paragraph(f"-${advance:.2f}", cell_red_bold_style),
            ""
        ])

    receipt_table_data.append([
        Paragraph("<b>Total</b>", cell_bold_style), "", "",
        Paragraph(f"<b>${net_total:.2f}</b>", cell_bold_style),
        Paragraph(f"<b>${data['total_gst']:.2f}</b>", cell_bold_style)
    ])

    t_receipts = Table(receipt_table_data, colWidths=[30, 80, 250, 90, 90])
    t_receipts.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
        ('SPAN', (0, -1), (2, -1))
    ]))
    story.append(t_receipts)
    story.append(Spacer(1, 15))

    signoff_data = [
        [Paragraph("<b>Requested by:</b>", cell_bold_style), Paragraph("<b>Reviewed by Nazime A'la:</b>", cell_bold_style)],
        [Paragraph(f"Name: {E(data['name'])}", cell_style), Paragraph(f"Name: {E(data['nazim_name'])}", cell_style)],
        [Paragraph("Signature:", cell_style), Paragraph("Signature:", cell_style)],
        [Paragraph(f"Date: {E(data.get('request_date') or now_local().strftime('%d/%m/%Y'))}", cell_style), Paragraph("Date:", cell_style)],
        [Paragraph("<b>Approved by Qaid Mal:</b>", cell_bold_style), Paragraph("<b>Approved by Sadr MAA:</b>", cell_bold_style)],
        [Paragraph(f"Name: {E(data['qaid_mal'])}", cell_style), Paragraph(f"Name: {E(data['sadr_maa'])}", cell_style)],
        [Paragraph("Signature: <i>Tahir Mehmood</i>", cell_style), Paragraph("Signature:", cell_style)],
        [Paragraph("Date:", cell_style), Paragraph("Date:", cell_style)],
    ]

    t_signoff = Table(signoff_data, colWidths=[270, 270])
    t_signoff.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 0), (1, 0), colors.lightgrey),
        ('BACKGROUND', (0, 4), (1, 4), colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_signoff)

    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Acknowledgment</b>", header_subtitle_style))
    story.append(Paragraph("I have received cash/cheque payment of $________________ as per details provided above.", cell_style))
    story.append(Paragraph("<b>Comments:</b> Hand written Receipt approved by Sadr Majlis. (Updated: 1-11-19)", cell_style))

    doc.build(story)

# -------------------------------------------------------------------------
# APPENDIX BINDER STITCHING PIPELINE
# -------------------------------------------------------------------------
def _image_to_pdf_buffer(u_file, label):
    """Places an image receipt on an A4-size page, scaled to fit."""
    img = Image.open(u_file)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    page_w, page_h = A4
    margin = 36
    header_h = 20
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin - header_h

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, page_h - margin, label)

    iw, ih = img.size
    scale = min(max_w / iw, max_h / ih)
    dw, dh = iw * scale, ih * scale
    x = margin + (max_w - dw) / 2
    y = page_h - margin - header_h - dh
    c.drawImage(ImageReader(img), x, y, width=dw, height=dh)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def stitch_files_to_pdf(voucher_pdf_path, uploaded_files_list, labels=None, skip_pages=None):
    """Summary page first, then every uploaded receipt, as one PDF.
    labels = the text printed above each image receipt (default Receipt #1, #2 ...).
    skip_pages = {file position: set of 0-based page numbers to leave out of that PDF}."""
    try:
        writer = PdfWriter()

        for page in PdfReader(voucher_pdf_path).pages:
            writer.add_page(page)

        for idx, u_file in enumerate(uploaded_files_list):
            if u_file is None:
                continue
            u_file.seek(0)
            ext = os.path.splitext(u_file.name)[1].lower()

            if ext == ".pdf":
                reader = PdfReader(io.BytesIO(u_file.read()))
            else:
                label = labels[idx] if labels else f"Receipt #{idx + 1}"
                reader = PdfReader(_image_to_pdf_buffer(u_file, label))

            skip = (skip_pages or {}).get(idx, set())
            for n, page in enumerate(reader.pages):
                if n not in skip:
                    writer.add_page(page)

        tmp_out = voucher_pdf_path + ".tmp"
        with open(tmp_out, "wb") as f:
            writer.write(f)
        os.replace(tmp_out, voucher_pdf_path)
        return True
    except Exception as e:
        st.error(f"⚠️ Could not attach receipts to the voucher: {e}")
        return False

# -------------------------------------------------------------------------
# CLAIMS TABLES (shared by Admin, Nazim e Ala and President)
# -------------------------------------------------------------------------
DASH_HEADERS = ["Claimant", "Date", "Event", "Total Claim", "GST", "Advance Adj.", "Amount To Pay",
                "Voucher No.", "Status", "💬", "File"]
DASH_WIDTHS = [1.5, 0.9, 1.4, 0.9, 0.7, 0.9, 1.0, 1.0, 1.8, 0.5, 0.8]


def _md_escape(text):
    """Makes typed text safe to show with st.markdown (no stray formatting, links or maths)."""
    return re.sub(r'([\\`*_{}\[\]<>()#+\-.!|$~:])', r'\\\1', str(text if text is not None else "")).replace("\n", "  \n")


def _cell(col, text):
    col.markdown(str(text).replace("$", "\\$"))


def _history_lines(c):
    events = c.get("history") or []
    if not events:
        return [f"- {_md_escape(c.get('submitted_on', ''))} — Claim submitted",
                "- _No detailed history was recorded for this claim because it was submitted before history logging was added._"]
    return [f"- **{_md_escape(e.get('when', ''))}** — {_md_escape(e.get('by', ''))}: {_md_escape(e.get('what', ''))}"
            for e in events]


PERIOD_CHOICES = ["All dates", "Last 7 days", "Last 30 days", "Last 90 days", "This month", "Last month",
                  "This year"]
SORT_CHOICES = ["Date submitted", "Claimant", "Event", "Total claim", "Amount to pay", "Voucher No.", "Status"]


def _in_period(d, period, today):
    """True when date d falls in the chosen period preset (claims with no date only show under 'All dates')."""
    if period == "All dates":
        return True
    if d is None:
        return False
    if period == "Last 7 days":
        return today - timedelta(days=7) <= d <= today
    if period == "Last 30 days":
        return today - timedelta(days=30) <= d <= today
    if period == "Last 90 days":
        return today - timedelta(days=90) <= d <= today
    if period == "This month":
        return (d.year, d.month) == (today.year, today.month)
    if period == "Last month":
        last = today.replace(day=1) - timedelta(days=1)
        return (d.year, d.month) == (last.year, last.month)
    return _fy_label(d) == _fy_label(today)


def _voucher_sort_key(c):
    v = str(c.get("voucher_no") or "").strip()
    parts = re.split(r"(\d+)", v.lower())
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p != ""]


def filter_sort_claims(claims_subset, key, default_newest=False, show_region=True):
    """Draws the Search / Sort / Filter controls above a claims list and returns the claims to show, in order.
    Every list has its own controls (key), so each tab remembers its own choices."""
    k = lambda n: f"fs_{key}_{n}"
    stage_text = {c["id"]: claim_stage(c)[1] for c in claims_subset}

    def options(values):
        return ["All"] + sorted({v for v in values if v}, key=lambda x: str(x).lower())

    opts = {
        "status": options(stage_text.values()),
        "who": options(c.get("name") for c in claims_subset),
        "reg": options(c.get("region") for c in claims_subset),
        "acct": options(c.get("account") for c in claims_subset),
        "year": options(_claim_year(c) for c in claims_subset),
    }
    defaults = {"q": "", "sort": "Date submitted", "dir": "Descending" if default_newest else "Ascending",
                "status": "All", "who": "All", "reg": "All", "acct": "All", "per": "All dates", "year": "All"}
    for name, value in defaults.items():
        st.session_state.setdefault(k(name), value)       # first visit: start from the default order and no filters
    for name, choices in (("status", opts["status"]), ("who", opts["who"]), ("reg", opts["reg"]),
                          ("acct", opts["acct"]), ("year", opts["year"]), ("per", PERIOD_CHOICES), ("sort", SORT_CHOICES),
                          ("dir", ["Ascending", "Descending"])):
        if st.session_state.get(k(name), defaults[name]) not in choices:
            st.session_state[k(name)] = defaults[name]     # a saved choice that no longer exists falls back to All

    def reset():
        for name, value in defaults.items():
            st.session_state[k(name)] = value

    extra = ["status", "who", "acct", "year", "per"] + (["reg"] if show_region else [])
    active = sum(1 for n in extra if st.session_state.get(k(n), defaults[n]) != defaults[n])
    active_q = bool(str(st.session_state.get(k("q"), "")).strip())

    c1, c2, c3, c4 = st.columns([3, 2, 1.6, 1.2])
    query = c1.text_input("Search", key=k("q"), placeholder="🔍 Search claimant, event, voucher no. or amount",
                          label_visibility="collapsed")
    sort_by = c2.selectbox("Sort by", SORT_CHOICES, key=k("sort"), format_func=lambda x: f"Sort by: {x}",
                           label_visibility="collapsed")
    direction = c3.selectbox("Order", ["Ascending", "Descending"], key=k("dir"),
                             format_func=lambda x: "↑ Ascending (A-Z, oldest, lowest)" if x == "Ascending"
                             else "↓ Descending (Z-A, newest, highest)", label_visibility="collapsed")
    c4.button("Clear filters", key=k("clear"), on_click=reset, use_container_width=True,
              disabled=not (active or active_q))

    with st.expander("Filters" + (f" ({active} active)" if active else ""), expanded=bool(active)):
        cols = st.columns(6 if show_region else 5)
        status_f = cols[0].selectbox("Status", opts["status"], key=k("status"))
        who_f = cols[1].selectbox("Claimant", opts["who"], key=k("who"))
        acct_f = cols[2].selectbox("Account head", opts["acct"], key=k("acct"))
        year_f = cols[3].selectbox("Year (Jan-Dec)", opts["year"], key=k("year"))
        per_f = cols[4].selectbox("Date submitted", PERIOD_CHOICES, key=k("per"))
        reg_f = cols[5].selectbox("Region", opts["reg"], key=k("reg")) if show_region else "All"

    today = now_local().date()
    needle = " ".join(str(query or "").lower().split())
    shown = []
    for c in claims_subset:
        if status_f != "All" and stage_text[c["id"]] != status_f:
            continue
        if who_f != "All" and c.get("name") != who_f:
            continue
        if reg_f != "All" and c.get("region") != reg_f:
            continue
        if acct_f != "All" and c.get("account") != acct_f:
            continue
        if year_f != "All" and _claim_year(c) != year_f:
            continue
        if not _in_period(_claim_date(c), per_f, today):
            continue
        if needle:
            hay = " ".join(str(x).lower() for x in (
                c.get("name"), c.get("event"), c.get("voucher_no"), c.get("region"), c.get("majlis"),
                c.get("account"), f"{float(c.get('total_claim') or 0):.2f}",
                f"{float(c.get('amount_payable') or 0):.2f}"))
            if needle not in hay:
                continue
        shown.append(c)

    rev = direction == "Descending"
    if sort_by == "Voucher No.":      # claims with no voucher number always stay at the bottom
        have = sorted([c for c in shown if c.get("voucher_no")], key=_voucher_sort_key, reverse=rev)
        shown = have + [c for c in shown if not c.get("voucher_no")]
    else:
        keyfn = {
            "Date submitted": lambda c: c.get("submitted_ts", ""),
            "Claimant": lambda c: (str(c.get("name", "")).lower(), c.get("submitted_ts", "")),
            "Event": lambda c: (str(c.get("event", "")).lower(), c.get("submitted_ts", "")),
            "Total claim": lambda c: float(c.get("total_claim") or 0),
            "Amount to pay": lambda c: float(c.get("amount_payable") or 0),
            "Status": lambda c: (stage_text[c["id"]].lower(), c.get("submitted_ts", "")),
        }[sort_by]
        shown = sorted(shown, key=keyfn, reverse=rev)

    st.caption(f"Showing {len(shown)} of {len(claims_subset)} claims")
    return shown


def render_claim_rows(service, folder_id, claims_subset, auth, tab_key, newest_first=False, show_history=False,
                      merge_select=False):
    """Draws the table for one tab: search, filters and sort order first (oldest first by default, newest first
    when newest_first is set), then the claims.
    The buttons each role sees come from actions_for(). Everyone can comment; only the Admin gets the
    Voucher No. box, Amend PDF and Delete."""
    if not claims_subset:
        st.info("No claims in this tab.")
        return

    role = auth["role"]
    actor = auth["name"]

    shown = filter_sort_claims(claims_subset, tab_key, default_newest=newest_first,
                               show_region=role != ROLE_NAZIM)
    if not shown:
        st.info("No claims match the search and filters. Use Clear filters to see them all.")
        return

    if merge_select and role == ROLE_ADMIN:
        chosen = [c["id"] for c in shown if st.session_state.get(f"mrg_{c['id']}")]
        m_btn, c_btn, m_note = st.columns([2, 2, 5])
        if m_btn.button(f"🔗 Merge selected ({len(chosen)})", key=f"merge_go_{tab_key}", type="primary",
                        disabled=len(chosen) < 2):
            st.session_state["merge_ids"] = chosen
            st.session_state["admin_view"] = "merge"
            st.rerun()
        if c_btn.button(f"✅ Complete selected ({len(chosen)})", key=f"complete_go_{tab_key}", disabled=not chosen):
            try:
                done = complete_claims(service, folder_id, chosen, role, actor)
            except Exception as e:
                st.error(f"Could not complete the claims: {e}")
            else:
                for cid in chosen:
                    st.session_state.pop(f"mrg_{cid}", None)
                st.session_state["dash_notice"] = f"{done} claim{'s' if done != 1 else ''} marked as complete and removed from Open Claims."
                st.rerun()
        m_note.caption("Tick 'Select' on one or more claims, then click Merge (several claims of one claimant, still with the Admin and "
                       "not yet approved, become one file with a header page) or Complete (all selected claims leave Open Claims). "
                       "Open claims can still be approved and paid.")

    header_cols = st.columns(DASH_WIDTHS)
    for col, label in zip(header_cols, DASH_HEADERS):
        col.markdown(f"**{label}**")
    st.divider()

    for c in shown:
        stage_key, status_text = claim_stage(c)
        flags = c.get("flags") or []
        comments = c.get("comments") or []

        cols = st.columns(DASH_WIDTHS)
        cols[0].markdown(f"**{_md_escape(c.get('name', ''))}**  \n:gray[{_md_escape(c.get('region', ''))}]")
        _cell(cols[1], c.get("submitted_on", "") + (f"  \n:gray[Year {c['fy']}]" if c.get("fy") else ""))
        cols[2].markdown(_md_escape(c.get("event", "")))
        _cell(cols[3], f"${c.get('total_claim', 0):,.2f}")
        _cell(cols[4], f"${c.get('gst', 0):,.2f}")
        advance = c.get("advance", 0)
        _cell(cols[5], f"-${advance:,.2f}" if advance > 0 else "-")
        _cell(cols[6], f"**${c.get('amount_payable', 0):,.2f}**")
        cols[7].markdown(f"**{_md_escape(c['voucher_no'])}**" if c.get("voucher_no") else "-")
        _cell(cols[8], status_text + (" ⚠️" if flags else "") + (" ✅ Complete" if c.get("complete") else ""))
        _cell(cols[9], f"💬 {len(comments)}" if comments else "-")
        cols[10].markdown(f"[Open PDF]({c.get('file_link', '')})")

        if flags:
            st.warning("⚠️ **Possible duplicate receipt(s):**  \n" +
                       "  \n".join(_md_escape(describe_flag(f, role)) for f in flags))

        actions = actions_for(role, c)
        key_base = f"{tab_key}_{c['id']}"
        ver = st.session_state.get(f"cver_{key_base}", 0)
        cmt_area = st.container()      # comment box sits above the buttons but is read first
        ctrl_area = st.container()

        with cmt_area:
            cc1, cc2, _ = st.columns([5, 1.6, 2.4])
            comment_text = cc1.text_input(
                "Comment", key=f"cmt_{key_base}_{ver}", max_chars=MAX_COMMENT_LEN, label_visibility="collapsed",
                placeholder="Add a comment (optional). It is saved with your decision and the Nazim e Ala, Admin and President can all read it.")
            add_comment_clicked = cc2.button("💬 Add comment", key=f"addcmt_{key_base}", use_container_width=True)

        def _apply(changes, voucher=None, c=c, key_base=key_base, ver=ver, comment=comment_text, fy=None):
            try:
                warning = update_claim(service, folder_id, c["id"], role, actor, changes=changes,
                                       voucher_no=voucher, region=auth.get("region"), comment=comment, fy=fy)
            except Exception as e:
                st.error(f"Could not update claim: {e}")
                return
            if comment.strip():
                st.session_state[f"cver_{key_base}"] = ver + 1      # a fresh, empty comment box next time
            if warning:
                st.session_state["dash_warning"] = warning
            st.rerun()

        if add_comment_clicked:
            if comment_text.strip():
                _apply({})
            else:
                st.warning("Type a comment first.")

        with ctrl_area:
            if role == ROLE_ADMIN:
                is_complete = bool(c.get("complete"))
                if stage_key not in ("merged", "rejected") and not merge_select:
                    # the key includes the saved state, so the box always shows the saved value
                    ticked = st.checkbox(
                        "Claim complete (nothing more is coming for this event). Untick = stays in Open Claims, "
                        "tick = removed from Open Claims. Approval and payment work either way.", value=is_complete,
                        key=f"cplt_{key_base}_{int(is_complete)}")
                    if ticked != is_complete:
                        _apply({"complete": ticked})
                if merge_select:
                    st.checkbox("Select (for Merge or Complete)", key=f"mrg_{c['id']}")

                n = len(actions)
                row_cols = st.columns([2.0, 1.0, 1.0] + [1.15] * n + [max(0.2, 2.6 - 1.15 * n), 1.4, 1.1])
                voucher_input = row_cols[0].text_input(
                    "Voucher No.", value=c.get("voucher_no", ""), max_chars=20,
                    key=f"vno_{key_base}", placeholder="Enter Voucher No.", label_visibility="collapsed"
                )
                # The year the claim counts in (our year is 1 Jan - 31 Dec). Current year unless already set.
                this_year = now_local().year
                saved_fy = str(c.get("fy") or "")
                year_opts = sorted({str(this_year + 1), str(this_year), str(this_year - 1), saved_fy,
                                    _claim_year(c)} - {""}, reverse=True)
                fy_pick = row_cols[2].selectbox(
                    "Year", year_opts, index=year_opts.index(saved_fy or str(this_year)),
                    key=f"fy_{key_base}_{saved_fy or 'new'}", label_visibility="collapsed",
                    format_func=lambda y: f"Year {y}",
                    help="The year (1 Jan - 31 Dec) this claim counts in. It is saved when you approve the claim, "
                         "or when you click Save No. on an approved claim.")
                if row_cols[1].button("Save No.", key=f"saveno_{key_base}", use_container_width=True):
                    keep_year = fy_pick if (saved_fy or c["admin_status"] == STATUS_APPROVED) else None
                    _apply({}, voucher_input, fy=keep_year)
                for b_col, (label, field, value) in zip(row_cols[3:3 + n], actions):
                    needs_voucher = field == "admin_status" and value == STATUS_APPROVED and not voucher_input.strip()
                    if b_col.button(label, key=f"{key_base}_{field}_{value}", use_container_width=True,
                                    disabled=needs_voucher,
                                    help="Enter a Voucher No. first" if needs_voucher else None):
                        _apply({field: value}, voucher_input,
                               fy=fy_pick if (field == "admin_status" and value == STATUS_APPROVED) else None)
                if row_cols[-2].button("✏️ Amend PDF", key=f"amend_{key_base}", use_container_width=True):
                    st.session_state["amend_claim_id"] = c["id"]
                    st.session_state["amend_init"] = None
                    st.session_state["admin_view"] = "amend"
                    st.rerun()
                if row_cols[-1].button("🗑 Delete", key=f"del_{key_base}", use_container_width=True):
                    st.session_state[f"confirm_del_{c['id']}"] = True

                if st.session_state.get(f"confirm_del_{c['id']}"):
                    st.warning(
                        f"Delete the claim from **{_md_escape(c.get('name', ''))}** ({_md_escape(c.get('event', ''))})? "
                        "Its PDF will be moved to the Google Drive Trash and the claim removed from every screen."
                    )
                    yes_col, no_col, _ = st.columns([1.3, 1, 6])
                    if yes_col.button("Yes, delete", key=f"delyes_{key_base}", type="primary"):
                        try:
                            delete_claim(service, folder_id, c["id"], role)
                        except Exception as e:
                            st.error(f"Could not delete the claim: {e}")
                        else:
                            st.session_state.pop(f"confirm_del_{c['id']}", None)
                            st.rerun()
                    if no_col.button("Cancel", key=f"delno_{key_base}"):
                        st.session_state.pop(f"confirm_del_{c['id']}", None)
                        st.rerun()
            elif actions:
                row_cols = st.columns([1.4] * len(actions) + [max(0.3, 8.0 - 1.4 * len(actions))])
                for b_col, (label, field, value) in zip(row_cols, actions):
                    if b_col.button(label, key=f"{key_base}_{field}_{value}", use_container_width=True):
                        _apply({field: value})

        if comments:
            with st.expander(f"💬 {len(comments)} comment{'s' if len(comments) != 1 else ''} — click to read"):
                for cm in comments:
                    decision = f"  \n:gray[{_md_escape(cm['decision'])}]" if cm.get("decision") else ""
                    st.markdown(f"**{_md_escape(cm.get('by', ''))}** · {_md_escape(cm.get('when', ''))}{decision}  \n"
                                f"{_md_escape(cm.get('text', ''))}")

        if show_history:
            with st.expander("📜 History"):
                st.markdown("\n".join(_history_lines(c)))
        st.divider()


def load_dashboard_data(role):
    """Connects to Drive and loads all claims, or shows a friendly message and returns None."""
    notice = st.session_state.pop("dash_notice", None)
    if notice:
        st.success(notice)
    pending_warning = st.session_state.pop("dash_warning", None)
    if pending_warning:
        st.warning(f"Saved, but: {pending_warning} Use the Open PDF link to check the file in Drive.")

    try:
        service = get_drive_service()
    except Exception as e:
        st.error(f"Google Drive credentials problem: {e}")
        return None
    folder_id = get_drive_folder_id()

    fix = "Open Settings and paste your Drive folder link." if role == ROLE_ADMIN else "Please contact the administrator."
    if service is None:
        st.warning(f"Google Drive is not connected yet. {fix}")
        return None
    if not folder_id:
        st.warning(f"No Google Drive folder is configured. {fix}")
        return None
    try:
        claims, _ = load_register(service, folder_id)
    except Exception as e:
        st.error(f"Could not read the claims from Google Drive: {e}")
        return None
    return service, folder_id, claims


def render_tabs(service, folder_id, claims, auth, tab_specs, role_key):
    """tab_specs = [(label, predicate)] or [(label, predicate, options)].
    Each tab shows the claims its predicate accepts; options are passed on to render_claim_rows."""
    specs = [(s[0], s[1], s[2] if len(s) > 2 else {}) for s in tab_specs]
    subsets = [[c for c in claims if pred(c)] for _, pred, _ in specs]
    tabs = st.tabs([f"{label} ({len(sub)})" for (label, _, _), sub in zip(specs, subsets)])
    for tab, (label, _, opts), sub in zip(tabs, specs, subsets):
        with tab:
            render_claim_rows(service, folder_id, sub, auth, f"{role_key}_{label}", **opts)


def _stage_is(*keys):
    return lambda c: claim_stage(c)[0] in keys


def is_open_claim(c):
    """Open = not ticked 'complete' by the Admin (more claims may still come for the same event). An open claim is
    still approved, sent to the President and paid as normal; it just stays listed here until it is ticked complete."""
    return not c.get("complete") and claim_stage(c)[0] not in ("rejected", "merged")


# ---------------------------- ADMIN ----------------------------
def render_claims_dashboard(auth):
    st.markdown("### 📊 Claims Dashboard")
    top_s, top_a, top_b, top_c, top_u, _ = st.columns([1.2, 1, 1, 1, 1.9, 3.1])
    if top_s.button("📈 Summary", use_container_width=True, type="primary"):
        st.session_state.admin_view = "summary"
        st.rerun()
    if top_a.button("⚙️ Settings", use_container_width=True):
        st.session_state.admin_view = "settings"
        st.rerun()
    if top_b.button("👥 Roles", use_container_width=True):
        st.session_state.admin_view = "roles"
        st.rerun()
    if top_c.button("🔄 Refresh", use_container_width=True):
        st.rerun()
    if top_u.button("📤 Upload Claim", use_container_width=True):
        st.session_state.admin_view = "upload"
        st.rerun()

    data = load_dashboard_data(ROLE_ADMIN)
    if data is None:
        return
    service, folder_id, claims = data

    render_tabs(service, folder_id, claims, auth, [
        ("Open Claims", is_open_claim, {"merge_select": True}),
        ("To Process", _stage_is("to_process")),
        ("Awaiting Nazim", _stage_is("await_nazim")),
        ("On Hold", _stage_is("hold")),
        ("Waiting President", _stage_is("await_president")),
        ("Approved", _stage_is("approved")),
        ("Paid", _stage_is("paid"), {"newest_first": True, "show_history": True}),
        ("Rejected", _stage_is("rejected")),
        ("Merged", _stage_is("merged"), {"newest_first": True, "show_history": True}),
    ], "admin")


# ------------------------- NAZIM E ALA -------------------------
def render_nazim_dashboard(auth):
    region = auth["region"]
    st.markdown(f"### 📋 {auth['name']} — Claims")
    if st.button("🔄 Refresh"):
        st.rerun()

    data = load_dashboard_data(ROLE_NAZIM)
    if data is None:
        return
    service, folder_id, claims = data
    mine = [c for c in claims if c.get("region") == region]

    in_progress = lambda c: _stage_done(c["nazim_status"]) and claim_stage(c)[0] not in ("approved", "paid", "rejected", "merged")
    render_tabs(service, folder_id, mine, auth, [
        ("To Review", _stage_is("await_nazim")),
        ("On Hold", lambda c: c["nazim_status"] == STATUS_HOLD),
        ("In Progress", in_progress),
        ("Approved", _stage_is("approved", "paid")),
        ("Rejected", _stage_is("rejected")),
    ], "nazim")


# --------------------------- PRESIDENT ---------------------------
def render_president_dashboard(auth):
    st.markdown("### 📋 President — Claims")
    if st.button("🔄 Refresh"):
        st.rerun()

    data = load_dashboard_data(ROLE_PRESIDENT)
    if data is None:
        return
    service, folder_id, claims = data
    visible = [c for c in claims if can_view_claim(ROLE_PRESIDENT, c)]

    render_tabs(service, folder_id, visible, auth, [
        ("To Approve", _stage_is("await_president")),
        ("On Hold", lambda c: c["president_status"] == STATUS_HOLD),
        ("Approved", lambda c: claim_stage(c)[0] in ("approved", "paid") and c["president_status"] == STATUS_APPROVED),
        ("Rejected", lambda c: c["president_status"] == STATUS_REJECTED),
    ], "president")


# -------------------------------------------------------------------------
# SUMMARY, ACCOUNT BALANCES AND BAS REPORT (Admin only)
# Balances live in a small ledger file (account_ledger.json) in the same Drive folder as the claims register.
# For each account head:  balance = last "Balance set" amount + money added - other payments out
#                                   - amount paid on claims marked Paid AFTER the balance was set.
# -------------------------------------------------------------------------
LEDGER_NAME = "account_ledger.json"
LEDGER_TYPES = {"set": "Balance set", "add": "Money added", "deduct": "Other payment out"}
STAGE_LABELS = {
    "await_nazim": "With Nazim e Ala", "to_process": "With Admin", "await_president": "With President",
    "approved": "Approved - ready to pay", "paid": "Paid", "hold": "On hold", "rejected": "Rejected",
}


def _find_file_id(service, folder_id, name):
    q = f"name = '{name}' and '{folder_id}' in parents and trashed = false"
    res = service.files().list(
        q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def load_ledger(service, folder_id):
    """Returns (list_of_entries, ledger_file_id_or_None)."""
    fid = _find_file_id(service, folder_id, LEDGER_NAME)
    if not fid:
        return [], None
    raw = service.files().get_media(fileId=fid, supportsAllDrives=True).execute()
    return json.loads(raw.decode("utf-8")), fid


def save_ledger(service, folder_id, entries, fid):
    media = MediaIoBaseUpload(io.BytesIO(json.dumps(entries, indent=2).encode("utf-8")),
                              mimetype="application/json", resumable=False)
    if fid:
        service.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
    else:
        service.files().create(body={"name": LEDGER_NAME, "parents": [folder_id]},
                               media_body=media, fields="id", supportsAllDrives=True).execute()


def add_ledger_entry(service, folder_id, kind, account, amount, note, actor):
    if kind not in LEDGER_TYPES:
        raise ValueError("Unknown entry type.")
    if not account:
        raise ValueError("Choose an account head.")
    amount = round(float(amount or 0), 2)
    if kind in ("add", "deduct") and amount <= 0:
        raise ValueError("The amount must be more than $0.00.")
    entries, fid = load_ledger(service, folder_id)
    now = now_local()
    entries.append({"id": uuid.uuid4().hex[:10], "ts": now.isoformat(), "date": now.strftime('%d/%m/%Y'),
                    "type": kind, "account": account, "amount": amount,
                    "note": (note or "").strip()[:200], "by": actor})
    save_ledger(service, folder_id, entries, fid)


def delete_ledger_entry(service, folder_id, entry_id):
    entries, fid = load_ledger(service, folder_id)
    save_ledger(service, folder_id, [e for e in entries if e.get("id") != entry_id], fid)


def _parse_dmy(text):
    m = re.match(r"\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", str(text or ""))
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _iso(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _claim_date(c):
    d = _parse_dmy(c.get("submitted_on"))
    if d:
        return d
    dt = _iso(c.get("submitted_ts"))
    return dt.date() if dt else None


def _paid_dt(c):
    dt = _iso(c.get("paid_ts"))
    if dt:
        return dt
    d = _parse_dmy(c.get("paid_on"))
    return datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ) if d else None


def _fy_label(d):
    """Our financial year is the calendar year (1 January - 31 December), labelled by that year, e.g. '2026'."""
    return str(d.year)


def _claim_year(c):
    """The year a claim counts in: the year the Admin chose when approving it, otherwise the year it was submitted."""
    fy = str(c.get("fy") or "").strip()
    if fy:
        return fy
    d = _claim_date(c)
    return str(d.year) if d else ""


def all_heads(claims, entries=()):
    accts = st.session_state.admin_lists.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
    heads = list(accts.get("National", [])) + list(accts.get("General_Regional", []))
    for src in (claims, entries):
        for item in src:
            if item.get("account") and item["account"] not in heads:
                heads.append(item["account"])
    return heads


def compute_balances(entries, claims, heads):
    """{head: {balance, has_base, base_date, start, added, manual_out, claims_paid}}"""
    out = {}
    for head in heads:
        bal = start = added = manual_out = 0.0
        base_dt, base_date = None, ""
        for e in sorted((x for x in entries if x.get("account") == head), key=lambda x: x.get("ts", "")):
            amt = float(e.get("amount") or 0)
            if e.get("type") == "set":
                bal = start = amt
                added = manual_out = 0.0
                base_dt, base_date = _iso(e.get("ts")), e.get("date", "")
            elif e.get("type") == "add":
                bal += amt
                added += amt
            elif e.get("type") == "deduct":
                bal -= amt
                manual_out += amt
        claims_paid = 0.0
        for c in claims:
            if c.get("account") != head or claim_stage(c)[0] != "paid":
                continue
            pdt = _paid_dt(c)
            if base_dt and (pdt is None or pdt <= base_dt):
                continue            # that payment was already in the balance that was typed in
            claims_paid += float(c.get("amount_payable") or 0)
        out[head] = {"balance": round(bal - claims_paid, 2), "has_base": base_dt is not None,
                     "base_date": base_date, "start": round(start, 2), "added": round(added, 2),
                     "manual_out": round(manual_out, 2), "claims_paid": round(claims_paid, 2)}
    return out


def filter_claims(claims, head="All", region="All", fy="All", state="All"):
    out = []
    for c in claims:
        if claim_stage(c)[0] == "merged":       # counted through the consolidated claim instead
            continue
        if head != "All" and c.get("account") != head:
            continue
        if region != "All" and c.get("region") != region:
            continue
        if fy != "All":
            if _claim_year(c) != fy:
                continue
        if state == "Open" and not is_open_claim(c):
            continue
        if state == "Completed" and (is_open_claim(c) or claim_stage(c)[0] == "rejected"):
            continue
        out.append(c)
    return out


def _money(x):
    return f"${x:,.2f}"


def _show_df(df, money_cols=()):
    cfg = {col: st.column_config.NumberColumn(col, format="$%.2f") for col in money_cols if col in df.columns}
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=cfg)


def _group_table(claims, key_fn, label):
    """One row per key (account head, region ...) with claim counts and money columns. Rejected claims are left out."""
    rows = {}
    for c in claims:
        stage = claim_stage(c)[0]
        if stage == "rejected":
            continue
        r = rows.setdefault(key_fn(c), {"Claims": 0, "Total claimed": 0.0, "GST": 0.0, "Paid out": 0.0})
        r["Claims"] += 1
        r["Total claimed"] += float(c.get("total_claim") or 0)
        r["GST"] += float(c.get("gst") or 0)
        if stage == "paid":
            r["Paid out"] += float(c.get("amount_payable") or 0)
    df = pd.DataFrame([{label: k, **v} for k, v in sorted(rows.items())])
    return df


# ---- BAS ------------------------------------------------------------------
def _bas_quarters(today, n=8):
    """The last n quarters of our calendar year (Jan-Mar = Q1, Apr-Jun, Jul-Sep, Oct-Dec = Q4), newest first."""
    y, m = today.year, ((today.month - 1) // 3) * 3 + 1
    out = []
    for _ in range(n):
        start = date(y, m, 1)
        nm, ny = (m + 3, y) if m + 3 <= 12 else (m + 3 - 12, y + 1)
        end = date(ny, nm, 1) - timedelta(days=1)
        qnum = (m - 1) // 3 + 1
        label = f"{start.strftime('%b')} - {end.strftime('%b %Y')}  (Q{qnum} {start.year})"
        out.append((label, start, end))
        m -= 3
        if m < 1:
            m, y = m + 12, y - 1
    return out


def build_bas_lines(claims, start, end, basis, include, head="All"):
    """One line per receipt (or per claim, for older claims saved without receipt lines)."""
    lines = []
    for c in claims:
        stage = claim_stage(c)[0]
        if stage != "paid" and not (include == "Approved and paid" and stage == "approved"):
            continue
        if head != "All" and c.get("account") != head:
            continue
        pdt = _paid_dt(c)
        paid_d = pdt.date() if pdt else None
        base = {"claimant": c.get("name", ""), "region": c.get("region", ""), "voucher": c.get("voucher_no", ""),
                "account": c.get("account", ""), "paid_d": paid_d, "status": claim_stage(c)[1]}
        receipts = c.get("receipts") or []
        if receipts:
            for r in receipts:
                rd, note = _parse_dmy(r.get("date")), ""
                if rd is None:
                    rd, note = _claim_date(c), "Receipt date not readable, claim date used"
                total, gst = float(r.get("amount") or 0), float(r.get("gst") or 0)
                lines.append({**base, "date": rd, "desc": r.get("desc", ""), "total": total, "gst": gst,
                              "net": round(total - gst, 2), "note": note})
        else:
            total, gst = float(c.get("total_claim") or 0), float(c.get("gst") or 0)
            lines.append({**base, "date": _claim_date(c), "desc": c.get("event", ""), "total": total, "gst": gst,
                          "net": round(total - gst, 2), "note": "Older claim: totals only, claim date used"})
    keep = []
    for ln in lines:
        d = ln["date"] if basis == "Receipt date" else ln["paid_d"]
        if d and start <= d <= end:
            keep.append(ln)
    keep.sort(key=lambda x: (x["date"] or date.min, x["claimant"]))
    return keep


def bas_totals(lines):
    total = round(sum(x["total"] for x in lines), 2)
    gst = round(sum(x["gst"] for x in lines), 2)
    return {"total": total, "gst": gst, "net": round(total - gst, 2), "count": len(lines),
            "no_gst": sum(1 for x in lines if x["gst"] <= 0)}


def _fmt_d(d):
    return d.strftime("%d/%m/%Y") if d else ""


def bas_csv_bytes(lines):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Receipt date", "Claimant", "Region", "Voucher No.", "Account head", "Description",
                "Total (incl GST)", "GST", "Net (excl GST)", "Claim paid on", "Claim status", "Note"])
    for x in lines:
        w.writerow([_fmt_d(x["date"]), x["claimant"], x["region"], x["voucher"], x["account"], x["desc"],
                    f"{x['total']:.2f}", f"{x['gst']:.2f}", f"{x['net']:.2f}", _fmt_d(x["paid_d"]), x["status"], x["note"]])
    t = bas_totals(lines)
    w.writerow([])
    w.writerow(["TOTAL", "", "", "", "", "", f"{t['total']:.2f}", f"{t['gst']:.2f}", f"{t['net']:.2f}"])
    return buf.getvalue().encode("utf-8-sig")        # utf-8-sig so Excel opens it correctly


def build_bas_pdf(lines, period_text, basis_text, head_text):
    E = lambda v: xml_escape(str(v if v is not None else ""))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    title = ParagraphStyle("BT", fontName="Helvetica-Bold", fontSize=14, spaceAfter=4)
    body = ParagraphStyle("BB", fontName="Helvetica", fontSize=9, leading=12)
    small = ParagraphStyle("BS", fontName="Helvetica", fontSize=7.5, leading=9)
    small_b = ParagraphStyle("BSB", fontName="Helvetica-Bold", fontSize=7.5, leading=9)
    right = ParagraphStyle("BR", parent=small, alignment=2)
    right_b = ParagraphStyle("BRB", parent=small_b, alignment=2)

    t = bas_totals(lines)
    story = [
        Paragraph("MAJLIS ANSARULLAH AUSTRALIA - BAS Expense Report", title),
        Paragraph(f"<b>Period:</b> {E(period_text)} &nbsp;&nbsp; <b>Counted by:</b> {E(basis_text)} &nbsp;&nbsp; "
                  f"<b>Account head:</b> {E(head_text)}", body),
        Paragraph(f"<b>Generated:</b> {now_local().strftime('%d/%m/%Y %H:%M')}", body),
        Spacer(1, 8),
        Paragraph(f"<b>Total purchases (incl GST):</b> {_money(t['total'])} &nbsp;&nbsp; "
                  f"<b>GST on purchases (1B):</b> {_money(t['gst'])} &nbsp;&nbsp; "
                  f"<b>Net (excl GST):</b> {_money(t['net'])} &nbsp;&nbsp; <b>Receipts:</b> {t['count']}", body),
        Spacer(1, 8),
    ]

    head = ["Date", "Claimant", "Voucher", "Account head", "Description", "Total", "GST", "Net", "Paid on"]
    data = [[Paragraph(f"<b>{h}</b>", small_b) for h in head]]
    for x in lines:
        data.append([
            Paragraph(_fmt_d(x["date"]), small), Paragraph(E(x["claimant"]), small), Paragraph(E(x["voucher"]), small),
            Paragraph(E(x["account"]), small), Paragraph(E(x["desc"]) + (" *" if x["note"] else ""), small),
            Paragraph(_money(x["total"]), right), Paragraph(_money(x["gst"]), right),
            Paragraph(_money(x["net"]), right), Paragraph(_fmt_d(x["paid_d"]), small)])
    data.append([Paragraph("<b>TOTAL</b>", small_b), "", "", "", "", Paragraph(_money(t["total"]), right_b),
                 Paragraph(_money(t["gst"]), right_b), Paragraph(_money(t["net"]), right_b), ""])
    table = Table(data, colWidths=[52, 88, 50, 130, 190, 58, 48, 58, 58], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 3),
        ("SPAN", (0, -1), (4, -1)),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    notes = ["GST is the amount printed on each receipt as entered on the claim. A GST credit normally needs a valid "
             "tax invoice, so check the receipts before lodging. Please confirm figures with your accountant."]
    if any(x["note"] for x in lines):
        notes.append("* Date or detail taken from the claim, not the receipt (see the CSV Note column).")
    for n in notes:
        story.append(Paragraph(E(n), small))
    doc.build(story)
    return buf.getvalue()


# ---- the page ---------------------------------------------------------------
def _ledger_form(kind, title, help_text, heads, service, folder_id, actor):
    with st.form(f"ledger_form_{kind}", clear_on_submit=True):
        st.markdown(f"**{title}**")
        st.caption(help_text)
        head = st.selectbox("Account head", heads, key=f"lf_head_{kind}")
        amount = st.number_input("Amount ($)", value=0.0, step=10.0, format="%.2f",
                                 min_value=None if kind == "set" else 0.0, key=f"lf_amt_{kind}")
        note = st.text_input("Note (optional)", max_chars=200, key=f"lf_note_{kind}")
        go = st.form_submit_button(title, type="primary")
    if go:
        try:
            add_ledger_entry(service, folder_id, kind, head, amount, note, actor)
        except Exception as e:
            st.error(f"Could not save: {e}")
        else:
            st.session_state["sum_notice"] = f"{LEDGER_TYPES[kind]}: {_money(amount)} for {head}."
            st.rerun()


def render_summary_page(auth):
    st.markdown("### 📈 Summary & Tracking")
    if st.button("⬅ Back to Claims Dashboard"):
        st.session_state.admin_view = "dashboard"
        st.rerun()

    notice = st.session_state.pop("sum_notice", None)
    if notice:
        st.success(notice)

    data = load_dashboard_data(ROLE_ADMIN)
    if data is None:
        return
    service, folder_id, claims = data

    ledger_ok = True
    try:
        entries, _ = load_ledger(service, folder_id)
    except Exception as e:
        entries, ledger_ok = [], False
        st.error(f"Could not read the account ledger from Google Drive: {e}")

    heads = all_heads(claims, entries)
    regions = st.session_state.admin_lists.get("regions", [])
    balances = compute_balances(entries, claims, heads)

    tab_over, tab_bal, tab_bas = st.tabs(["📊 Overview", "💰 Account Balances", "🧾 BAS Report"])

    # ------------------------------------------------------------ overview
    with tab_over:
        fys = sorted({y for y in (_claim_year(c) for c in claims) if y} | {str(now_local().year)}, reverse=True)
        f1, f2, f3, f4 = st.columns(4)
        head_f = f1.selectbox("Account head", ["All"] + heads, key="sum_head")
        region_f = f2.selectbox("Region", ["All"] + regions, key="sum_region")
        fy_f = f3.selectbox("Year (Jan-Dec)", ["All"] + fys, key="sum_fy")
        state_label = f4.selectbox("Claims", ["All claims", "Open only", "Completed only"], key="sum_state")
        state_f = {"All claims": "All", "Open only": "Open", "Completed only": "Completed"}[state_label]

        g1, g2, g3 = st.columns(3)
        who_opts = ["All"] + sorted({c.get("name") for c in claims if c.get("name")}, key=str.lower)
        stage_opts = ["All"] + [STAGE_LABELS[k] for k in ("await_nazim", "to_process", "await_president", "approved",
                                                          "paid", "hold", "rejected")]
        who_f = g1.selectbox("Claimant", who_opts, key="sum_who")
        stage_f = g2.selectbox("Status", stage_opts, key="sum_stage")
        text_f = g3.text_input("Search", key="sum_text", placeholder="🔍 Event or voucher no.")

        sub = filter_claims(claims, head_f, region_f, fy_f, state_f)
        if who_f != "All":
            sub = [c for c in sub if c.get("name") == who_f]
        if stage_f != "All":
            sub = [c for c in sub if STAGE_LABELS.get(claim_stage(c)[0]) == stage_f]
        if text_f.strip():
            t = text_f.strip().lower()
            sub = [c for c in sub if t in str(c.get("event", "")).lower() or t in str(c.get("voucher_no", "")).lower()]
        live = [c for c in sub if claim_stage(c)[0] != "rejected"]
        stages = [claim_stage(c)[0] for c in live]
        paid_out = sum(float(c.get("amount_payable") or 0) for c, s in zip(live, stages) if s == "paid")
        to_pay = sum(float(c.get("amount_payable") or 0) for c, s in zip(live, stages) if s == "approved")
        in_pipe = sum(float(c.get("amount_payable") or 0) for c, s in zip(live, stages)
                      if s not in ("approved", "paid"))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Claims (excl. rejected)", len(live))
        m2.metric("Total claimed", _money(sum(float(c.get("total_claim") or 0) for c in live)))
        m3.metric("Total GST", _money(sum(float(c.get("gst") or 0) for c in live)))
        m4.metric("Paid out", _money(paid_out))
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("Approved, still to pay", _money(to_pay))
        m6.metric("Still in approval", _money(in_pipe))
        m7.metric("Open claims", sum(1 for c in live if is_open_claim(c)))
        m8.metric("Completed claims", sum(1 for c in live if not is_open_claim(c)))

        if head_f != "All":
            b = balances.get(head_f, {})
            st.metric(f"Balance now: {head_f}", _money(b["balance"]) if b.get("has_base") else "Not set yet")
        if not sub:
            st.info("No claims match these filters.")
        else:
            st.markdown("#### By status")
            by_status = {}
            for c in sub:
                label = STAGE_LABELS.get(claim_stage(c)[0], "Other")
                r = by_status.setdefault(label, {"Claims": 0, "Total claimed": 0.0, "Amount to pay": 0.0})
                r["Claims"] += 1
                r["Total claimed"] += float(c.get("total_claim") or 0)
                r["Amount to pay"] += float(c.get("amount_payable") or 0)
            order = [STAGE_LABELS[k] for k in ("await_nazim", "to_process", "await_president", "approved", "paid",
                                              "hold", "rejected")]
            _show_df(pd.DataFrame([{"Status": k, **by_status[k]} for k in order if k in by_status]),
                     ("Total claimed", "Amount to pay"))

            st.markdown("#### By account head")
            df_head = _group_table(sub, lambda c: c.get("account") or "(none)", "Account head")
            if not df_head.empty:
                df_head["Balance now"] = [
                    balances[h]["balance"] if balances.get(h, {}).get("has_base") else None
                    for h in df_head["Account head"]]
                _show_df(df_head, ("Total claimed", "GST", "Paid out", "Balance now"))
                if len(df_head) > 1:
                    st.bar_chart(df_head.set_index("Account head")[["Total claimed", "Paid out"]])
                st.caption("Balance now is the live balance for the head (all dates). "
                           "Heads with no balance entered show empty.")

            st.markdown("#### By region")
            df_reg = _group_table(sub, lambda c: c.get("region") or "(none)", "Region")
            if not df_reg.empty:
                _show_df(df_reg, ("Total claimed", "GST", "Paid out"))

    # ------------------------------------------------------------ balances
    with tab_bal:
        st.caption("Enter what is in an account head today with **Set balance**. After that, the balance updates by "
                   "itself: **Money added** goes up, **Other payment out** (for example an advance you handed out) "
                   "goes down, and every claim you mark **Paid** after that moment is taken off "
                   "(the amount actually paid, after any advance settled).")
        rows = []
        for h in heads:
            b = balances[h]
            rows.append({"Account head": h,
                         "Balance now": b["balance"] if b["has_base"] else None,
                         "Set to": b["start"] if b["has_base"] else None,
                         "Set on": b["base_date"] or "not set",
                         "Money added": b["added"], "Other payments out": b["manual_out"],
                         "Claims paid": b["claims_paid"]})
        if rows:
            df_bal = pd.DataFrame(rows)
            total_now = sum(r["Balance now"] for r in rows if r["Balance now"] is not None)
            st.metric("Total balance across heads that have a balance set", _money(total_now))
            _show_df(df_bal, ("Balance now", "Set to", "Money added", "Other payments out", "Claims paid"))
        else:
            st.info("No account heads are configured yet. Add them in Settings.")

        if heads and ledger_ok:
            c1, c2, c3 = st.columns(3)
            with c1:
                _ledger_form("set", "Set balance", "Type the real balance of the head right now. "
                             "This restarts the count from today.", heads, service, folder_id, auth["name"])
            with c2:
                _ledger_form("add", "Add money", "Money received into the head (a transfer, a grant, a deposit).",
                             heads, service, folder_id, auth["name"])
            with c3:
                _ledger_form("deduct", "Other payment out", "Money that left the head but is not a claim in this app "
                             "(for example an advance, or a bank fee).", heads, service, folder_id, auth["name"])

        st.markdown("#### Ledger history")
        lf1, lf2, lf3, lf4 = st.columns([2, 2, 2, 2])
        pick = lf1.selectbox("Show entries for", ["All"] + heads, key="ledger_pick")
        type_pick = lf2.selectbox("Type", ["All"] + list(LEDGER_TYPES.values()), key="ledger_type")
        l_sort = lf3.selectbox("Sort by", ["Date entered", "Amount", "Account head", "Type"], key="ledger_sort")
        l_dir = lf4.selectbox("Order", ["Descending", "Ascending"], key="ledger_dir")
        l_text = st.text_input("Search notes", key="ledger_text", placeholder="🔍 Search the note or who entered it")
        shown = [e for e in entries if (pick == "All" or e.get("account") == pick)
                 and (type_pick == "All" or LEDGER_TYPES.get(e.get("type"), e.get("type")) == type_pick)
                 and (not l_text.strip() or l_text.strip().lower() in
                      f"{e.get('note', '')} {e.get('by', '')}".lower())]
        l_key = {"Date entered": lambda e: e.get("ts", ""), "Amount": lambda e: float(e.get("amount") or 0),
                 "Account head": lambda e: (str(e.get("account", "")).lower(), e.get("ts", "")),
                 "Type": lambda e: (str(LEDGER_TYPES.get(e.get("type"), e.get("type"))), e.get("ts", ""))}[l_sort]
        shown.sort(key=l_key, reverse=l_dir == "Descending")
        if not shown:
            st.info("No ledger entries yet.")
        else:
            _show_df(pd.DataFrame([{"Date": e.get("date", ""), "Account head": e.get("account", ""),
                                    "Type": LEDGER_TYPES.get(e.get("type"), e.get("type")),
                                    "Amount": float(e.get("amount") or 0), "Note": e.get("note", ""),
                                    "By": e.get("by", "")} for e in shown]), ("Amount",))
            if ledger_ok:
                with st.expander("Delete a wrong entry"):
                    labels = {f"{e.get('date', '')} | {e.get('account', '')} | "
                              f"{LEDGER_TYPES.get(e.get('type'), e.get('type'))} | {_money(float(e.get('amount') or 0))}"
                              f" | {e.get('note', '')}": e["id"] for e in shown}
                    choice = st.selectbox("Entry", list(labels.keys()), key="ledger_del_pick")
                    sure = st.checkbox("Yes, delete this entry", key="ledger_del_sure")
                    if st.button("Delete entry", disabled=not sure):
                        try:
                            delete_ledger_entry(service, folder_id, labels[choice])
                        except Exception as e:
                            st.error(f"Could not delete: {e}")
                        else:
                            st.session_state["sum_notice"] = "Ledger entry deleted."
                            st.rerun()

    # ------------------------------------------------------------ BAS
    with tab_bas:
        st.caption("Lists the GST on every receipt in a period, ready for your BAS (G11 total purchases and 1B GST credits). "
                   "Only the amounts typed on the claims are used.")
        today = now_local().date()
        quarters = _bas_quarters(today)
        presets = [q[0] for q in quarters] + ["Custom dates"]
        preset = st.selectbox("Period", presets, key="bas_preset")
        if preset == "Custom dates":
            d_from_default, d_to_default = today.replace(day=1), today
        else:
            _, d_from_default, d_to_default = next(q for q in quarters if q[0] == preset)
        b1, b2, b3, b4 = st.columns(4)
        d_from = b1.date_input("From", value=d_from_default, key=f"bas_from_{preset}", format="DD/MM/YYYY")
        d_to = b2.date_input("To", value=d_to_default, key=f"bas_to_{preset}", format="DD/MM/YYYY")
        basis = b3.selectbox("Count receipts by", ["Receipt date", "Date claim was paid"], key="bas_basis")
        include = b4.selectbox("Include claims", ["Approved and paid", "Paid only"], key="bas_include",
                               help="'Date claim was paid' always uses paid claims only.")
        bas_head = st.selectbox("Account head", ["All"] + heads, key="bas_head")

        basis_key = "Receipt date" if basis == "Receipt date" else "Paid date"
        if d_from > d_to:
            st.error("The From date is after the To date.")
        else:
            lines = build_bas_lines(claims, d_from, d_to, basis_key, include, bas_head)
            s1, s2, s3 = st.columns([2, 2, 3])
            bas_sort = s1.selectbox("Sort by", ["Receipt date", "Claimant", "Voucher No.", "Account head", "Total", "GST"],
                                    key="bas_sort")
            bas_dir = s2.selectbox("Order", ["Ascending", "Descending"], key="bas_dir")
            bas_text = s3.text_input("Search the table", key="bas_text", placeholder="🔍 Claimant, description or voucher no.")
            bas_key = {"Receipt date": lambda x: str(x["date"] or ""), "Claimant": lambda x: str(x["claimant"]).lower(),
                       "Voucher No.": lambda x: _voucher_sort_key({"voucher_no": x["voucher"]}),
                       "Account head": lambda x: str(x["account"]).lower(), "Total": lambda x: x["total"],
                       "GST": lambda x: x["gst"]}[bas_sort]
            lines = sorted(lines, key=bas_key, reverse=bas_dir == "Descending")
            t = bas_totals(lines)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Total purchases (incl GST)", _money(t["total"]))
            k2.metric("GST on purchases (1B)", _money(t["gst"]))
            k3.metric("Net (excl GST)", _money(t["net"]))
            k4.metric("Receipts / with no GST", f"{t['count']} / {t['no_gst']}")

            if not lines:
                st.info("No receipts match this period. Check the dates, and that the claims are approved or paid.")
            else:
                if any(x["note"] for x in lines):
                    st.warning("Some lines use the claim date because the receipt date was missing, or the claim is "
                               "older than receipt-line storage. See the Note column.")
                table_lines = [x for x in lines if not bas_text.strip() or bas_text.strip().lower() in
                               f"{x['claimant']} {x['desc']} {x['voucher']}".lower()]
                df_bas = pd.DataFrame([{
                    "Receipt date": _fmt_d(x["date"]), "Claimant": x["claimant"], "Voucher No.": x["voucher"],
                    "Account head": x["account"], "Description": x["desc"], "Total": x["total"], "GST": x["gst"],
                    "Net": x["net"], "Paid on": _fmt_d(x["paid_d"]), "Note": x["note"]} for x in table_lines])
                if bas_text.strip():
                    st.caption(f"Showing {len(table_lines)} of {len(lines)} receipts. The totals above and the downloads "
                               "always cover the whole period; only the order follows the sort choice.")
                _show_df(df_bas, ("Total", "GST", "Net"))

                st.markdown("#### GST by account head")
                by_head = {}
                for x in lines:
                    r = by_head.setdefault(x["account"] or "(none)", {"Receipts": 0, "Total": 0.0, "GST": 0.0})
                    r["Receipts"] += 1
                    r["Total"] += x["total"]
                    r["GST"] += x["gst"]
                _show_df(pd.DataFrame([{"Account head": k, **v} for k, v in sorted(by_head.items())]),
                         ("Total", "GST"))

                period_text = f"{_fmt_d(d_from)} to {_fmt_d(d_to)}"
                stamp = f"{d_from:%Y%m%d}_{d_to:%Y%m%d}"
                try:
                    pdf_bytes = build_bas_pdf(lines, period_text, basis, bas_head)
                except Exception as e:
                    pdf_bytes = None
                    st.error(f"Could not build the PDF: {e}")
                dl1, dl2, _ = st.columns([1.4, 1.4, 5])
                dl1.download_button("📥 Download CSV (Excel)", data=bas_csv_bytes(lines),
                                    file_name=f"BAS_report_{stamp}.csv", mime="text/csv")
                if pdf_bytes:
                    dl2.download_button("📥 Download PDF", data=pdf_bytes,
                                        file_name=f"BAS_report_{stamp}.pdf", mime="application/pdf")


# -------------------------------------------------------------------------
# ADMIN: MERGE OPEN CLAIMS
# -------------------------------------------------------------------------
def render_merge_page(auth):
    st.markdown("### 🔗 Merge Open Claims")
    if st.button("⬅ Back to Claims Dashboard"):
        st.session_state["admin_view"] = "dashboard"
        st.rerun()

    data = load_dashboard_data(ROLE_ADMIN)
    if data is None:
        return
    service, folder_id, claims = data
    ids = st.session_state.get("merge_ids") or []
    picked = sorted([c for c in claims if c["id"] in ids], key=lambda c: c.get("submitted_ts", ""))
    if len(picked) < 2:
        st.error("Select at least two open claims on the Open Claims tab first.")
        return

    st.caption("One consolidated voucher page is placed first (every receipt line of every claim, one total), "
               "followed by the pages of each claim. The result is saved to Google Drive as a new claim "
               "(Voucher No_Claimant_Event_Date.pdf) and the original claims move to the Merged tab.")
    st.dataframe(pd.DataFrame([{
        "Claimant": c.get("name", ""), "Date": c.get("submitted_on", ""), "Event": c.get("event", ""),
        "Receipts": len(c.get("receipts") or []) or 1, "Total claim": float(c.get("total_claim") or 0),
        "GST": float(c.get("gst") or 0), "Advance": float(c.get("advance") or 0)} for c in picked]),
        hide_index=True, use_container_width=True,
        column_config={k: st.column_config.NumberColumn(k, format="$%.2f") for k in ("Total claim", "GST", "Advance")})

    total = sum(float(c.get("total_claim") or 0) for c in picked)
    adv = sum(float(c.get("advance") or 0) for c in picked)
    m1, m2, m3 = st.columns(3)
    m1.metric("Consolidated total claim", _money(total))
    m2.metric("Advance settled", _money(adv))
    m3.metric("Amount to pay", _money(total - adv))

    tag = "_".join(c["id"] for c in picked)
    event = st.text_input("Event description for the merged claim", value=picked[0].get("event", ""), key=f"mg_event_{tag}")
    voucher = st.text_input("Voucher No. for the merged claim (can also be added later)", max_chars=20, key=f"mg_vno_{tag}")
    keep = st.checkbox("Keep each claim's own first (voucher) page after the consolidated page", value=True, key=f"mg_keep_{tag}")

    if st.button("🔗 Merge and save to Google Drive", type="primary"):
        try:
            with st.spinner("Merging the PDFs and saving to Google Drive..."):
                record = merge_claims(service, folder_id, [c["id"] for c in picked], auth["role"], auth["name"],
                                      event, voucher, keep)
        except Exception as e:
            st.error(f"Could not merge: {e}")
        else:
            for c in picked:
                st.session_state.pop(f"mrg_{c['id']}", None)
            st.session_state["merge_ids"] = []
            st.session_state["admin_view"] = "dashboard"
            st.session_state["dash_notice"] = (f"Merged {len(picked)} claims into one consolidated claim "
                                               f"({record['file_name']}). It is now in To Process.")
            st.rerun()


# -------------------------------------------------------------------------
# ADMIN: AMEND PDF (all claim-form fields prefilled and editable)
# -------------------------------------------------------------------------
def _account_choices_for(region):
    saved = st.session_state.admin_lists.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
    return saved.get("National", []) if region == "National" else saved.get("General_Regional", [])


def _options_with(current, options):
    """The option list, plus the current value if it is no longer in the configured list."""
    opts = list(options)
    if current and current not in opts:
        opts.insert(0, current)
    return opts or ["(none configured)"]


def _am_set_row(rid, line=None, fp=None):
    line = line or {}
    st.session_state[f"am_date_{rid}"] = str(line.get("date", ""))
    st.session_state[f"am_desc_{rid}"] = str(line.get("desc", ""))
    st.session_state[f"am_amt_{rid}"] = float(line.get("amount") or 0.0)
    st.session_state[f"am_gst_{rid}"] = float(line.get("gst") or 0.0)
    st.session_state[f"am_fp_{rid}"] = fp or {}


def _am_add_row():
    n = st.session_state.get("am_ctr", 0)
    rid = f"a{n}"
    st.session_state["am_ctr"] = n + 1
    st.session_state["am_rows"].append(rid)
    _am_set_row(rid)


def _am_remove_row(rid):
    st.session_state["am_rows"].remove(rid)
    for prefix in ("am_date_", "am_desc_", "am_amt_", "am_gst_", "am_fp_"):
        st.session_state.pop(f"{prefix}{rid}", None)


def _init_amend_state(service, claim):
    """Copies the claim's details into the editor. Claims saved before details were stored are read back from the PDF."""
    for key in [k for k in st.session_state.keys() if str(k).startswith("am_")]:
        del st.session_state[key]

    info = {}
    if not claim.get("receipts") or not claim.get("bsb") or not claim.get("account"):
        try:
            pdf_bytes = service.files().get_media(fileId=claim["file_id"], supportsAllDrives=True).execute()
            info = parse_voucher_fields(pdf_bytes) or {}
        except Exception:
            info = {}

    def pick(key, pdf_key=None):
        return claim.get(key) or info.get(pdf_key or key) or ""

    region = pick("region")
    st.session_state["am_name"] = pick("name")
    st.session_state["am_email"] = claim.get("claimant_email", "")
    st.session_state["am_event"] = pick("event")
    st.session_state["am_bsb"] = pick("bsb")
    st.session_state["am_accnum"] = pick("acc_num")
    st.session_state["am_region"] = region
    st.session_state[f"am_majlis_{region}"] = pick("majlis")
    st.session_state[f"am_account_{region == 'National'}"] = pick("account")
    advance = claim.get("advance") if claim.get("advance") is not None else info.get("advance", 0.0)
    st.session_state["am_adv_mode"] = "Yes" if (advance or 0) > 0 else "No"
    st.session_state["am_advance"] = float(advance or 0.0)

    lines = claim.get("receipts") or info.get("rows") or []
    rows = []
    for n, line in enumerate(lines):
        rid = f"a{n}"
        rows.append(rid)
        _am_set_row(rid, line, {k: line[k] for k in ("exact", "sig", "date_detected") if k in line})
    if not rows:
        rows = ["a0"]
        _am_set_row("a0")
    st.session_state["am_rows"] = rows
    st.session_state["am_ctr"] = len(rows)
    st.session_state["am_recovered"] = bool(info)
    st.session_state["amend_init"] = claim["id"]


def _leave_amend(notice=None):
    st.session_state["amend_init"] = None
    st.session_state["amend_claim_id"] = None
    st.session_state["admin_view"] = "dashboard"
    if notice:
        st.session_state["dash_notice"] = notice


def render_amend_page(auth):
    st.markdown("### ✏️ Amend PDF")
    if st.button("⬅ Cancel and go back to Claims Dashboard"):
        _leave_amend()
        st.rerun()

    data = load_dashboard_data(ROLE_ADMIN)
    if data is None:
        return
    service, folder_id, claims = data
    claim_id = st.session_state.get("amend_claim_id")
    claim = next((c for c in claims if c["id"] == claim_id), None)
    if claim is None:
        st.error("That claim could not be found. It may have been deleted. Please go back and try again.")
        return

    if st.session_state.get("amend_init") != claim_id:
        _init_amend_state(service, claim)

    st.caption(f"Claim from **{_md_escape(claim.get('name', ''))}**, submitted {claim.get('submitted_on', '')}. "
               "Change anything below and save. The voucher page of the PDF in Google Drive is rebuilt "
               "(same file, same link). The receipt pages, the voucher number and the approval status are kept.")
    if st.session_state.get("am_recovered"):
        st.info("This claim was submitted before every detail was stored, so some fields were read back from its PDF. "
                "Please check each field, especially the email address, which is not on the PDF.")

    regions = st.session_state.admin_lists.get("regions", [])
    majlis_map = st.session_state.admin_lists.get("regional_majlis", {})

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Name (Payee Name)", key="am_name")
        email = st.text_input("Email Address", key="am_email")
        event = st.text_input("Description of Event (Expense Type)", key="am_event")
        region = st.selectbox("Select Region / National",
                              options=_options_with(st.session_state.get("am_region"), regions), key="am_region")
        if region != claim.get("region"):
            st.warning("Changing the region changes which Nazim e Ala can see this claim.")
        majlis = st.selectbox(
            "Select your Majlis / Office",
            options=_options_with(st.session_state.get(f"am_majlis_{region}"), majlis_map.get(region, [])),
            key=f"am_majlis_{region}")
        account = st.selectbox(
            "Select Tracking Account Code",
            options=_options_with(st.session_state.get(f"am_account_{region == 'National'}"), _account_choices_for(region)),
            key=f"am_account_{region == 'National'}")
    with col2:
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7, key="am_bsb")
        acc_num = st.text_input("Account Number", key="am_accnum")
        st.markdown("**💵 Advance Settlement**")
        adv_mode = st.radio("Is an advance being settled in this claim?", ["No", "Yes"], horizontal=True, key="am_adv_mode")
        advance = 0.0
        if adv_mode == "Yes":
            advance = st.number_input("Advance Amount Being Settled ($)", min_value=0.0, format="%.2f", key="am_advance")

    st.markdown("---")
    st.markdown("#### 🧾 Receipt lines")
    rows = st.session_state["am_rows"]
    for n, rid in enumerate(list(rows), start=1):
        r1, r2, r3, r4, r5 = st.columns([1.6, 3.4, 1.5, 1.5, 1.2])
        r1.text_input(f"Receipt Date #{n}", key=f"am_date_{rid}")
        r2.text_input(f"Item Description #{n}", key=f"am_desc_{rid}")
        r3.number_input(f"Amount ($) #{n}", min_value=0.0, format="%.2f", key=f"am_amt_{rid}")
        r4.number_input(f"GST ($) #{n}", min_value=0.0, format="%.2f", key=f"am_gst_{rid}")
        if len(rows) > 1:
            r5.markdown("&nbsp;")
            r5.button("🗑 Remove", key=f"am_rm_{rid}", on_click=_am_remove_row, args=(rid,))
    st.button("➕ Add receipt line", key="am_add_line", on_click=_am_add_row)

    extra_files = st.file_uploader("Attach more receipt files (optional, added after the existing pages)",
                                   type=["png", "jpg", "jpeg", "heic", "heif", "pdf"], accept_multiple_files=True, key="am_files")

    line_values = [{
        "date": st.session_state.get(f"am_date_{rid}", ""),
        "desc": st.session_state.get(f"am_desc_{rid}", ""),
        "amount": float(st.session_state.get(f"am_amt_{rid}", 0.0) or 0.0),
        "gst": float(st.session_state.get(f"am_gst_{rid}", 0.0) or 0.0),
        **(st.session_state.get(f"am_fp_{rid}") or {}),
    } for rid in rows]
    total = round(sum(r["amount"] for r in line_values), 2)
    gst_total = round(sum(r["gst"] for r in line_values), 2)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Claim", f"${total:,.2f}")
    m2.metric("Total GST", f"${gst_total:,.2f}")
    m3.metric("Amount to be paid (after advance)", f"${total - advance:,.2f}")

    problems = []
    if not name.strip():
        problems.append("Full name is required.")
    if not event.strip():
        problems.append("Event description is required.")
    if advance > total + 0.001:
        problems.append(f"The advance (${advance:.2f}) is more than the total claim (${total:.2f}).")
    for p in problems:
        st.error(p)

    save_col, _ = st.columns([2, 6])
    if save_col.button("💾 Save amended PDF", type="primary", disabled=bool(problems)):
        form = {"name": name, "claimant_email": email, "event": event, "region": region, "majlis": majlis,
                "account": account, "bsb": bsb, "acc_num": acc_num, "advance": advance}
        attachments = [(f.name, f.getvalue()) for f in (extra_files or [])]
        try:
            with st.spinner("Rebuilding the PDF and saving it to Google Drive..."):
                amend_claim(service, folder_id, claim_id, auth["role"], auth["name"], form, line_values, attachments)
        except Exception as e:
            st.error(f"Could not amend the PDF: {e}")
        else:
            _leave_amend(f"PDF amended and saved to Google Drive for {claim.get('name', '')}.")
            st.rerun()


# -------------------------------------------------------------------------
# ADMIN: UPLOAD AN ALREADY PROCESSED CLAIM
# For claims finished before this system existed. The uploaded PDF is already complete (summary page + receipts), so it is
# saved to Drive EXACTLY as uploaded: no new summary page, no changes inside the file. Only the Drive file name follows the
# usual VoucherNo_Claimant_Event_Date.pdf pattern. The details are read from the PDF's own summary page into fields
# (for the register, dashboard, balances and BAS) and the claim can be saved as Paid, bypassing every approval stage.
# -------------------------------------------------------------------------
def _natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", str(name))]


def _is_voucher_pdf(data):
    """True when page 1 of the PDF is one of our Expense Payment Voucher summary pages."""
    try:
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text() or ""
    except Exception:
        return False
    return "Payee Name" in text and "Expense Payment Voucher" in text


def save_existing_claim(service, folder_id, role, actor, form, rows, pdf_bytes, claim_date, voucher_no,
                        paid, paid_date, flags=None):
    """Admin only. Saves an already complete claim PDF to Drive unchanged (only the file name is set) and adds the claim to the
    register. paid=True saves it as Paid with the Nazim e Ala, Admin and President stages bypassed."""
    if role != ROLE_ADMIN:
        raise PermissionError("Only the Admin can upload a processed claim.")
    name = (form.get("name") or "").strip()
    event = (form.get("event") or "").strip()
    if not name or not event:
        raise ValueError("Name and event description are required.")
    if not rows:
        raise ValueError("At least one receipt line is needed.")

    clean = []
    for r in rows:
        amount = round(float(r.get("amount") or 0), 2)
        gst = round(float(r.get("gst") or 0), 2)
        if amount < 0 or gst < 0:
            raise ValueError("Amounts cannot be negative.")
        line = {"date": str(r.get("date", "")).strip(), "desc": str(r.get("desc", "")).strip(),
                "amount": amount, "gst": gst}
        for k in ("exact", "sig", "date_detected"):
            if k in r:
                line[k] = r[k]
        clean.append(line)
    total = round(sum(r["amount"] for r in clean), 2)
    gst_total = round(sum(r["gst"] for r in clean), 2)
    advance = round(float(form.get("advance") or 0), 2)
    if advance > total + 0.001:
        raise ValueError("The advance cannot be more than the total claim.")
    voucher_no = (voucher_no or "").strip()[:20]
    submitted_on = claim_date.strftime("%d/%m/%Y")

    voucher_pages = _voucher_page_count(PdfReader(io.BytesIO(pdf_bytes)))
    now = now_local()
    drive_name = build_drive_filename(voucher_no, name, event, submitted_on)
    file_id, file_link = upload_pdf_to_drive(service, folder_id, drive_name, pdf_bytes)   # the PDF itself is not changed
    today = now.strftime("%d/%m/%Y")
    record = {
        "id": uuid.uuid4().hex[:10], "submitted_on": submitted_on, "submitted_ts": now.isoformat(),
        "name": name, "claimant_email": form.get("claimant_email", ""), "event": event,
        "region": form["region"], "majlis": form["majlis"], "account": form["account"],
        "bsb": form["bsb"], "acc_num": form["acc_num"],
        "total_claim": total, "gst": gst_total, "advance": advance, "amount_payable": round(total - advance, 2),
        "file_name": drive_name, "file_id": file_id, "file_link": file_link, "voucher_no": voucher_no,
        "voucher_pages": voucher_pages, "receipts": clean, "flags": flags or [], "comments": [],
        "paid": False, "complete": False, "uploaded_by": actor,
        "nazim_status": STATUS_BYPASSED, "nazim_by": "Upload", "nazim_on": today,
        "admin_status": STATUS_PENDING, "president_status": STATUS_PENDING, "history": [],
    }
    _log(record, actor, f"Existing processed claim uploaded by Admin ({len(clean)} receipt line{'s' if len(clean) != 1 else ''}, "
                        f"${total:,.2f}); PDF saved unchanged")
    if paid:
        paid_str = paid_date.strftime("%d/%m/%Y")
        record.update({
            "admin_status": STATUS_APPROVED, "admin_by": actor, "admin_on": today,
            "president_status": STATUS_BYPASSED, "president_by": actor, "president_on": today,
            "paid": True, "paid_by": actor, "paid_on": paid_str,
            "paid_ts": datetime(paid_date.year, paid_date.month, paid_date.day, 12, 0, tzinfo=LOCAL_TZ).isoformat(),
            "complete": True, "complete_by": actor, "complete_on": today,
        })
        _log(record, actor, f"Saved as already paid on {paid_str}: Nazim e Ala, Admin and President approval stages bypassed")
    add_claim_to_register(service, folder_id, record)
    return record


def _up_reset():
    for key in [k for k in st.session_state.keys() if str(k).startswith("up_")]:
        del st.session_state[key]


def _up_set_row(rid, line=None):
    line = line or {}
    st.session_state[f"up_date_{rid}"] = str(line.get("date", ""))
    st.session_state[f"up_desc_{rid}"] = str(line.get("desc", ""))
    st.session_state[f"up_amt_{rid}"] = float(line.get("amount") or 0.0)
    st.session_state[f"up_gst_{rid}"] = float(line.get("gst") or 0.0)


def _up_add_row():
    n = st.session_state.get("up_ctr", 0)
    rid = f"u{n}"
    st.session_state["up_ctr"] = n + 1
    st.session_state["up_rows"].append(rid)
    _up_set_row(rid)


def _up_remove_row(rid):
    st.session_state["up_rows"].remove(rid)
    for prefix in ("up_date_", "up_desc_", "up_amt_", "up_gst_"):
        st.session_state.pop(f"{prefix}{rid}", None)


def _up_autofill():
    """When the name is typed: fill email, region, majlis and account from the members list / earlier claims."""
    info = lookup_person(st.session_state.get("up_name", ""), _members(), _earlier_claims())
    if not info:
        return
    cfg = st.session_state.admin_lists
    regions = cfg.get("regions", [])
    if info.get("email"):
        st.session_state["up_email"] = info["email"]
    if info.get("region") in regions:
        st.session_state["up_region"] = info["region"]
    region = st.session_state.get("up_region") or (regions[0] if regions else "")
    if info.get("majlis") in cfg.get("regional_majlis", {}).get(region, []):
        st.session_state[f"up_majlis_{region}"] = info["majlis"]
    if info.get("account") in _account_choices_for(region):
        st.session_state[f"up_account_{region == 'National'}"] = info["account"]


def _up_read(fname, data):
    """Reads the claim's own summary page (page 1) into the fields. The PDF itself is never changed."""
    _up_reset()
    has_summary = _is_voucher_pdf(data)
    info = (parse_voucher_fields(data) or {}) if has_summary else {}
    claim_date = None
    try:
        reader = PdfReader(io.BytesIO(data))
        st.session_state["up_pages"] = len(reader.pages)
        m = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})", reader.pages[0].extract_text() or "")
        claim_date = _parse_dmy(m.group(1)) if m else None
    except Exception:
        st.session_state["up_pages"] = 0

    regions = st.session_state.admin_lists.get("regions", [])
    region = info.get("region") or (regions[0] if regions else "")
    st.session_state["up_has_summary"] = has_summary
    st.session_state["up_name"] = info.get("name", "")
    st.session_state["up_email"] = ""
    st.session_state["up_event"] = info.get("event", "")
    st.session_state["up_bsb"] = info.get("bsb", "")
    st.session_state["up_accnum"] = info.get("acc_num", "")
    st.session_state["up_region"] = region
    if info.get("majlis"):
        st.session_state[f"up_majlis_{region}"] = info["majlis"]
    if info.get("account"):
        st.session_state[f"up_account_{region == 'National'}"] = info["account"]
    advance = float(info.get("advance") or 0.0)
    st.session_state["up_adv_mode"] = "Yes" if advance > 0 else "No"
    st.session_state["up_advance"] = advance
    m = re.match(r"\s*(\d{1,8})(?!\d)", fname)
    st.session_state["up_vno"] = m.group(1) if m else ""
    st.session_state["up_claim_date"] = claim_date or now_local().date()
    st.session_state["up_paid_date"] = claim_date or now_local().date()
    st.session_state["up_paid_mode"] = "Yes"
    rows = []
    for n, line in enumerate(info.get("rows", [])):
        rid = f"u{n}"
        rows.append(rid)
        _up_set_row(rid, line)
    if not rows:
        rows = ["u0"]
        _up_set_row("u0")
    st.session_state["up_rows"] = rows
    st.session_state["up_ctr"] = len(rows)
    if info.get("name"):
        _up_autofill()


def render_upload_claim_page(auth):
    st.markdown("### 📤 Upload Processed Claim")
    if st.button("⬅ Back to Claims Dashboard"):
        _up_reset()
        st.session_state["upq_i"] = 0
        st.session_state["admin_view"] = "dashboard"
        st.rerun()

    notice = st.session_state.pop("upl_notice", None)
    if notice:
        st.success(notice)

    data = load_dashboard_data(ROLE_ADMIN)
    if data is None:
        return
    service, folder_id, claims = data

    st.caption("For claims that were completed before this system. Upload the finished claim PDF (summary page and receipts "
               "already inside). It is saved to Google Drive exactly as it is, with nothing added or changed in the file; only "
               "the Drive name becomes VoucherNo_Claimant_Event_Date.pdf. The details are read from its summary page so the "
               "claim appears in the dashboard, balances and BAS report. You can upload several files; they are handled "
               "one at a time in name order (1, 2, 3 ...).")
    upl_v = st.session_state.get("upl_v", 0)
    uploaded = st.file_uploader("Completed claim PDF(s)", type=["pdf"], accept_multiple_files=True,
                                key=f"upl_files_{upl_v}")
    if not uploaded:
        _up_reset()
        st.session_state.pop("upq_sig", None)
        return

    files = [(f.name, f.getvalue()) for f in sorted(uploaded, key=lambda f: _natural_key(f.name))]
    queue_sig = [(n, len(d)) for n, d in files]
    if st.session_state.get("upq_sig") != queue_sig:
        st.session_state["upq_sig"] = queue_sig
        st.session_state["upq_i"] = 0
        _up_reset()
    i = st.session_state.get("upq_i", 0)
    if i >= len(files):
        st.success(f"All {len(files)} file(s) have been dealt with.")
        if st.button("Upload more claims"):
            st.session_state["upl_v"] = upl_v + 1
            st.session_state.pop("upq_sig", None)
            st.rerun()
        return

    fname, pdf_bytes = files[i]
    st.markdown(f"#### Claim {i + 1} of {len(files)}: {_md_escape(fname)}")
    file_sig = (i, fname, len(pdf_bytes))
    if st.session_state.get("up_sig") != file_sig:
        with st.spinner("Reading the claim ..."):
            _up_read(fname, pdf_bytes)
        st.session_state["up_sig"] = file_sig

    if st.session_state.get("up_has_summary"):
        st.info(f"The summary page was found and read ({st.session_state.get('up_pages', 0)} pages in the file). "
                "Please check the fields below. The PDF is saved unchanged, so corrections here change the dashboard "
                "record only, not the PDF. The email address is not on the PDF, so please add it if you want it recorded.")
    else:
        st.warning("No summary page was found on page 1 of this file, so nothing could be read. You can type the details "
                   "in below. The PDF is still saved unchanged.")

    regions = st.session_state.admin_lists.get("regions", [])
    majlis_map = st.session_state.admin_lists.get("regional_majlis", {})

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Name (Payee Name)", key="up_name", on_change=_up_autofill)
        email = st.text_input("Email Address", key="up_email")
        event = st.text_input("Description of Event (Expense Type)", key="up_event")
        region = st.selectbox("Select Region / National",
                              options=_options_with(st.session_state.get("up_region"), regions), key="up_region")
        majlis = st.selectbox(
            "Select Majlis / Office",
            options=_options_with(st.session_state.get(f"up_majlis_{region}"), majlis_map.get(region, [])),
            key=f"up_majlis_{region}")
        account = st.selectbox(
            "Select Tracking Account Code",
            options=_options_with(st.session_state.get(f"up_account_{region == 'National'}"), _account_choices_for(region)),
            key=f"up_account_{region == 'National'}")
    with col2:
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7, key="up_bsb")
        acc_num = st.text_input("Account Number", key="up_accnum")
        voucher_no = st.text_input("Voucher No. (used in the Drive file name and the dashboard)", max_chars=20, key="up_vno")
        claim_date = st.date_input("Claim date", key="up_claim_date", format="DD/MM/YYYY")
        adv_mode = st.radio("Was an advance settled in this claim?", ["No", "Yes"], horizontal=True, key="up_adv_mode")
        advance = 0.0
        if adv_mode == "Yes":
            advance = st.number_input("Advance Amount Settled ($)", min_value=0.0, format="%.2f", key="up_advance")

    st.markdown("---")
    st.markdown("#### 🧾 Receipt lines (read from the summary page; used for totals, GST and BAS only)")
    rows = st.session_state["up_rows"]
    for n, rid in enumerate(list(rows), start=1):
        r1, r2, r3, r4, r5 = st.columns([1.6, 3.4, 1.5, 1.5, 1.2])
        r1.text_input(f"Receipt Date #{n}", key=f"up_date_{rid}")
        r2.text_input(f"Item Description #{n}", key=f"up_desc_{rid}")
        r3.number_input(f"Amount ($) #{n}", min_value=0.0, format="%.2f", key=f"up_amt_{rid}")
        r4.number_input(f"GST ($) #{n}", min_value=0.0, format="%.2f", key=f"up_gst_{rid}")
        if len(rows) > 1:
            r5.markdown("&nbsp;")
            r5.button("🗑 Remove", key=f"up_rm_{rid}", on_click=_up_remove_row, args=(rid,))
    st.button("➕ Add receipt line", key="up_add_line", on_click=_up_add_row)

    line_values = [{
        "date": st.session_state.get(f"up_date_{rid}", ""), "desc": st.session_state.get(f"up_desc_{rid}", ""),
        "amount": float(st.session_state.get(f"up_amt_{rid}", 0.0) or 0.0),
        "gst": float(st.session_state.get(f"up_gst_{rid}", 0.0) or 0.0),
    } for rid in rows]
    total = round(sum(r["amount"] for r in line_values), 2)
    gst_total = round(sum(r["gst"] for r in line_values), 2)
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Claim", f"${total:,.2f}")
    m2.metric("Total GST", f"${gst_total:,.2f}")
    m3.metric("Amount paid (after advance)", f"${total - advance:,.2f}")

    problems = []
    if not name.strip():
        problems.append("Full name is required.")
    if not event.strip():
        problems.append("Event description is required.")
    if total <= 0:
        problems.append("The total claim is $0.00. Enter the receipt amounts from the summary page.")
    if advance > total + 0.001:
        problems.append(f"The advance (${advance:.2f}) is more than the total claim (${total:.2f}).")
    for p in problems:
        st.error(p)

    st.markdown("---")
    st.markdown("#### 💳 Payment")
    paid_mode = st.radio("Was this claim paid?", ["Yes", "No"], horizontal=True, key="up_paid_mode",
                         format_func=lambda x: "Yes - already paid" if x == "Yes"
                         else "No - save it as a new claim for the Admin to process")
    paid = paid_mode == "Yes"
    paid_date = now_local().date()
    if paid:
        paid_date = st.date_input("Date paid", key="up_paid_date", format="DD/MM/YYYY")
        st.info("It will be saved straight into the Paid tab. The Nazim e Ala, Admin and President approval stages are "
                "bypassed and the claim is marked complete.")

    save_col, skip_col, _ = st.columns([2, 1.4, 5])
    if save_col.button("✅ Save as PAID claim" if paid else "💾 Save claim", type="primary", disabled=bool(problems)):
        form = {"name": name, "claimant_email": email, "event": event, "region": region, "majlis": majlis,
                "account": account, "bsb": bsb, "acc_num": acc_num, "advance": advance}
        try:
            with st.spinner("Saving to Google Drive..."):
                record = save_existing_claim(service, folder_id, auth["role"], auth["name"], form, line_values,
                                             pdf_bytes, claim_date, voucher_no, paid, paid_date, [])
        except Exception as e:
            st.error(f"Could not save the claim: {e}")
        else:
            st.session_state.pop("earlier_cache", None)
            _up_reset()
            st.session_state["upq_i"] = i + 1
            st.session_state["upl_notice"] = (f"Saved {record['file_name']} " +
                                              ("as a PAID claim." if paid else "in To Process."))
            st.rerun()
    if skip_col.button("Skip this file"):
        _up_reset()
        st.session_state["upq_i"] = i + 1
        st.rerun()


# -------------------------------------------------------------------------
# STAFF LOGIN
# -------------------------------------------------------------------------
def render_login():
    st.title("🔐 Staff Login")
    with st.form("staff_login"):
        role_label = st.radio("Sign in as", ["Admin", "Nazim e Ala", "President"], horizontal=True)
        username = st.text_input("Username (Nazim e Ala and President only — e.g. your region name)")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if submitted:
        role = {"Admin": ROLE_ADMIN, "Nazim e Ala": ROLE_NAZIM, "President": ROLE_PRESIDENT}[role_label]
        auth = authenticate(role, username, password)
        if auth:
            st.session_state.auth = auth
            st.session_state.admin_view = "dashboard"
            st.rerun()
        else:
            st.error("Incorrect username or password.")


# -------------------------------------------------------------------------
# ADMIN: ROLES PAGE
# -------------------------------------------------------------------------
def render_roles_page():
    st.markdown("### 👥 Roles — Secondary Approvals")
    if st.button("⬅ Back to Claims Dashboard"):
        st.session_state.admin_view = "dashboard"
        st.rerun()

    if st.session_state.pop("roles_saved", False):
        st.success("Roles saved.")

    st.caption(
        "Claims go to the Nazim e Ala of the claimant's region first, then to you (Admin), then to the President. "
        "Passwords are stored scrambled, so they cannot be read back later. Leave a password box empty to keep the current one."
    )

    roles = get_roles_config()
    regions = st.session_state.admin_lists.get("regions", [])
    form_v = st.session_state.get("roles_form_v", 0)   # changes after saving so password boxes clear

    st.markdown("#### 🏛️ President")
    pres = roles["president"]
    c1, c2, c3 = st.columns([2, 2, 2])
    p_user = c1.text_input("Username", value=pres.get("username") or "President", key=f"role_pres_user_{form_v}")
    p_pass = c2.text_input("New password", type="password", key=f"role_pres_pass_{form_v}")
    c3.markdown("&nbsp;")
    c3.caption("✅ Password is set" if pres.get("hash") else "⚠️ No password yet — the President cannot sign in")

    st.markdown("#### 🗺️ Nazim e Ala — one login per region")
    nazim_inputs = {}
    for i, region in enumerate(regions):
        info = roles["nazims"].get(region, {})
        c1, c2, c3 = st.columns([2, 2, 2])
        user = c1.text_input(f"Username for {region}", value=info.get("username") or region, key=f"role_nz_user_{i}_{form_v}")
        pwd = c2.text_input(f"New password for {region}", type="password", key=f"role_nz_pass_{i}_{form_v}")
        c3.markdown("&nbsp;")
        c3.caption("✅ Password is set" if info.get("hash") else "⚠️ No password yet — claims from this region skip the Nazim e Ala")
        nazim_inputs[region] = (user, pwd)

    if st.button("Save Roles", type="primary"):
        usernames = [p_user.strip().lower()] + [u.strip().lower() for u, _ in nazim_inputs.values()]
        if any(not u for u in usernames):
            st.error("Every username must be filled in.")
            return
        if len(set(usernames)) != len(usernames):
            st.error("Each username must be different.")
            return

        new_roles = {"president": dict(pres), "nazims": {}}
        new_roles["president"]["username"] = p_user.strip()
        if p_pass:
            new_roles["president"]["salt"], new_roles["president"]["hash"] = hash_password(p_pass)
        for region, (user, pwd) in nazim_inputs.items():
            entry = dict(roles["nazims"].get(region, {"salt": "", "hash": ""}))
            entry["username"] = user.strip()
            if pwd:
                entry["salt"], entry["hash"] = hash_password(pwd)
            new_roles["nazims"][region] = entry

        updated = dict(st.session_state.admin_lists)
        updated["roles"] = new_roles
        save_admin_settings(updated)
        st.session_state.admin_lists = updated
        st.session_state["roles_saved"] = True
        st.session_state["roles_form_v"] = form_v + 1
        st.rerun()

# -------------------------------------------------------------------------
# ADMIN: SETTINGS PAGE
# -------------------------------------------------------------------------
def render_settings_page():
    st.markdown("### ⚙️ Settings")
    if st.button("⬅ Back to Claims Dashboard"):
        st.session_state.admin_view = "dashboard"
        st.rerun()

    new_pwd = st.text_input("Modify System Admin Password Key", value=st.session_state.admin_lists.get("admin_password", "123"))

    st.markdown("---")
    st.markdown("#### ☁️ Google Drive Storage")
    auth_mode = get_drive_auth_mode()
    if auth_mode == "oauth":
        st.success("Drive is connected using your own Google account. Claims are saved as you, so the folder just needs to be in that account's Drive.")
    elif auth_mode == "service_account":
        sa_email = get_service_account_email()
        st.warning(
            "Using a service account, which Google often refuses for normal Drive folders (storage quota error). "
            f"If you use it, share the folder with `{sa_email}` as Editor. Switching to the google_oauth secret is recommended."
        )
    else:
        st.warning("No Google credentials were found in the app secrets, so claims cannot be saved to Drive yet.")

    drive_link_input = st.text_input(
        "Google Drive Folder Link (claim PDFs and the claims register are saved here — change it each year)",
        value=st.session_state.admin_lists.get("drive_folder_link", "")
    )
    typed_folder_id = extract_folder_id(drive_link_input)
    if drive_link_input.strip() and not typed_folder_id:
        st.error("Could not find a folder ID in that link. Open the folder in Drive and copy the address from the browser.")

    if st.button("Test Drive connection"):
        if not typed_folder_id:
            st.error("Enter a valid Drive folder link first.")
        else:
            try:
                service = get_drive_service()
                if service is None:
                    st.error("Google credentials are missing from the app secrets.")
                else:
                    meta = service.files().get(
                        fileId=typed_folder_id, fields="id, name, mimeType", supportsAllDrives=True
                    ).execute()
                    if meta.get("mimeType") != "application/vnd.google-apps.folder":
                        st.error("That link points to a file, not a folder.")
                    else:
                        st.success(f"Connected. Folder found: {meta.get('name')}")
            except Exception as e:
                st.error(f"Could not open that folder. Check the link, and that it belongs to (or is shared with) the connected Google account. Details: {e}")

    st.markdown("---")
    st.markdown("#### 👤 Members List (auto-fills the claim form)")
    st.caption("Upload a CSV or Excel file with the headings Name, Email, Region, Majlis (and optionally Account). "
               "When a claimant types their name, those details are filled in. Bank details are never stored in this list "
               "and are always typed in by the claimant. The list is saved in the Drive folder as members.csv. "
               "A name with no match here is looked up in that person's earlier claims.")
    members_file = st.file_uploader("Members list (.csv or .xlsx)", type=["csv", "xlsx"], key="members_upload")
    if st.button("Save members list to Drive", disabled=members_file is None):
        try:
            m_service = get_drive_service()
            m_folder = typed_folder_id or get_drive_folder_id()
            if m_service is None or not m_folder:
                raise RuntimeError("Google Drive is not set up yet.")
            csv_data = members_csv_from_upload(members_file.name, members_file.getvalue())
            save_members(m_service, m_folder, csv_data)
            st.session_state.pop("members_cache", None)
            count = len(list(csv.DictReader(io.StringIO(csv_data.decode("utf-8-sig", errors="replace")))))
            st.success(f"Members list saved ({count} people).")
        except Exception as e:
            st.error(f"Could not save the members list: {e}")

    st.markdown("---")
    st.markdown("### 👑 Central Executive Authority Configuration (Global Overrides)")
    q_mal = st.text_input("Approved by Qaid Mal - Target Field Assignment Name", value=st.session_state.admin_lists.get("qaid_mal_name", "Tahir Mahmood Rana"))
    s_maa = st.text_input("Approved by Sadr MAA - Target Field Assignment Name", value=st.session_state.admin_lists.get("sadr_maa_name", "Abdul Jamil Mubbashar"))

    st.markdown("---")
    regions_text = st.text_area("Regions Master List Configuration (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
    parsed_regions_list = [line.strip() for line in regions_text.split("\n") if line.strip()]

    st.markdown("---")
    st.markdown("#### 📑 Categorized Ledger Accounts Management Matrix")
    saved_accounts = st.session_state.admin_lists.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
    nat_acc_text = st.text_area("National-Only Accounts Configuration (One entry per line)", value="\n".join(saved_accounts.get("National", [])))
    reg_acc_text = st.text_area("General Regional Accounts Configuration (One entry per line)", value="\n".join(saved_accounts.get("General_Regional", [])))
    parsed_nat_accounts = [line.strip() for line in nat_acc_text.split("\n") if line.strip()]
    parsed_reg_accounts = [line.strip() for line in reg_acc_text.split("\n") if line.strip()]

    st.markdown("---")
    st.markdown("#### 🗺️ Regional Majlis & Nazim e Ala Configuration Matrix")
    selected_admin_region = st.selectbox("Select a region to configure details", options=parsed_regions_list, key="cfg_region_pick")

    with st.expander("👀 Majlis currently saved for every region"):
        for r_name in parsed_regions_list:
            saved_list = st.session_state.admin_lists.get("regional_majlis", {}).get(r_name, [])
            st.markdown(f"**{_md_escape(r_name)}**: " + (", ".join(_md_escape(x) for x in saved_list) if saved_list else "_none saved_"))

    current_map = st.session_state.admin_lists.get("regional_majlis", {})
    existing_majlis_for_region = current_map.get(selected_admin_region, [])
    majlis_text = st.text_area(f"Enter Majlis names for '{selected_admin_region}' (One entry per line)", value="\n".join(existing_majlis_for_region), height=100,
                             key=f"cfg_majlis_{selected_admin_region}")
    parsed_majlis_list = [line.strip() for line in majlis_text.split("\n") if line.strip()]

    current_nazims = st.session_state.admin_lists.get("regional_nazims", {})
    region_nazim_data = current_nazims.get(selected_admin_region, {"name": "", "email": ""})
    nazim_name_input = st.text_input(f"Nazim e Ala Name for '{selected_admin_region}'", value=region_nazim_data.get("name", ""), key=f"cfg_nz_name_{selected_admin_region}")
    nazim_email_input = st.text_input(f"Nazim e Ala Email Address for '{selected_admin_region}'", value=region_nazim_data.get("email", ""), key=f"cfg_nz_email_{selected_admin_region}")

    st.markdown("---")
    st.markdown("#### 🔐 Outbound live SMTP Server Credentials Gateway")
    smtp_srv = st.text_input("SMTP Server Relay Node Hostname", value=st.session_state.admin_lists.get("smtp_server", "smtp.gmail.com"))
    smtp_prt = st.number_input("SMTP Server TLS Port Number", value=int(st.session_state.admin_lists.get("smtp_port", 587)), step=1)
    smtp_usr = st.text_input("Sender User Authentication Email Address ID", value=st.session_state.admin_lists.get("smtp_user", "claimsubmissionmaaa@gmail.com"))
    smtp_pwd = st.text_input("Sender Account App Password Access Key", value=st.session_state.admin_lists.get("smtp_password", ""), type="password")

    if st.button("Save System Configuration Schema Matrix", type="primary"):
        updated_map = dict(current_map)
        updated_map[selected_admin_region] = parsed_majlis_list
        updated_map = {k: v for k, v in updated_map.items() if k in parsed_regions_list}
        for r in parsed_regions_list:
            if r not in updated_map:
                updated_map[r] = []

        updated_nazims = dict(current_nazims)
        updated_nazims[selected_admin_region] = {"name": nazim_name_input.strip(), "email": nazim_email_input.strip()}
        updated_nazims = {k: v for k, v in updated_nazims.items() if k in parsed_regions_list}

        updated_config = dict(st.session_state.admin_lists)   # keeps roles and any other saved keys
        updated_config.update({
            "regions": parsed_regions_list,
            "regional_majlis": updated_map,
            "regional_accounts": {
                "National": parsed_nat_accounts,
                "General_Regional": parsed_reg_accounts
            },
            "drive_folder_link": drive_link_input.strip(),
            "emails": [line.strip() for line in st.session_state.admin_lists.get("emails", [])],
            "admin_password": new_pwd.strip(),
            "smtp_server": smtp_srv.strip(),
            "smtp_port": int(smtp_prt),
            "smtp_user": smtp_usr.strip(),
            "smtp_password": smtp_pwd.strip(),
            "qaid_mal_name": q_mal.strip(),
            "sadr_maa_name": s_maa.strip(),
            "regional_nazims": updated_nazims
        })

        save_admin_settings(updated_config)
        st.session_state.admin_lists = updated_config
        st.success("Global application schema configuration matrix updated successfully!")
        st.rerun()

# -------------------------------------------------------------------------
# INTERACTIVE CLAIM FORM LAYOUT PANEL
# -------------------------------------------------------------------------
# Every receipt line has a stable id (r0, r1 ...). Widget keys look like  fm{version}_{field}_{id}.
# "Clear form" bumps the version, so every widget starts again from empty.
EARLIER_CACHE_SECONDS = 120
PAGE_MODES = ["Separate receipt", "Continues previous receipt (attach only)", "Skip this page (leave it out)"]


def _K(field, rid=""):
    return f"fm{st.session_state['form_v']}_{field}" + (f"_{rid}" if rid else "")


def _form_reset(dismiss_submission=False):
    v = st.session_state.get("form_v", 0)
    for key in [k for k in st.session_state.keys() if str(k).startswith(f"fm{v}_")]:
        del st.session_state[key]
    st.session_state["form_v"] = v + 1
    st.session_state.pop("autofill_msg", None)
    st.session_state["row_ctr"] = 1
    st.session_state["rows"] = ["r0"]
    st.session_state["row_meta"] = {"r0": {"kind": "upload"}}
    if dismiss_submission:
        st.session_state.pop("last_submission", None)


def _form_init():
    if "form_v" not in st.session_state:
        st.session_state["form_v"] = 0
        st.session_state["row_ctr"] = 1
        st.session_state["rows"] = ["r0"]
        st.session_state["row_meta"] = {"r0": {"kind": "upload"}}


def _new_rid():
    n = st.session_state["row_ctr"]
    st.session_state["row_ctr"] = n + 1
    return f"r{n}"


def _set_row_values(rid, item=None):
    item = item or {}
    st.session_state[_K("desc", rid)] = str(item.get("desc", ""))
    st.session_state[_K("date", rid)] = str(item.get("date", ""))
    st.session_state[_K("amt", rid)] = float(item.get("amount") or 0.0)
    st.session_state[_K("gst", rid)] = float(item.get("gst") or 0.0)
    st.session_state[_K("fp", rid)] = {k: item[k] for k in ("exact", "sig", "date_detected") if k in item}
    if item.get("text") is not None:
        st.session_state[_K("ocr", rid)] = item["text"]
    else:
        st.session_state.pop(_K("ocr", rid), None)


def _drop_row_keys(rid):
    v = st.session_state["form_v"]
    for key in [k for k in st.session_state.keys() if str(k).startswith(f"fm{v}_") and str(k).endswith(f"_{rid}")]:
        del st.session_state[key]


def _drop_children(rid):
    rows, meta = st.session_state["rows"], st.session_state["row_meta"]
    for child in [r for r in rows if meta.get(r, {}).get("parent") == rid]:
        rows.remove(child)
        meta.pop(child, None)
        _drop_row_keys(child)


def _clear_upload_row(rid):
    """The receipt was removed or replaced: forget everything that was read from it."""
    _drop_children(rid)
    _set_row_values(rid)
    for field in ("sig",):
        st.session_state.pop(_K(field, rid), None)
    st.session_state["row_meta"][rid] = {"kind": "upload"}


def _add_receipt_row():
    rid = _new_rid()
    st.session_state["rows"].append(rid)
    st.session_state["row_meta"][rid] = {"kind": "upload"}


def _remove_receipt_row(rid):
    _drop_children(rid)
    st.session_state["rows"].remove(rid)
    st.session_state["row_meta"].pop(rid, None)
    _drop_row_keys(rid)


def _read_upload_into_row(rid, uploaded, split):
    """Reads the file and fills the row (or, for a multi-page PDF, one row per page)."""
    bar = st.progress(0.0, text=f"Reading {uploaded.name} ...")

    def tick(done, total):
        bar.progress(min(1.0, done / max(1, total)), text=f"Reading page {done} of {total} ...")

    try:
        analysis = analyse_upload(uploaded.name, uploaded.getvalue(), split, progress=tick)
    except Exception as e:
        bar.empty()
        _clear_upload_row(rid)
        st.error(f"Could not read {uploaded.name}: {e}. You can type the details in by hand.")
        _set_row_values(rid)
        return
    bar.empty()
    st.session_state[_K("problems", rid)] = analysis.get("problems", [])

    _drop_children(rid)
    rows, meta = st.session_state["rows"], st.session_state["row_meta"]
    items = analysis["items"]
    if len(items) > 1:
        meta[rid] = {"kind": "upload", "split": True, "pages": analysis["pages"], "read": analysis["read_pages"]}
        _set_row_values(rid)
        pos = rows.index(rid) + 1
        for k, item in enumerate(items):
            child = _new_rid()
            rows.insert(pos + k, child)
            meta[child] = {"kind": "page", "parent": rid, "page": k + 1, "of": analysis["read_pages"], "fname": uploaded.name}
            _set_row_values(child, item)
    else:
        meta[rid] = {"kind": "upload", "pages": analysis["pages"], "read": analysis["read_pages"]}
        _set_row_values(rid, items[0])


def _earlier_claims():
    """All earlier claims (for duplicate checks), kept for a couple of minutes so typing stays fast."""
    cache = st.session_state.get("earlier_cache")
    if cache and time.time() - cache["at"] < EARLIER_CACHE_SECONDS:
        return cache["claims"]
    return _refresh_earlier_claims()


def _refresh_earlier_claims():
    claims = []
    try:
        service = get_drive_service()
        folder_id = get_drive_folder_id()
        if service is not None and folder_id:
            claims, _ = load_register(service, folder_id)
    except Exception:
        claims = []
    st.session_state["earlier_cache"] = {"at": time.time(), "claims": claims}
    return claims


def _members():
    """The members list from Drive, kept for a couple of minutes so typing stays fast."""
    cache = st.session_state.get("members_cache")
    if cache and time.time() - cache["at"] < EARLIER_CACHE_SECONDS:
        return cache["rows"]
    rows = []
    try:
        service = get_drive_service()
        folder_id = get_drive_folder_id()
        if service is not None and folder_id:
            rows = load_members(service, folder_id)
    except Exception:
        rows = []
    st.session_state["members_cache"] = {"at": time.time(), "rows": rows}
    return rows


def _autofill_from_name():
    """Runs when the name is changed: fills email, region, majlis and account (never the bank details)."""
    name = st.session_state.get(_K("name"), "")
    info = lookup_person(name, _members(), _earlier_claims())
    if not info:
        st.session_state.pop("autofill_msg", None)
        return
    cfg = st.session_state.admin_lists
    regions = cfg.get("regions", [])
    if info.get("email"):
        st.session_state[_K("email")] = info["email"]
    region = info.get("region") if info.get("region") in regions else st.session_state.get(_K("region")) or (regions[0] if regions else "")
    if info.get("region") in regions:
        st.session_state[_K("region")] = info["region"]
    if info.get("majlis") in cfg.get("regional_majlis", {}).get(region, []):
        st.session_state[_K(f"majlis_{region}")] = info["majlis"]
    accounts = cfg.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
    choices = accounts.get("National", []) if region == "National" else accounts.get("General_Regional", [])
    if info.get("account") in choices:
        st.session_state[_K(f"account_{region == 'National'}")] = info["account"]
    st.session_state["autofill_msg"] = "✅ Your details were filled in from our records. Please check them and enter your bank details."


def _value_inputs(rid, label):
    c1, c2, c3, c4 = st.columns([3, 2, 1.5, 1.5])
    desc = c1.text_input(f"Item Description {label}", key=_K("desc", rid))
    date = c2.text_input(f"Receipt Date (DD/MM/YYYY) {label}", key=_K("date", rid))
    amount = c3.number_input(f"Amount ($) {label}", min_value=0.0, format="%.2f", key=_K("amt", rid))
    gst = c4.number_input(f"GST ($) {label}", min_value=0.0, format="%.2f", key=_K("gst", rid))
    fp = st.session_state.get(_K("fp", rid)) or {}
    # a date the person typed over is no longer "detected" from the receipt
    return {"desc": desc, "date": date, "amount": amount, "gst": gst, **fp}


def render_claim_form():
    _form_init()
    st.title("📋 Online Expense Claim Submission System")

    if st.session_state.pop("show_balloons", False):
        st.balloons()

    last_submission = st.session_state.get("last_submission")
    if last_submission:
        if last_submission["saved"]:
            st.success("🎉 Your claim has been submitted and saved. Download a copy below for your records. "
                       "The form below has been cleared, ready for a new claim.")
        else:
            st.error("Your claim PDF was created, but it could NOT be saved for the administrator. "
                     "Please download it below and send it to them directly. "
                     f"Details: {last_submission['error']}")
        d1, d2, _ = st.columns([2.2, 1.6, 4])
        d1.download_button("📥 Download Official Summary Voucher PDF File", data=last_submission["pdf_bytes"],
                           file_name=last_submission["filename"], mime="application/pdf")
        d2.button("📝 Start a new claim", on_click=_form_reset, kwargs={"dismiss_submission": True})
        st.markdown("---")

    top_a, _ = st.columns([1.3, 7])
    top_a.button("🧹 Clear form", key="clear_top", on_click=_form_reset, help="Empties every field and removes every receipt")

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Name (Payee Name)", key=_K("name"), on_change=_autofill_from_name,
                             help="Type your full name and press Enter: your email, region, majlis and account code are filled in if we know you.")
        if st.session_state.get("autofill_msg"):
            st.caption(st.session_state["autofill_msg"])
        claimant_email = st.text_input("Email Address", key=_K("email"))
        expense_event_desc = st.text_input("Description of Event (Expense Type)", key=_K("event"))

        configured_regions = st.session_state.admin_lists["regions"]
        region = st.selectbox("Select Region / National", options=configured_regions, key=_K("region"))

        majlis_lookup_map = st.session_state.admin_lists["regional_majlis"]
        available_majlis_options = majlis_lookup_map.get(region, [])
        if not available_majlis_options:
            available_majlis_options = ["No entries declared under this region boundary"]
        majlis = st.selectbox("Select your Majlis / Office", options=available_majlis_options, key=_K(f"majlis_{region}"))

        saved_accounts = st.session_state.admin_lists.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
        active_account_choices = saved_accounts.get("National", []) if region == "National" else saved_accounts.get("General_Regional", [])
        if not active_account_choices:
            active_account_choices = ["No configured account lines found for this category"]
        account = st.selectbox("Select Tracking Account Code", options=active_account_choices,
                               key=_K(f"account_{region == 'National'}"))

    with col2:
        declared_total_input = st.number_input("Total Declared Claim Amount ($)", min_value=0.0, format="%.2f", key=_K("declared"))

        st.markdown("**🏦 Direct Reimbursement Bank Segment**")
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7, key=_K("bsb"))
        acc_num = st.text_input("Account Number", key=_K("accnum"))
        st.markdown("**💵 Advance Settlement**")
        settling_advance = st.radio("Are you settling an advance in this claim?", ["No", "Yes"], horizontal=True, key=_K("adv_mode"))
        advance_amount = 0.0
        if settling_advance == "Yes":
            advance_amount = st.number_input("Advance Amount Being Settled ($)", min_value=0.0, format="%.2f", key=_K("advance"))

    st.markdown("---")
    st.subheader("📎 Receipts")
    st.caption("Upload each receipt (photo or PDF). The date, amount, GST and details are read automatically and "
               "you can correct them. A PDF containing many receipts is read page by page and each page becomes its own line. "
               "Choosing a different file replaces that receipt's values straight away.")

    rows = st.session_state["rows"]
    meta = st.session_state["row_meta"]
    receipt_records = []        # one per receipt line, in order
    upload_files = []           # (uploaded file or None, first line number it covers)
    parent_positions = {}       # row id of an uploaded file -> its position in upload_files
    skip_pages = {}             # file position -> set of page numbers (0-based) left out of the PDF
    missing_files = 0
    line_no = 0
    i = 0
    while i < len(rows):
        rid = rows[i]
        m = meta[rid]
        i += 1

        if m["kind"] == "upload":
            first_line = line_no + 1
            head_l, head_r = st.columns([6, 1.2])
            head_l.markdown(f"#### 📄 Receipt file {sum(1 for r in rows[:i] if meta[r]['kind'] == 'upload')}")
            uploaded = st.file_uploader("Upload receipt (Image or PDF)", type=["png", "jpg", "jpeg", "heic", "heif", "pdf"],
                                        key=_K("file", rid), label_visibility="collapsed")
            if len([r for r in rows if meta[r]["kind"] == "upload"]) > 1:
                head_r.button("🗑 Remove", key=_K("rm", rid), on_click=_remove_receipt_row, args=(rid,))

            split_key = _K("split", rid)
            if split_key not in st.session_state:
                st.session_state[split_key] = True
            split_now = bool(st.session_state[split_key])
            stored_sig = st.session_state.get(_K("sig", rid))
            if uploaded is None:
                if stored_sig is not None:
                    _clear_upload_row(rid)
                    m = meta[rid]
            else:
                sig = f"{uploaded.name}_{uploaded.size}_{int(split_now)}"
                if sig != stored_sig:
                    _read_upload_into_row(rid, uploaded, split_now)
                    st.session_state[_K("sig", rid)] = sig
                    m = meta[rid]

            upload_files.append((uploaded, first_line))
            parent_positions[rid] = len(upload_files) - 1
            for problem in st.session_state.get(_K("problems", rid), []):
                st.warning(f"⚠️ {problem}. Please type that receipt's details in by hand.")
            if uploaded is None:
                missing_files += 1

            if m.get("split"):
                st.info(f"📑 {uploaded.name} has {m['pages']} pages. Choose what each page is below: a separate receipt (default), a continuation of the receipt before it, or a page to skip.")
                st.checkbox(f"Treat each of the {m['pages']} pages as a separate receipt", key=split_key)
                if m["pages"] > m["read"]:
                    st.warning(f"Only the first {m['read']} pages were read. Please split the file or add the rest as another file.")
            else:
                if m.get("pages", 1) > 1:
                    st.checkbox(f"Treat each of the {m['pages']} pages as a separate receipt", key=split_key)
                line_no += 1
                receipt_records.append(_value_inputs(rid, f"#{line_no}"))
                if _K("ocr", rid) in st.session_state:
                    with st.expander(f"Show the text read from receipt #{line_no}"):
                        st.text(st.session_state[_K("ocr", rid)])
        else:
            parent_pos = parent_positions.get(m["parent"], len(upload_files) - 1)
            mode = st.radio(
                f"Page {m['page']} of {m['of']} ({m['fname']})", PAGE_MODES, horizontal=True, key=_K("pgmode", rid),
                help="Choose 'Continues previous receipt' when a bill runs over several pages, or 'Skip' to leave the page out of the PDF.")
            if mode == PAGE_MODES[2]:
                skip_pages.setdefault(parent_pos, set()).add(m["page"] - 1)
            elif mode == PAGE_MODES[0]:
                line_no += 1
                receipt_records.append(_value_inputs(rid, f"#{line_no}"))
                if _K("ocr", rid) in st.session_state:
                    with st.expander(f"Show the text read from receipt #{line_no} (page {m['page']})"):
                        st.text(st.session_state[_K("ocr", rid)])
            else:
                st.caption("This page is attached to the claim PDF after the receipt above it, with no separate line.")

    btn_a, btn_b, _ = st.columns([1.6, 1.3, 6])
    btn_a.button("➕ Add receipt", key="add_receipt", on_click=_add_receipt_row)
    btn_b.button("🧹 Clear form", key="clear_bottom", on_click=_form_reset)

    calculated_total_claim = round(sum(r["amount"] for r in receipt_records), 2)
    calculated_total_gst = round(sum(r["gst"] for r in receipt_records), 2)
    is_amount_matching = abs(calculated_total_claim - declared_total_input) < 0.01

    st.markdown("---")
    st.markdown("### 📊 Dynamic Live Aggregated Verification Totals")
    sum_col1, sum_col2, sum_col3 = st.columns(3)
    sum_col1.metric("Receipts", f"{len(receipt_records)}")
    sum_col2.metric("Total Aggregated Claim Amount (Calculated)", f"${calculated_total_claim:,.2f}")
    sum_col3.metric("Total Aggregated GST Amount (Calculated)", f"${calculated_total_gst:,.2f}")

    # ---- duplicate receipts ----
    flags = []
    if any(f for f, _ in upload_files):
        flags = find_duplicate_receipts(receipt_records, _earlier_claims())
    dup_confirmed = True
    if flags:
        st.markdown("### ⚠️ Possible duplicate receipts")
        for fl in flags:
            st.warning(describe_flag(fl, ROLE_NAZIM))
        dup_confirmed = st.checkbox("I have checked, and these are genuine separate expenses (not the same receipt claimed twice)",
                                    key=_K("dupok"))

    st.markdown("---")
    st.markdown("### 🛡️ System Submission Safety Validation Checks")
    block_submission = False

    if not receipt_records:
        st.error("❌ Please upload at least one receipt.")
        block_submission = True
    if not is_amount_matching:
        st.error(f"❌ Total Doesn't Match: The sum of your individual row entries (${calculated_total_claim:.2f}) does not match your typed Total Declared Claim Amount (${declared_total_input:.2f}). Please review row values.")
        block_submission = True
    else:
        st.success("✅ Financial balance validation passed: Claim amounts are matching.")

    if missing_files > 0:
        st.warning(f"⚠️ {missing_files} receipt file(s) still to upload. Upload a file in each empty receipt box, or remove the box.")
        block_submission = True
    else:
        st.success("✅ Attachment verification passed: every receipt has its file.")

    if advance_amount > calculated_total_claim:
        st.error(f"❌ Advance Too High: The advance being settled (${advance_amount:.2f}) is more than the total claim (${calculated_total_claim:.2f}).")
        block_submission = True
    if flags and not dup_confirmed:
        st.error("❌ Please tick the box in the duplicate warning above to confirm these are separate expenses.")
        block_submission = True

    st.markdown("---")

    if block_submission:
        st.button("Submit", type="primary", disabled=True, help="Please resolve all structural error/warning notifications above to unlock the submit pipeline.")
        return
    if not st.button("Finalize and Submit Expense Claims Bundle", type="primary"):
        return

    if not name or not bsb or not acc_num or not claimant_email or not expense_event_desc:
        st.error("Submission Denied: Profile details, Event Description, Claimant Email, BSB, and Account Numbers must be filled out completely.")
        return

    # Fresh duplicate check against the latest register, so a claim submitted a minute ago is not missed
    fresh_flags = find_duplicate_receipts(receipt_records, _refresh_earlier_claims())
    if fresh_flags and not st.session_state.get(_K("dupok")):
        st.warning("Possible duplicate receipts were found. Please review the warning and tick the confirmation box, then submit again.")
        st.rerun()
    flags = fresh_flags

    form = {"name": name, "event": expense_event_desc, "region": region, "majlis": majlis, "account": account,
            "bsb": bsb, "acc_num": acc_num, "advance": advance_amount}
    payload = build_voucher_payload(form, receipt_records)

    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "", name.replace(" ", "_")) or "Claimant"
    output_filename = f"Expense_Voucher_{safe_name}_{stamp}.pdf"
    generate_official_voucher_pdf(output_filename, payload, receipt_records)
    voucher_pages = _voucher_page_count(PdfReader(output_filename))

    files_in_order = [f for f, _ in upload_files]
    labels = [f"Receipt #{first}" for _, first in upload_files]
    with st.spinner("Stitching receipts into the claim PDF..."):
        stitched_ok = stitch_files_to_pdf(output_filename, files_in_order, labels, skip_pages)
    if not stitched_ok:
        st.stop()

    with open(output_filename, "rb") as f:
        pdf_bytes = f.read()
    os.remove(output_filename)

    saved_to_drive = False
    drive_error = ""
    with st.spinner("Saving your claim..."):
        try:
            service = get_drive_service()
            folder_id = get_drive_folder_id()
            if service is None or not folder_id:
                raise RuntimeError("Google Drive storage has not been set up by the administrator yet.")

            drive_name = build_drive_filename("", name, expense_event_desc, now_local().strftime("%d/%m/%Y"))
            file_id, file_link = upload_pdf_to_drive(service, folder_id, drive_name, pdf_bytes)
            has_nazim = region_has_nazim_login(region)
            now = now_local()
            record = {
                "id": uuid.uuid4().hex[:10],
                "submitted_on": now.strftime('%d/%m/%Y'),
                "submitted_ts": now.isoformat(),
                "name": name,
                "claimant_email": claimant_email,
                "event": expense_event_desc,
                "region": region,
                "majlis": majlis,
                "account": account,
                "bsb": bsb,
                "acc_num": acc_num,
                "total_claim": calculated_total_claim,
                "gst": calculated_total_gst,
                "advance": round(advance_amount, 2),
                "amount_payable": round(calculated_total_claim - advance_amount, 2),
                "file_name": drive_name,
                "file_id": file_id,
                "file_link": file_link,
                "voucher_no": "",
                "voucher_pages": voucher_pages,
                "receipts": [{k: v for k, v in r.items() if k != "text"} for r in receipt_records],
                "flags": flags,
                "comments": [],
                "paid": False,
                # Approval workflow: Nazim e Ala -> Admin -> President.
                # Regions with no Nazim e Ala login go straight to the Admin.
                "nazim_status": STATUS_PENDING if has_nazim else STATUS_BYPASSED,
                "nazim_by": "" if has_nazim else "Auto",
                "admin_status": STATUS_PENDING,
                "president_status": STATUS_PENDING,
                "history": [],
            }
            _log(record, name, f"Claim submitted ({len(receipt_records)} receipt{'s' if len(receipt_records) != 1 else ''}, "
                               f"${calculated_total_claim:,.2f})" + ("; sent straight to Admin (no Nazim e Ala login for this region)" if not has_nazim else ""))
            if flags:
                _log(record, "System", f"{len(flags)} possible duplicate receipt(s) flagged")
            add_claim_to_register(service, folder_id, record)
            saved_to_drive = True
        except Exception as e:
            drive_error = str(e)
            if "storage quota" in drive_error.lower() or "storagequota" in drive_error.lower():
                drive_error += " (Google does not allow a service account to save files here. The administrator needs to connect Drive with their own Google account.)"

    st.session_state["last_submission"] = {
        "saved": saved_to_drive, "error": drive_error, "filename": output_filename, "pdf_bytes": pdf_bytes,
    }
    if saved_to_drive:
        st.session_state["show_balloons"] = True
        st.session_state.pop("earlier_cache", None)
        _form_reset()
        st.rerun()
    else:
        st.error(f"Your claim PDF was created, but it could NOT be saved for the administrator. Details: {drive_error}")
        st.download_button("📥 Download Official Summary Voucher PDF File", data=pdf_bytes,
                           file_name=output_filename, mime="application/pdf", key="dl_failed")


# -------------------------------------------------------------------------
# CORE FRONT-END SELECTION ROUTER
# -------------------------------------------------------------------------
st.set_page_config(page_title="Expense Portal (Official MAA Clone)", layout="wide")
app_mode = st.sidebar.selectbox("Navigate Mode", ["User Claim Portal", "Admin Panel Configuration"])

if app_mode == "Admin Panel Configuration":
    auth = st.session_state.get("auth")

    if not auth:
        render_login()
    else:
        st.sidebar.markdown(f"Signed in as **{auth['name']}**")
        if st.sidebar.button("Sign out"):
            st.session_state.auth = None
            st.session_state.admin_view = "dashboard"
            st.rerun()

        if auth["role"] == ROLE_ADMIN:
            st.title("⚙️ Admin Control Panel")
            view = st.session_state.get("admin_view", "dashboard")
            if view == "settings":
                render_settings_page()
            elif view == "roles":
                render_roles_page()
            elif view == "amend":
                render_amend_page(auth)
            elif view == "merge":
                render_merge_page(auth)
            elif view == "upload":
                render_upload_claim_page(auth)
            elif view == "summary":
                render_summary_page(auth)
            else:
                render_claims_dashboard(auth)
        elif auth["role"] == ROLE_NAZIM:
            st.title("📋 Nazim e Ala Approvals")
            render_nazim_dashboard(auth)
        else:
            st.title("📋 President Approvals")
            render_president_dashboard(auth)

else:
    render_claim_form()
