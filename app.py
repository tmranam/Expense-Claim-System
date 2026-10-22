import streamlit as st
import re
import json
import os
import io
import uuid
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps
import pytesseract
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
import pypdf
from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

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
                        data[key] = DEFAULT_CONFIG[key]
                    elif not data[key] and key != "drive_folder_link":
                        data[key] = DEFAULT_CONFIG[key]
                return data
        except Exception:
            return dict(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


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
    return json.loads(raw.decode("utf-8")), fid


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
    cell_left = label_x - 5 + 100          # label cell is 100pt wide, 5pt padding
    page_w = float(first_page.mediabox.width)
    page_h = float(first_page.mediabox.height)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.white)
    c.rect(cell_left + 2.5, base_y - 6.5, 170 - 5, 18, stroke=0, fill=1)
    if voucher_no:
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(cell_left + 5, base_y - 1.5, voucher_no)
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


def update_pdf_in_drive(service, file_id, pdf_bytes):
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()


def set_claim_status(service, folder_id, claim_id, new_status=None, voucher_no=None):
    """Changes a claim's status and/or voucher number.
    The voucher number is also stamped onto the PDF in Drive. Returns a warning message or ''."""
    claims, fid = load_register(service, folder_id)
    warning = ""
    for c in claims:
        if c["id"] != claim_id:
            continue

        if voucher_no is not None:
            new_no = voucher_no.strip()[:30]
            if new_no != c.get("voucher_no", ""):
                try:
                    pdf_bytes = service.files().get_media(fileId=c["file_id"], supportsAllDrives=True).execute()
                    update_pdf_in_drive(service, c["file_id"], stamp_voucher_number(pdf_bytes, new_no))
                    c["voucher_no"] = new_no
                except Exception as e:
                    warning = f"The voucher number could not be written onto the PDF in Drive ({e})."

        if new_status:
            c["status"] = new_status
            c["decided_on"] = now_local().strftime('%d/%m/%Y') if new_status in (STATUS_APPROVED, STATUS_REJECTED) else ""
        break
    save_register(service, folder_id, claims, fid)
    return warning

# -------------------------------------------------------------------------
# OCR ENGINE FOR IMAGE & PDF RECEIPTS
# -------------------------------------------------------------------------
def parse_receipt_image_or_pdf(uploaded_file):
    """Processes images or PDFs, returning (description, total, gst, receipt_date, raw_text)."""
    try:
        uploaded_file.seek(0)
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()
        raw_text = ""

        if file_ext == ".pdf":
            temp_pdf_path = f"temp_ocr_{uuid.uuid4().hex}.pdf"
            with open(temp_pdf_path, "wb") as temp_f:
                temp_f.write(uploaded_file.read())

            try:
                reader = pypdf.PdfReader(temp_pdf_path)
                for page in reader.pages:
                    text_content = page.extract_text()
                    if text_content:
                        raw_text += text_content + "\n"

                if not raw_text.strip():
                    converted_images = convert_from_path(temp_pdf_path)
                    for pil_img in converted_images:
                        gray = ImageOps.autocontrast(pil_img.convert("L"))
                        raw_text += pytesseract.image_to_string(gray, config="--psm 6") + "\n"
            finally:
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)
        else:
            image = Image.open(uploaded_file)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2000, 2000))
            gray = ImageOps.autocontrast(image.convert("L"))
            raw_text = pytesseract.image_to_string(gray, config="--psm 6")

        raw_text_lower = raw_text.lower()
        cleaned_text = re.sub(r'(?<=\d)[ \t]*([.,])[ \t]*(?=\d{2}\b)', r'\1', raw_text_lower)

        amounts = []
        for m in re.findall(r'\b\d+[.,]\d{2}\b', cleaned_text):
            try:
                amounts.append(float(m.replace(',', '.')))
            except ValueError:
                pass
        total_amount = max(amounts) if amounts else 0.0

        gst_amount = 0.0
        if "gst" in cleaned_text or "tax" in cleaned_text:
            gst_numbers = re.findall(r'(?:gst|tax)[^\d\n]*(\d+[.,]\d{2})', cleaned_text)
            if gst_numbers:
                gst_amount = float(gst_numbers[0].replace(',', '.'))
            else:
                gst_amount = round(total_amount / 11, 2)

        detected_date = now_local().strftime('%d/%m/%Y')
        date_patterns = re.findall(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', raw_text)
        if date_patterns:
            detected_date = date_patterns[0].replace('-', '/')

        possible_items = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking',
                          'fuel', 'stationery', 'food', 'meals', 'woolworths', 'coles']
        keywords = [i.title() for i in possible_items if i in cleaned_text]
        description = ", ".join(keywords) if keywords else "General Expense Purchases"

        return description, total_amount, gst_amount, detected_date, raw_text
    except Exception as e:
        return "Scan failed", 0.0, 0.0, now_local().strftime('%d/%m/%Y'), f"ERROR: {e}"

