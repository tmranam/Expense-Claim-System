import streamlit as st
import re
import json
import os
import shutil
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from PIL import Image, ImageOps
import pytesseract
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import pypdf
from pdf2image import convert_from_path
import io
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# -------------------------------------------------------------------------
# DEFENSIVE LINUX PATH ANCHORING FOR STREAMLIT CLOUD INFRASTRUCTURE
# -------------------------------------------------------------------------
detected_path = shutil.which("tesseract")
if detected_path:
    pytesseract.pytesseract.tesseract_cmd = detected_path
else:
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

CONFIG_FILE = "admin_config.json"

# Relational mapping framework configuration dictionary updated for Majlis Ansarullah Australia
DEFAULT_CONFIG = {
    "regions": ["National", "Region North", "Region South"],
    "regional_majlis": {
        "National": ["Head Office", "Finance Dept"],
        "Region North": ["Youth Majlis North", "Local Chapter A"],
        "Region South": ["Youth Majlis South", "Local Chapter B"]
    },
    "emails": ["finance@example.com", "approvals@example.com"],
    "admin_password": "123",
    "smtp_server": "://gmail.com",
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
# PERSISTENT STORAGE MANAGEMENT FOR FREE STREAMLIT TIER
# -------------------------------------------------------------------------
def load_admin_settings():
    """Loads admin configurations from a local JSON file to survive app sleeping."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                
                # --- FIXED CRITICAL FALLBACK STRUCTURE CHECKS ---
                for key in DEFAULT_CONFIG:
                    if key not in data or not data[key]:
                        data[key] = DEFAULT_CONFIG[key]
                return data
        except Exception:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

def save_admin_settings(config_data):
    """Saves admin updates permanently into the repository environment."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

# Initialize settings in state
if 'admin_lists' not in st.session_state:
    st.session_state.admin_lists = load_admin_settings()
# -------------------------------------------------------------------------
# DUAL-FORMAT OCR ENGINE FOR IMAGE & PDF TRANSLATION PROCESSING
# -------------------------------------------------------------------------
def parse_receipt_image_or_pdf(uploaded_file):
    """Processes images or PDFs, returning (description, total, gst, receipt_date, raw_text)."""
    try:
        uploaded_file.seek(0)
        file_ext = os.path.splitext(uploaded_file.name)[1].lower()
        raw_text = ""
        
        # Scenario A: Handle direct PDF digital uploads via page vector scanning blocks
        if file_ext == ".pdf":
            temp_pdf_path = f"temp_ocr_{uploaded_file.name}"
            with open(temp_pdf_path, "wb") as temp_f:
                temp_f.write(uploaded_file.read())
            
            try:
                # Attempt structural digital text reading extraction first
                reader = pypdf.PdfReader(temp_pdf_path)
                for page in reader.pages:
                    text_content = page.extract_text()
                    if text_content:
                        raw_text += text_content + "\n"
                
                # Fallback to raster image conversion parsing if document is a scanned image container
                if not raw_text.strip():
                    converted_images = convert_from_path(temp_pdf_path)
                    for pil_img in converted_images:
                        gray = ImageOps.autocontrast(pil_img.convert("L"))
                        raw_text += pytesseract.image_to_string(gray, config="--psm 6") + "\n"
            finally:
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)
                    
        # Scenario B: Handle standard image asset uploads (PNG, JPG, JPEG)
        else:
            image = Image.open(uploaded_file)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2000, 2000))
            gray = ImageOps.autocontrast(image.convert("L"))
            raw_text = pytesseract.image_to_string(gray, config="--psm 6")

        raw_text_lower = raw_text.lower()
        cleaned_text = re.sub(r'(?<=\d)[ \t]*([.,])[ \t]*(?=\d{2}\b)', r'\1', raw_text_lower)

        # Extrapolate numeric balance points
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

        # Parse data timestamps matching common layouts
        detected_date = datetime.today().strftime('%d/%m/%Y')
        date_patterns = re.findall(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', raw_text)
        if date_patterns:
            detected_date = date_patterns[0].replace('-', '/')

        possible_items = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking',
                          'fuel', 'stationery', 'food', 'meals', 'woolworths', 'coles']
        keywords = [i.title() for i in possible_items if i in cleaned_text]
        description = ", ".join(keywords) if keywords else "General Expense Purchases"

        return description, total_amount, gst_amount, detected_date, raw_text
    except Exception as e:
        return "Scan failed", 0.0, 0.0, datetime.today().strftime('%d/%m/%Y'), f"ERROR: {e}"
