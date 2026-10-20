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
        file_ext = os.path.splitext(uploaded_file.name).lower()
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
                gst_amount = float(gst_numbers.replace(',', '.'))
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
                ext = os.path.splitext(u_file.name).lower()
                
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
def dispatch_real_email(recipient_list, subject_text, body_text, attachment_path, cc_recipient=None):
    """Logs into configured SMTP engine server to transmit automated summary file payloads with optional CC."""
    smtp_info = st.session_state.admin_lists
    
    if not smtp_info.get("smtp_user") or not smtp_info.get("smtp_password"):
        st.error("⚠️ Real Delivery Skipped: Outbound SMTP User Email and Password keys must be filled out on the Admin Configuration screen.")
        return False
        
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_info["smtp_user"]
        msg['To'] = ", ".join(recipient_list)
        msg['Subject'] = subject_text
        
        all_recipients = list(recipient_list)
        if cc_recipient:
            msg['Cc'] = cc_recipient
            all_recipients.append(cc_recipient)
        
        msg.attach(MIMEText(body_text, 'plain'))
        
        if os.path.exists(attachment_path):
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename= {os.path.basename(attachment_path)}",
            )
            msg.attach(part)
        
        server = smtplib.SMTP(smtp_info["smtp_server"], smtp_info["smtp_port"])
        server.starttls()
        server.login(smtp_info["smtp_user"], smtp_info["smtp_password"])
        server.sendmail(smtp_info["smtp_user"], all_recipients, msg.as_string())
        server.quit()
        return True
    except Exception as email_err:
        st.error(f"❌ Failed to dispatch email via mail network relay servers. Internal Error details: {email_err}")
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
# =========================================================================
# BATCH 6 OF 6: LIVE AGGREGATION, VALIDATION BARRIERS, AND SUBMISSION DISPATCH
# =========================================================================

    # Continuous background aggregation loops reading live calculated user edits instantly
    calculated_total_claim = sum(r["amount"] for r in receipt_records)
    calculated_total_gst = sum(r["gst"] for r in receipt_records)
    
    # Mathematical balance verification validation script checks
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

    # Interactive blocking flags initialization parameters
    block_submission = False

    # 1. Verification of line item total amount entries matching claimant expectations
    if not is_amount_matching:
        st.error(f"❌ Total Doesn't Match: The sum of your individual row entries (${calculated_total_claim:.2f}) does not match your typed Total Declared Claim Amount (${declared_total_input:.2f}). Please review row values.")
        block_submission = True
    else:
        st.success("✅ Financial balance validation passed: Claim amounts are matching.")

    # 2. Verification of total line item count matched against physically present files
    if missing_receipts_delta > 0:
        st.warning(f"⚠️ {missing_receipts_delta} Receipt Outstanding: You have stated a total count of {total_receipts_input} receipts, but uploaded only {uploaded_files_count} file assets. Please upload remaining attachments.")
        block_submission = True
    elif missing_receipts_delta < 0:
        st.error(f"❌ Attachment Overflow: You have uploaded {uploaded_files_count} assets, which exceeds your stated limit configuration count allocation of {total_receipts_input}.")
        block_submission = True
    else:
        st.success("✅ Attachment verification passed: All structural files accounted for.")

    st.markdown("---")

    # Dynamic interface enforcement: submit button is deactivated until validation errors are cleared
    if block_submission:
        st.button("Finalize and Submit Expense Claims Bundle", type="primary", disabled=True, help="Please resolve all structural error/warning notifications above to unlock the submit pipeline.")
    else:
        if st.button("Finalize and Submit Expense Claims Bundle", type="primary"):
            if not name or not bsb or not acc_num or not claimant_email or not expense_event_desc:
                st.error("Submission Denied: Profile details, Event Description, Claimant Email, BSB, and Account Numbers must be filled out completely.")
            else:
                # Fetch localized admin configuration variables mapped out under Batch 4
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
                
                # Step 1: Compile primary formal ReportLab voucher sheet structure matching the MAA official format
                generate_official_voucher_pdf(output_filename, payload, receipt_records)
                
                # Step 2: Extract uploaded images or PDFs and stitch them as consecutive backend attachment pages
                with st.spinner("Stitching file assets into multi-page formal PDF binder format..."):
                    stitch_files_to_pdf(output_filename, raw_files_tracker)
                    
                st.success("🎉 Comprehensive Claim Payment Voucher Pack compiled successfully with attached file pages!")
                st.balloons()
                
                # ROUTING ENGINE: Delivers directly to dynamic Nazim e Ala and CCs claimant
                target_email = nazim_info.get("email", "")
                if target_email:
                    recipient_emails = [target_email]
                    subject = f"Expense Payment Voucher Submission - {name} ({region})"
                    body = f"Assalam-o-Alaikum,\n\nA new expense payment voucher pack has been generated and submitted by {name} for the '{majlis}' branch of the '{region}' region.\n\nPlease find the comprehensive document statement bundle (Voucher Sheet + Attached Receipts) attached below for your immediate review.\n\nBest regards,\nAutomated Expense Portal Gateway"
                    
                    with st.spinner("Transmitting secure PDF files bundle across outbound networks..."):
                        success = dispatch_real_email(
                            recipient_list=recipient_emails, 
                            subject_text=subject, 
                            body_text=body, 
                            attachment_path=output_filename,
                            cc_recipient=claimant_email.strip()
                        )
                    
                    if success:
                        st.success(f"📧 Outbound Email Dispatched! Delivered directly to Nazim e Ala ({target_email}) and carbon-copied (CC) to claimant address: {claimant_email}")
                else:
                    st.warning("⚠️ Email delivery skipped: No Nazim e Ala email address configured for this region in admin settings.")
                
                pdf_bytes = open(output_filename, "rb").read()
                st.download_button(
                    label="📥 Download Official Summary Voucher PDF File",
                    data=pdf_bytes,
                    file_name=output_filename,
                    mime="application/pdf"
                )