# -------------------------------------------------------------------------
# VOUCHER PDF BUILDER
# -------------------------------------------------------------------------
def generate_official_voucher_pdf(filename, data, receipts):
    """Compiles the Majlis Ansarullah Australia expense voucher."""
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
        [Paragraph("<b>Payee Name:</b>", cell_style), Paragraph(data['name'], cell_style), Paragraph("<b>Voucher No:</b>", cell_style), Paragraph("", cell_style)],
        [Paragraph("<b>Expense Type:</b>", cell_style), Paragraph(data['expense_type'], cell_style), Paragraph("<b>BSB:</b>", cell_style), Paragraph(data['bsb'], cell_style)],
        [Paragraph("<b>Account:</b>", cell_style), Paragraph(data['account'], cell_style), Paragraph("<b>Account No:</b>", cell_style), Paragraph(data['acc_num'], cell_style)],
        [Paragraph("<b>Region:</b>", cell_style), Paragraph(data['region'], cell_style), Paragraph("<b>Total Claim:</b>", cell_bold_style), Paragraph(f"<b>$ {data['total_amount']:.2f}</b>", cell_bold_style)],
        [Paragraph("<b>Majlis:</b>", cell_style), Paragraph(data['majlis'], cell_style), Paragraph("<b>Advance Issued:</b>", cell_style), Paragraph(f"$ {data['advance']:.2f}" if data['advance'] > 0 else "", cell_red_bold_style)]
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
                Paragraph(r['date'], cell_style),
                Paragraph(r['desc'], cell_style),
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
        [Paragraph(f"Name: {data['name']}", cell_style), Paragraph(f"Name: {data['nazim_name']}", cell_style)],
        [Paragraph("Signature:", cell_style), Paragraph("Signature:", cell_style)],
        [Paragraph(f"Date: {now_local().strftime('%d/%m/%Y')}", cell_style), Paragraph("Date:", cell_style)],
        [Paragraph("<b>Approved by Qaid Mal:</b>", cell_bold_style), Paragraph("<b>Approved by Sadr MAA:</b>", cell_bold_style)],
        [Paragraph(f"Name: {data['qaid_mal']}", cell_style), Paragraph(f"Name: {data['sadr_maa']}", cell_style)],
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


def stitch_files_to_pdf(voucher_pdf_path, uploaded_files_list):
    """Summary page first, then every uploaded receipt, as one PDF."""
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
                reader = PdfReader(_image_to_pdf_buffer(u_file, f"Receipt #{idx + 1}"))

            for page in reader.pages:
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
# ADMIN: CLAIMS DASHBOARD
# -------------------------------------------------------------------------
DASH_HEADERS = ["Claimant", "Date", "Event", "Total Claim", "GST", "Advance Adj.", "Amount To Pay", "Voucher No.", "File"]
DASH_WIDTHS = [1.5, 1.0, 1.7, 1.0, 0.8, 1.0, 1.1, 1.2, 0.9]


def _cell(col, text):
    col.markdown(str(text).replace("$", "\\$"))