# -------------------------------------------------------------------------
# COMPREHENSIVE DYNAMIC MULTI-PAGE REPORTLAB PDF BUILDER MODULE
# -------------------------------------------------------------------------
def generate_official_voucher_pdf(filename, data, receipts):
    """Compiles a dynamic multi-page clone matching the Majlis Ansarullah Australia layout."""
    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    header_title_style = ParagraphStyle('HeadTitle', fontName='Helvetica-Bold', fontSize=14, alignment=1, spaceAfter=2)
    header_subtitle_style = ParagraphStyle('HeadSub', fontName='Helvetica-Bold', fontSize=12, alignment=1, spaceAfter=2)
    dept_style = ParagraphStyle('Dept', fontName='Helvetica', fontSize=11, alignment=1, spaceAfter=2)
    top_line_style = ParagraphStyle('TopLine', fontName='Helvetica-Oblique', fontSize=9, leading=11, alignment=1, spaceAfter=4)
    cell_style = ParagraphStyle('Cell', fontName='Helvetica', fontSize=9, leading=11)
    cell_bold_style = ParagraphStyle('CellB', fontName='Helvetica-Bold', fontSize=9, leading=11)
    
    story.append(Paragraph("In the name of Allah, the gracious, the merciful", top_line_style))
    story.append(Paragraph("MAJLIS ANSARULLAH AUSTRALIA", header_title_style))
    story.append(Paragraph("Department of Mal", dept_style))
    story.append(Paragraph("Expense Payment Voucher", header_subtitle_style))
    story.append(Spacer(1, 10))
    
    header_data = [
        [Paragraph("<b>Payee Name:</b>", cell_style), Paragraph(data['name'], cell_style), Paragraph("<b>Voucher No:</b>", cell_style), Paragraph("", cell_style)],
        [Paragraph("<b>Expense Type:</b>", cell_style), Paragraph(data['expense_type'], cell_style), Paragraph("<b>BSB:</b>", cell_style), Paragraph(data['bsb'], cell_style)],
        [Paragraph("<b>Account:</b>", cell_style), Paragraph(data['account'], cell_style), Paragraph("<b>Account:</b>", cell_style), Paragraph(data['acc_num'], cell_style)],
        [Paragraph("<b>Region:</b>", cell_style), Paragraph(data['region'], cell_style), Paragraph("<b>Total Claim:</b>", cell_bold_style), Paragraph(f"<b>$ {data['total_amount']:.2f}</b>", cell_bold_style)],
        [Paragraph("<b>Majlis:</b>", cell_style), Paragraph(data['majlis'], cell_style), Paragraph("<b>Advance Issued:</b>", cell_style), Paragraph("", cell_style)]
    ]
    
    t_header = Table(header_data, colWidths=[100, 170, 100, 170])
    t_header.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 15))
    
    receipt_table_data = [[Paragraph("<b>#</b>", cell_bold_style), Paragraph("<b>Date</b>", cell_bold_style), Paragraph("<b>Description</b>", cell_bold_style), Paragraph("<b>Total amount</b>", cell_bold_style), Paragraph("<b>GST</b>", cell_bold_style)]]
    
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
            
    receipt_table_data.append([Paragraph("<b>Total</b>", cell_bold_style), "", "", Paragraph(f"<b>${data['total_amount']:.2f}</b>", cell_bold_style), Paragraph(f"<b>${data['total_gst']:.2f}</b>", cell_bold_style)])
    
    t_receipts = Table(receipt_table_data, colWidths=[30, 80, 250, 90, 90])
    t_receipts.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 4),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (3,1), (-1,-1), 'RIGHT')
    ]))
    story.append(t_receipts)
    story.append(Spacer(1, 15))
    
    signoff_data = [
        [Paragraph("<b>Requested by:</b>", cell_bold_style), Paragraph("<b>Reviewed by Nazime A'la:</b>", cell_bold_style)],
        [Paragraph(f"Name: {data['name']}", cell_style), Paragraph(f"Name: {data['nazim_name']}", cell_style)],
        [Paragraph("Signature:", cell_style), Paragraph("Signature:", cell_style)],
        [Paragraph(f"Date: {datetime.today().strftime('%d/%m/%Y')}", cell_style), Paragraph("Date:", cell_style)],
        [Paragraph("<b>Approved by Qaid Mal:</b>", cell_bold_style), Paragraph("<b>Approved by Sadr MAA:</b>", cell_bold_style)],
        [Paragraph(f"Name: {data['qaid_mal']}", cell_style), Paragraph(f"Name: {data['sadr_maa']}", cell_style)],
        [Paragraph("Signature: <i>Tahir Mehmood</i>", cell_style), Paragraph("Signature:", cell_style)],
        [Paragraph("Date:", cell_style), Paragraph("Date:", cell_style)],
    ]
    
    t_signoff = Table(signoff_data, colWidths=[270, 270])
    t_signoff.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,0), (1,0), colors.lightgrey),
        ('BACKGROUND', (0,4), (1,4), colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_signoff)
    
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Acknowledgment</b>", header_subtitle_style))
    story.append(Paragraph("I have received cash/cheque payment of $________________ as per details provided above.", cell_style))
    story.append(Paragraph("<b>Comments:</b> Hand written Receipt approved by Sadr Majlis. (Updated: 1-11-19)", cell_style))
    
    doc.build(story)