def render_claim_rows(service, folder_id, claims_subset, actions):
    if not claims_subset:
        st.info("No claims in this tab.")
        return

    header_cols = st.columns(DASH_WIDTHS)
    for col, label in zip(header_cols, DASH_HEADERS):
        col.markdown(f"**{label}**")
    st.divider()

    for c in sorted(claims_subset, key=lambda x: x.get("submitted_ts", ""), reverse=True):
        cols = st.columns(DASH_WIDTHS)
        _cell(cols[0], c.get("name", ""))
        _cell(cols[1], c.get("submitted_on", ""))
        _cell(cols[2], c.get("event", ""))
        _cell(cols[3], f"${c.get('total_claim', 0):,.2f}")
        _cell(cols[4], f"${c.get('gst', 0):,.2f}")
        advance = c.get("advance", 0)
        _cell(cols[5], f"-${advance:,.2f}" if advance > 0 else "-")
        _cell(cols[6], f"**${c.get('amount_payable', 0):,.2f}**")
        _cell(cols[7], f"**{c['voucher_no']}**" if c.get("voucher_no") else "-")
        cols[8].markdown(f"[Open PDF]({c.get('file_link', '')})")

        # Second line: voucher number box + action buttons
        key_base = f"{c['status']}_{c['id']}"
        row_cols = st.columns([2.2, 1.2] + [1.2] * len(actions) + [4.0 - 1.2 * len(actions)])
        voucher_input = row_cols[0].text_input(
            "Voucher No.", value=c.get("voucher_no", ""), max_chars=30,
            key=f"vno_{key_base}", placeholder="Enter Voucher No.", label_visibility="collapsed"
        )

        def _apply(new_status=None):
            try:
                warning = set_claim_status(service, folder_id, c["id"], new_status, voucher_no=voucher_input)
            except Exception as e:
                st.error(f"Could not update claim: {e}")
                return
            if warning:
                st.session_state["dash_warning"] = warning
            st.rerun()

        if row_cols[1].button("Save No.", key=f"saveno_{key_base}", use_container_width=True):
            _apply(None)
        for b_col, (label, new_status) in zip(row_cols[2:2 + len(actions)], actions):
            if b_col.button(label, key=f"{key_base}_{new_status}", use_container_width=True):
                _apply(new_status)
        st.divider()


def render_claims_dashboard():
    st.markdown("### 📊 Claims Dashboard")
    pending_warning = st.session_state.pop("dash_warning", None)
    if pending_warning:
        st.warning(f"Saved, but: {pending_warning} Use the Open PDF link to check the file in Drive.")
    top_a, top_b, _ = st.columns([1, 1, 6])
    if top_a.button("⚙️ Settings", use_container_width=True):
        st.session_state.admin_view = "settings"
        st.rerun()
    if top_b.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    folder_id = get_drive_folder_id()
    try:
        service = get_drive_service()
    except Exception as e:
        st.error(f"Google Drive credentials problem: {e}")
        return

    if service is None:
        st.warning("Google Drive credentials are not set up yet. Add them to the app secrets (see Settings for details).")
        return
    if not folder_id:
        st.warning("No Google Drive folder is configured. Open Settings and paste your Drive folder link.")
        return

    try:
        claims, _ = load_register(service, folder_id)
    except Exception as e:
        st.error(f"Could not read the claims register from Google Drive: {e}")
        return

    by_status = {
        STATUS_PENDING: [c for c in claims if c.get("status") == STATUS_PENDING],
        STATUS_HOLD: [c for c in claims if c.get("status") == STATUS_HOLD],
        STATUS_APPROVED: [c for c in claims if c.get("status") == STATUS_APPROVED],
        STATUS_REJECTED: [c for c in claims if c.get("status") == STATUS_REJECTED],
    }

    tab_pending, tab_hold, tab_approved, tab_rejected = st.tabs([
        f"Pending ({len(by_status[STATUS_PENDING])})",
        f"On Hold ({len(by_status[STATUS_HOLD])})",
        f"Approved ({len(by_status[STATUS_APPROVED])})",
        f"Rejected ({len(by_status[STATUS_REJECTED])})",
    ])

    with tab_pending:
        render_claim_rows(service, folder_id, by_status[STATUS_PENDING],
                          [("Approve", STATUS_APPROVED), ("Reject", STATUS_REJECTED), ("Hold", STATUS_HOLD)])
    with tab_hold:
        render_claim_rows(service, folder_id, by_status[STATUS_HOLD],
                          [("Approve", STATUS_APPROVED), ("Reject", STATUS_REJECTED)])
    with tab_approved:
        render_claim_rows(service, folder_id, by_status[STATUS_APPROVED],
                          [("Reopen", STATUS_PENDING)])
    with tab_rejected:
        render_claim_rows(service, folder_id, by_status[STATUS_REJECTED],
                          [("Reopen", STATUS_PENDING)])

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
    selected_admin_region = st.selectbox("Select a region to configure details", options=parsed_regions_list)

    current_map = st.session_state.admin_lists.get("regional_majlis", {})
    existing_majlis_for_region = current_map.get(selected_admin_region, [])
    majlis_text = st.text_area(f"Enter Majlis names for '{selected_admin_region}' (One entry per line)", value="\n".join(existing_majlis_for_region), height=100)
    parsed_majlis_list = [line.strip() for line in majlis_text.split("\n") if line.strip()]

    current_nazims = st.session_state.admin_lists.get("regional_nazims", {})
    region_nazim_data = current_nazims.get(selected_admin_region, {"name": "", "email": ""})
    nazim_name_input = st.text_input(f"Nazim e Ala Name for '{selected_admin_region}'", value=region_nazim_data.get("name", ""))
    nazim_email_input = st.text_input(f"Nazim e Ala Email Address for '{selected_admin_region}'", value=region_nazim_data.get("email", ""))

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

        updated_config = {
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
        }

        save_admin_settings(updated_config)
        st.session_state.admin_lists = updated_config
        st.success("Global application schema configuration matrix updated successfully!")
        st.rerun()