# -------------------------------------------------------------------------
# DYNAMIC MULTI-FORMAT APPRENDIX BINDER STITCHING PIPELINE
# -------------------------------------------------------------------------
def stitch_files_to_pdf(voucher_pdf_path, uploaded_files_list):
    """Stitches multi-format attachments directly onto the base voucher sheet."""
    try:
        merger = pypdf.PdfMerger()
        merger.append(voucher_pdf_path)
        
        for idx, u_file in enumerate(uploaded_files_list):
            if u_file is not None:
                u_file.seek(0)
                ext = os.path.splitext(u_file.name)[1].lower()
                
                # Directly merge PDF file submissions
                if ext == ".pdf":
                    merger.append(u_file)
                # Convert and append image file submissions
                else:
                    img = Image.open(u_file)
                    img = ImageOps.exif_transpose(img)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    
                    tmp_img_pdf = f"tmp_stitch_{idx}.pdf"
                    img.save(tmp_img_pdf, "PDF", resolution=100.0)
                    merger.append(tmp_img_pdf)
                    if os.path.exists(tmp_img_pdf):
                        os.remove(tmp_img_pdf)
                        
        merger.write(voucher_pdf_path)
        merger.close()
        return True
    except Exception as e:
        st.error(f"⚠️ Appending engine notice: Attachment compilation encountered a restriction vector: {e}")
        return False
# -------------------------------------------------------------------------
# DIRECT EMAIL OUTBOUND DISPATCH ENGINE (SMTP)
# -------------------------------------------------------------------------
def _image_to_pdf_buffer(u_file, label):
    """Places an image receipt on a letter-size page, scaled to fit."""
    img = Image.open(u_file)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    page_w, page_h = letter
    margin = 36
    header_h = 20
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin - header_h

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
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
# CORE FRONT-END SELECTION ROUTER
# -------------------------------------------------------------------------
st.set_page_config(page_title="Expense Portal (Official MAA Clone)", layout="wide")
app_mode = st.sidebar.selectbox("Navigate Mode", ["User Claim Portal", "Admin Panel Configuration"])

if app_mode == "Admin Panel Configuration":
    st.title("⚙️ Admin Control Panel")
    pwd_input = st.text_input("Enter Master Admin Access Key Password", type="password")
    
    if pwd_input == st.session_state.admin_lists.get("admin_password", "AdminPassword123"):
        st.success("Access Verified. Secure Configuration Screen Unlocked.")
        
        new_pwd = st.text_input("Modify System Admin Password Key", value=st.session_state.admin_lists.get("admin_password", "AdminPassword123"))
        
        st.markdown("### 👑 Central Executive Authority Configuration (Global Overrides)")
        q_mal = st.text_input("Approved by Qaid Mal - Target Field Assignment Name", value=st.session_state.admin_lists.get("qaid_mal_name", "Tahir Mahmood Rana"))
        s_maa = st.text_input("Approved by Sadr MAA - Target Field Assignment Name", value=st.session_state.admin_lists.get("sadr_maa_name", "Abdul Jamil Mubbashar"))
        
        st.markdown("---")
        regions_text = st.text_area("Regions Master List Configuration (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
        parsed_regions_list = [line.strip() for line in regions_text.split("\n") if line.strip()]
        
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
        smtp_srv = st.text_input("SMTP Server Relay Node Hostname", value=st.session_state.admin_lists.get("smtp_server", "://gmail.com"))
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
# INTERACTIVE CLAIM FORM LAYOUT PANEL
# -------------------------------------------------------------------------
else:
    st.title("📋 Online Expense Claim Submission System")
    
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Employee / Claimant Name (Payee Name)")
        claimant_email = st.text_input("Your Personal/Employee Email Address (For confirmation copy)")
        expense_event_desc = st.text_input("Description of Event (Expense Type)")
        
        configured_regions = st.session_state.admin_lists["regions"]
        region = st.selectbox("Select Region / National Location", options=configured_regions)
        
        majlis_lookup_map = st.session_state.admin_lists["regional_majlis"]
        available_majlis_options = majlis_lookup_map.get(region, [])
        if not available_majlis_options:
            available_majlis_options = ["No entries declared under this region boundary"]
        
        majlis = st.selectbox("Select Respective Residing Majlis / Office Destination", options=available_majlis_options)
        account = st.text_input("General Ledger / Tracking Account Code (Account)")
        
    with col2:
        total_receipts_input = st.number_input("Total Number of Receipts To Claim", min_value=1, max_value=50, value=1, step=1)
        declared_total_input = st.number_input("Total Declared Claim Amount ($)", min_value=0.0, format="%.2f")
        
        st.markdown("**🏦 Direct Reimbursement Bank Segment**")
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7)
        acc_num = st.text_input("Account Number")

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

    st.markdown("---")

    if block_submission:
        st.button("Finalize and Submit Expense Claims Bundle", type="primary", disabled=True, help="Please resolve all structural error/warning notifications above to unlock the submit pipeline.")
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
                    "qaid_mal": st.session_state.admin_lists.get("qaid_mal_name", "Tahir Mahmood Rana"),
                    "sadr_maa": st.session_state.admin_lists.get("sadr_maa_name", "Abdul Jamil Mubbashar"),
                    "nazim_name": nazim_info.get("name", "Not Configured")
                }

                output_filename = f"Expense_Voucher_{name.replace(' ', '_')}.pdf"

                generate_official_voucher_pdf(output_filename, payload, receipt_records)

                with st.spinner("Stitching receipts into the claim PDF..."):
                    stitched_ok = stitch_files_to_pdf(output_filename, raw_files_tracker)

                if not stitched_ok:
                    st.stop()

                st.success("🎉 Claim pack compiled: summary page + all receipts!")
                st.balloons()

                target_email = nazim_info.get("email", "")
                if target_email:
                    recipient_emails = [target_email]
                    subject = f"Expense Payment Voucher Submission - {name} ({region})"
                    body = f"Assalam-o-Alaikum,\n\nA new expense payment voucher pack has been generated and submitted by {name} for the '{majlis}' branch of the '{region}' region.\n\nPlease find the comprehensive document statement bundle (Voucher Sheet + Attached Receipts) attached below for your immediate review.\n\nBest regards,\nAutomated Expense Portal Gateway"

                    with st.spinner("Sending the PDF bundle by email..."):
                        success = dispatch_real_email(
                            recipient_list=recipient_emails,
                            subject_text=subject,
                            body_text=body,
                            attachment_path=output_filename,
                            cc_recipient=claimant_email.strip()
                        )

                    if success:
                        st.success(f"📧 Email sent to Nazim e Ala ({target_email}) and CC'd to {claimant_email}")
                else:
                    st.warning("⚠️ Email delivery skipped: No Nazim e Ala email address configured for this region in admin settings.")

                with open(output_filename, "rb") as f:
                    pdf_bytes = f.read()
                st.download_button(
                    label="📥 Download Official Summary Voucher PDF File",
                    data=pdf_bytes,
                    file_name=output_filename,
                    mime="application/pdf"
                )