# -------------------------------------------------------------------------
# CORE FRONT-END SELECTION ROUTER
# -------------------------------------------------------------------------
st.set_page_config(page_title="Expense Portal (Official MAA Clone)", layout="wide")
app_mode = st.sidebar.selectbox("Navigate Mode", ["User Claim Portal", "Admin Panel Configuration"])

if app_mode == "Admin Panel Configuration":
    st.title("⚙️ Admin Control Panel")
    pwd_input = st.text_input("Enter Master Admin Access Key Password", type="password")

    if pwd_input == st.session_state.admin_lists.get("admin_password", "123"):
        if st.session_state.get("admin_view", "dashboard") == "settings":
            render_settings_page()
        else:
            render_claims_dashboard()
    elif pwd_input:
        st.error("Incorrect password.")

# -------------------------------------------------------------------------
# INTERACTIVE CLAIM FORM LAYOUT PANEL
# -------------------------------------------------------------------------
else:
    st.title("📋 Online Expense Claim Submission System")

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Name (Payee Name)")
        claimant_email = st.text_input("Email Address")
        expense_event_desc = st.text_input("Description of Event (Expense Type)")

        configured_regions = st.session_state.admin_lists["regions"]
        region = st.selectbox("Select Region / National", options=configured_regions)

        majlis_lookup_map = st.session_state.admin_lists["regional_majlis"]
        available_majlis_options = majlis_lookup_map.get(region, [])
        if not available_majlis_options:
            available_majlis_options = ["No entries declared under this region boundary"]

        majlis = st.selectbox("Select your Majlis / Office", options=available_majlis_options)

        saved_accounts = st.session_state.admin_lists.get("regional_accounts", DEFAULT_CONFIG["regional_accounts"])
        if region == "National":
            active_account_choices = saved_accounts.get("National", [])
        else:
            active_account_choices = saved_accounts.get("General_Regional", [])

        if not active_account_choices:
            active_account_choices = ["No configured account lines found for this category"]

        account = st.selectbox("Select Tracking Account Code", options=active_account_choices)

    with col2:
        total_receipts_input = st.number_input("Total Number of Receipts To Claim", min_value=1, max_value=50, value=1, step=1)
        declared_total_input = st.number_input("Total Declared Claim Amount ($)", min_value=0.0, format="%.2f")

        st.markdown("**🏦 Direct Reimbursement Bank Segment**")
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7)
        acc_num = st.text_input("Account Number")
        st.markdown("**💵 Advance Settlement**")
        settling_advance = st.radio("Are you settling an advance in this claim?", ["No", "Yes"], horizontal=True)
        advance_amount = 0.0
        if settling_advance == "Yes":
            advance_amount = st.number_input("Advance Amount Being Settled ($)", min_value=0.0, format="%.2f")

    st.markdown("---")
    st.subheader("📎 Individual Itemized Receipt Image/PDF Ingestion Block")

    receipt_records = []
    raw_files_tracker = []
    uploaded_files_count = 0

    for i in range(int(total_receipts_input)):
        st.markdown(f"#### 📄 Receipt Section Item #{i+1}")
        r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([2.5, 2.5, 2, 1.5, 1.5])

        with r_col1:
            uploaded_file = st.file_uploader(
                f"Upload Receipt #{i+1} (Image or PDF)",
                type=["png", "jpg", "jpeg", "pdf"], key=f"file_{i}"
            )
            raw_files_tracker.append(uploaded_file)
            if uploaded_file is not None:
                uploaded_files_count += 1

        if uploaded_file is not None:
            sig = f"{uploaded_file.name}_{uploaded_file.size}"
            if st.session_state.get(f"sig_{i}") != sig:
                with st.spinner(f"Scanning document #{i+1}..."):
                    d, t, g, dt, raw = parse_receipt_image_or_pdf(uploaded_file)
                st.session_state[f"desc_{i}"] = d
                st.session_state[f"amt_{i}"] = float(t)
                st.session_state[f"gst_{i}"] = float(g)
                st.session_state[f"date_{i}"] = dt
                st.session_state[f"ocr_{i}"] = raw
                st.session_state[f"sig_{i}"] = sig

        with r_col2:
            desc_val = st.text_input(f"Item Description #{i+1}", key=f"desc_{i}")
        with r_col3:
            date_val = st.text_input(f"Receipt Date (DD/MM/YYYY) #{i+1}", key=f"date_{i}")
        with r_col4:
            total_val = st.number_input(f"Amount ($) #{i+1}", min_value=0.0, format="%.2f", key=f"amt_{i}")
        with r_col5:
            gst_val = st.number_input(f"GST ($) #{i+1}", min_value=0.0, format="%.2f", key=f"gst_{i}")

        if f"ocr_{i}" in st.session_state:
            with st.expander(f"Debug: raw OCR text for receipt #{i+1}"):
                st.text(st.session_state[f"ocr_{i}"])

        receipt_records.append({"desc": desc_val, "date": date_val, "amount": total_val, "gst": gst_val})

    calculated_total_claim = sum(r["amount"] for r in receipt_records)
    calculated_total_gst = sum(r["gst"] for r in receipt_records)

    is_amount_matching = abs(calculated_total_claim - declared_total_input) < 0.01
    missing_receipts_delta = int(total_receipts_input) - uploaded_files_count

    st.markdown("---")
    st.markdown("### 📊 Dynamic Live Aggregated Verification Totals")

    sum_col1, sum_col2 = st.columns(2)
    with sum_col1:
        st.metric(label="Total Aggregated Claim Amount (Calculated)", value=f"${calculated_total_claim:,.2f}")
    with sum_col2:
        st.metric(label="Total Aggregated GST Amount (Calculated)", value=f"${calculated_total_gst:,.2f}")

    st.markdown("---")
    st.markdown("### 🛡️ System Submission Safety Validation Checks")

    block_submission = False

    if not is_amount_matching:
        st.error(f"❌ Total Doesn't Match: The sum of your individual row entries (${calculated_total_claim:.2f}) does not match your typed Total Declared Claim Amount (${declared_total_input:.2f}). Please review row values.")
        block_submission = True
    else:
        st.success("✅ Financial balance validation passed: Claim amounts are matching.")

    if missing_receipts_delta > 0:
        st.warning(f"⚠️ {missing_receipts_delta} Receipt Outstanding: You have stated a total count of {total_receipts_input} receipts, but uploaded only {uploaded_files_count} file assets. Please upload remaining attachments.")
        block_submission = True
    elif missing_receipts_delta < 0:
        st.error(f"❌ Attachment Overflow: You have uploaded {uploaded_files_count} assets, which exceeds your stated limit configuration count allocation of {total_receipts_input}.")
        block_submission = True
    else:
        st.success("✅ Attachment verification passed: All structural files accounted for.")

    if advance_amount > calculated_total_claim:
        st.error(f"❌ Advance Too High: The advance being settled (${advance_amount:.2f}) is more than the total claim (${calculated_total_claim:.2f}).")
        block_submission = True

    st.markdown("---")

    if block_submission:
        st.button("Submit", type="primary", disabled=True, help="Please resolve all structural error/warning notifications above to unlock the submit pipeline.")
    else:
        if st.button("Finalize and Submit Expense Claims Bundle", type="primary"):
            if not name or not bsb or not acc_num or not claimant_email or not expense_event_desc:
                st.error("Submission Denied: Profile details, Event Description, Claimant Email, BSB, and Account Numbers must be filled out completely.")
            else:
                nazim_info = st.session_state.admin_lists.get("regional_nazims", {}).get(region, {"name": "Not Configured", "email": ""})

                payload = {
                    "name": name,
                    "region": region,
                    "majlis": majlis,
                    "account": account,
                    "expense_type": expense_event_desc,
                    "total_receipts": len(receipt_records),
                    "total_amount": calculated_total_claim,
                    "total_gst": calculated_total_gst,
                    "bsb": bsb,
                    "acc_num": acc_num,
                    "advance": advance_amount,
                    "qaid_mal": st.session_state.admin_lists.get("qaid_mal_name", "Tahir Mahmood Rana"),
                    "sadr_maa": st.session_state.admin_lists.get("sadr_maa_name", "Abdul Jamil Mubbashar"),
                    "nazim_name": nazim_info.get("name", "Not Configured")
                }

                stamp = now_local().strftime("%Y%m%d_%H%M%S")
                safe_name = re.sub(r"[^A-Za-z0-9_-]", "", name.replace(" ", "_")) or "Claimant"
                output_filename = f"Expense_Voucher_{safe_name}_{stamp}.pdf"
                generate_official_voucher_pdf(output_filename, payload, receipt_records)

                with st.spinner("Stitching receipts into the claim PDF..."):
                    stitched_ok = stitch_files_to_pdf(output_filename, raw_files_tracker)

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

                        file_id, file_link = upload_pdf_to_drive(service, folder_id, output_filename, pdf_bytes)
                        record = {
                            "id": uuid.uuid4().hex[:10],
                            "submitted_on": now_local().strftime('%d/%m/%Y'),
                            "submitted_ts": now_local().isoformat(),
                            "name": name,
                            "event": expense_event_desc,
                            "region": region,
                            "majlis": majlis,
                            "total_claim": round(calculated_total_claim, 2),
                            "gst": round(calculated_total_gst, 2),
                            "advance": round(advance_amount, 2),
                            "amount_payable": round(calculated_total_claim - advance_amount, 2),
                            "file_name": output_filename,
                            "file_id": file_id,
                            "file_link": file_link,
                            "status": STATUS_PENDING,
                            "voucher_no": "",
                            "decided_on": ""
                        }
                        add_claim_to_register(service, folder_id, record)
                        saved_to_drive = True
                    except Exception as e:
                        drive_error = str(e)
                        if "storage quota" in drive_error.lower() or "storagequota" in drive_error.lower():
                            drive_error += " (Google does not allow a service account to save files here. The administrator needs to connect Drive with their own Google account.)"

                st.session_state.last_submission = {
                    "saved": saved_to_drive,
                    "error": drive_error,
                    "filename": output_filename,
                    "pdf_bytes": pdf_bytes,
                }
                if saved_to_drive:
                    st.balloons()

    last_submission = st.session_state.get("last_submission")
    if last_submission:
        st.markdown("---")
        if last_submission["saved"]:
            st.success("🎉 Your claim has been submitted and saved. You can download a copy below for your records.")
        else:
            st.error(
                "Your claim PDF was created, but it could NOT be saved for the administrator. "
                "Please download it below and send it to them directly. "
                f"Details: {last_submission['error']}"
            )
        st.download_button(
            label="📥 Download Official Summary Voucher PDF File",
            data=last_submission["pdf_bytes"],
            file_name=last_submission["filename"],
            mime="application/pdf"
        )
