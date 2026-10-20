import streamlit as st
import re
import json
import os
import shutil
import smtplib
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

# -------------------------------------------------------------------------
# DEFENSIVE LINUX PATH ANCHORING FOR STREAMLIT CLOUD INFRASTRUCTURE
# -------------------------------------------------------------------------
detected_path = shutil.which("tesseract")
if detected_path:
    pytesseract.pytesseract.tesseract_cmd = detected_path
else:
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

CONFIG_FILE = "admin_config.json"

# Relational mapping framework configuration dictionary supporting dynamic dependencies
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
    "smtp_user": "your_email@gmail.com",
    "smtp_password": ""
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
                if "regions" not in data or not data["regions"]:
                    data["regions"] = DEFAULT_CONFIG["regions"]
                if "regional_majlis" not in data or not data["regional_majlis"]:
                    data["regional_majlis"] = DEFAULT_CONFIG["regional_majlis"]
                if "admin_password" not in data:
                    data["admin_password"] = DEFAULT_CONFIG["admin_password"]
                if "emails" not in data:
                    data["emails"] = DEFAULT_CONFIG["emails"]
                if "smtp_server" not in data:
                    data["smtp_server"] = DEFAULT_CONFIG["smtp_server"]
                if "smtp_port" not in data:
                    data["smtp_port"] = DEFAULT_CONFIG["smtp_port"]
                if "smtp_user" not in data:
                    data["smtp_user"] = DEFAULT_CONFIG["smtp_user"]
                if "smtp_password" not in data:
                    data["smtp_password"] = DEFAULT_CONFIG["smtp_password"]
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
# OPTIMIZED WORKING OCR ENGINE LOGIC
# -------------------------------------------------------------------------
def parse_receipt_image(uploaded_file):
    """Returns (description, total, gst, raw_text)."""
    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)   # fixes sideways phone photos
        image.thumbnail((2000, 2000))

        # Gentle preprocessing: Tesseract binarizes well on its own
        gray = ImageOps.autocontrast(image.convert("L"))

        raw_text = pytesseract.image_to_string(gray, config="--psm 6").lower()

        # Fix "12 .50" / "12. 50" style breaks on the same line
        text = re.sub(r'(?<=\d)[ \t]*([.,])[ \t]*(?=\d{2}\b)', r'\1', raw_text)

        amounts = []
        for m in re.findall(r'\b\d+[.,]\d{2}\b', text):
            try:
                amounts.append(float(m.replace(',', '.')))
            except ValueError:
                pass
        total_amount = max(amounts) if amounts else 0.0

        gst_amount = 0.0
        if "gst" in text or "tax" in text:
            gst_numbers = re.findall(r'(?:gst|tax)[^\d\n]*(\d+[.,]\d{2})', text)
            if gst_numbers:
                gst_amount = float(gst_numbers.replace(',', '.'))
            else:
                gst_amount = round(total_amount / 11, 2)

        possible_items = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking',
                          'fuel', 'stationery', 'food', 'meals', 'woolworths', 'coles']
        keywords = [i.title() for i in possible_items if i in text]
        description = ", ".join(keywords) if keywords else "General Expense Purchases"

        return description, total_amount, gst_amount, raw_text
    except Exception as e:
        return "Scan failed", 0.0, 0.0, f"ERROR: {e}"

def generate_pdf(filename, data, receipts):
    """Compiles a clean document statement using ReportLab canvas flowables."""
    doc = SimpleDocTemplate(filename, pagesize=letter)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], spaceAfter=12, textColor=colors.HexColor("#1A365D"))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2B6CB0"))
    
    story.append(Paragraph("Expense Claim Summary Statement", title_style))
    story.append(Spacer(1, 12))
    
    profile_data = [
        [Paragraph(f"<b>Name:</b> {data['name']}", styles['Normal']), Paragraph(f"<b>Region/National:</b> {data['region']}", styles['Normal'])],
        [Paragraph(f"<b>Majlis/Office:</b> {data['majlis']}", styles['Normal']), Paragraph(f"<b>Account Category:</b> {data['account']}", styles['Normal'])],
        [Paragraph(f"<b>BSB No:</b> {data['bsb']}", styles['Normal']), Paragraph(f"<b>Account No:</b> {data['acc_num']}", styles['Normal'])],
        [Paragraph(f"<b>Total Receipts:</b> {data['total_receipts']}", styles['Normal']), Paragraph(f"<b>Declared Total:</b> ${data['total_amount']:.2f}", styles['Normal'])]
    ]
    
    t_profile = Table(profile_data, colWidths=[250, 250])
    t_profile.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#EDF2F7")),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_profile)
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Itemized Receipt Breakdowns", heading_style))
    receipt_table_data = [["#", "Description", "GST Amt", "Total Amt"]]
    
    for idx, r in enumerate(receipts):
        receipt_table_data.append([
            str(idx + 1),
            r['desc'],
            f"${r['gst']:.2f}",
            f"${r['amount']:.2f}"
        ])
        
    t_receipts = Table(receipt_table_data, colWidths=[30, 290, 90, 90])
    t_receipts.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (2,1), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_receipts)
    
    doc.build(story)    
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
# INTERFACE FRONT-END DESIGN
# -------------------------------------------------------------------------
st.set_page_config(page_title="Expense Portal (Cloud Ready)", layout="wide")

app_mode = st.sidebar.selectbox("Navigate Mode", ["User Claim Portal", "Admin Panel Configuration"])

# -------------------------------------------------------------------------
# PASSWORD-PROTECTED SECURE ADMIN INTERFACE PANEL
# -------------------------------------------------------------------------
if app_mode == "Admin Panel Configuration":
    st.title("⚙️ Admin Control Panel")
    
    pwd_input = st.text_input("Enter Master Admin Access Key Password", type="password")
    
    if pwd_input == st.session_state.admin_lists.get("admin_password", "AdminPassword123"):
        st.success("Access Verified. Secure Configuration Screen Unlocked.")
        
        new_pwd = st.text_input("Modify System Admin Password Key", value=st.session_state.admin_lists.get("admin_password", "AdminPassword123"))
        
        regions_text = st.text_area("Regions Master List Configuration (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
        parsed_regions_list = [line.strip() for line in regions_text.split("\n") if line.strip()]
        
        st.markdown("---")
        st.markdown("#### 🗺️ Regional Majlis Assignment Panel")
        st.caption("Select a region to allocate its respective Majlis entries.")
        
        selected_admin_region = st.selectbox("Select a region to configure its Majlis", options=parsed_regions_list)
        
        current_map = st.session_state.admin_lists.get("regional_majlis", {})
        existing_majlis_for_region = current_map.get(selected_admin_region, [])
        
        majlis_text = st.text_area(f"Enter Majlis names for '{selected_admin_region}' (One entry per line)", value="\n".join(existing_majlis_for_region), height=140)
        parsed_majlis_list = [line.strip() for line in majlis_text.split("\n") if line.strip()]
        
        st.markdown("---")
        st.markdown("#### ✉️ Target Email Assignment Matrix")
        st.caption("Route form notifications dynamically based on user selections.")
        
        email_region_select = st.selectbox("Step 1: Pick Target Region for Emails", options=parsed_regions_list, key="email_reg")
        available_majlis_for_email = parsed_majlis_list if email_region_select == selected_admin_region else current_map.get(email_region_select, [])
        
        if available_majlis_for_email:
            email_majlis_select = st.selectbox(f"Step 2: Pick Target Majlis in '{email_region_select}'", options=available_majlis_for_email)
            
            routing_key = f"{email_region_select}|{email_majlis_select}"
            current_routing_map = st.session_state.admin_lists.get("targeted_emails", {})
            existing_emails = current_routing_map.get(routing_key, [])
            
            majlis_emails_text = st.text_area(f"Emails for '{email_majlis_select}' in '{email_region_select}' (One entry per line)", value="\n".join(existing_emails), height=120)
            parsed_majlis_emails = [line.strip() for line in majlis_emails_text.split("\n") if line.strip()]
        else:
            st.warning("Please save your configuration or declare at least one Majlis entry above to map specific emails.")
            routing_key = None
            parsed_majlis_emails = []

        st.markdown("---")
        emails_text = st.text_area("Global Fallback Emails (Used if no specific Majlis email is set)", value="\n".join(st.session_state.admin_lists.get("emails", [])))
        
        st.markdown("---")
        st.markdown("#### 🔐 Outbound live SMTP Server Credentials Gateway")
        st.caption("Enter the dedicated system email account details responsible for distributing system emails.")
        
        smtp_srv = st.text_input("SMTP Server Relay Node Hostname", value=st.session_state.admin_lists.get("smtp_server", "://gmail.com"))
        smtp_prt = st.number_input("SMTP Server TLS Port Number", value=int(st.session_state.admin_lists.get("smtp_port", 587)), step=1)
        smtp_usr = st.text_input("Sender User Authentication Email Address ID (e.g., info@yourorg.com)", value=st.session_state.admin_lists.get("smtp_user", ""))
        smtp_pwd = st.text_input("Sender Account App Password Access Key", value=st.session_state.admin_lists.get("smtp_password", ""), type="password")
        
        if st.button("Save System Configuration Schema Matrix", type="primary"):
            updated_map = dict(current_map)
            updated_map[selected_admin_region] = parsed_majlis_list
            updated_map = {k: v for k, v in updated_map.items() if k in parsed_regions_list}
            for r in parsed_regions_list:
                if r not in updated_map:
                    updated_map[r] = []
            
            updated_routing_map = dict(st.session_state.admin_lists.get("targeted_emails", {}))
            if routing_key:
                updated_routing_map[routing_key] = parsed_majlis_emails
                
            valid_routing_map = {}
            for key, emails in updated_routing_map.items():
                if "|" in key:
                    r_part, m_part = key.split("|", 1)
                    if r_part in updated_map and m_part in updated_map[r_part]:
                        valid_routing_map[key] = emails

            updated_config = {
                "regions": parsed_regions_list,
                "regional_majlis": updated_map,
                "targeted_emails": valid_routing_map,
                "emails": [line.strip() for line in emails_text.split("\n") if line.strip()],
                "admin_password": new_pwd.strip(),
                "smtp_server": smtp_srv.strip(),
                "smtp_port": int(smtp_prt),
                "smtp_user": smtp_usr.strip(),
                "smtp_password": smtp_pwd.strip()
            }
            
            save_admin_settings(updated_config)
            st.session_state.admin_lists = updated_config
            st.success("Global application schema matrix and mail gateway routing configurations successfully written!")
            st.rerun()
            
    elif pwd_input != "":
        st.error("Access Refused: Secure validation key does not match records.")
  # -------------------------------------------------------------------------
# INTERACTIVE CLAIM FORM LAYOUT PANEL
# -------------------------------------------------------------------------
else:
    st.title("📋 Online Expense Claim Submission System")
    
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Employee / Claimant Name")
        claimant_email = st.text_input("Your Personal/Employee Email Address (For confirmation copy)")
        
        configured_regions = st.session_state.admin_lists["regions"]
        region = st.selectbox("Select Region / National Location", options=configured_regions)
        
        majlis_lookup_map = st.session_state.admin_lists["regional_majlis"]
        available_majlis_options = majlis_lookup_map.get(region, [])
        if not available_majlis_options:
            available_majlis_options = ["No entries declared under this region boundary"]
        
        majlis = st.selectbox("Select Respective Residing Majlis / Office Destination", options=available_majlis_options)
        account = st.text_input("General Ledger / Tracking Account Code")
        
    with col2:
        total_receipts_input = st.number_input("Total Number of Receipts", min_value=1, max_value=10, value=1, step=1)
        total_amount_input = st.number_input("Total Aggregated Summary Amount ($)", min_value=0.0, format="%.2f")
        st.markdown("**🏦 Direct Reimbursement Bank Segment**")
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7)
        acc_num = st.text_input("Account Number")

    st.markdown("---")
    st.subheader("📎 Individual Itemized Receipt Image Ingestion Block")
    
    receipt_records = []
    
    for i in range(int(total_receipts_input)):
        st.markdown(f"#### 📄 Receipt Section Item #{i+1}")
        
        r_col1, r_col2, r_col3, r_col4 = st.columns([2, 2, 1, 1])

        with r_col1:
            uploaded_file = st.file_uploader(
                f"Upload Image Asset for Receipt #{i+1}",
                type=["png", "jpg", "jpeg"], key=f"file_{i}"
            )
        initial_desc, initial_total, initial_gst = "No file parsed", 0.0, 0.0

        if uploaded_file is not None:
            sig = f"{uploaded_file.name}_{uploaded_file.size}"
            if st.session_state.get(f"sig_{i}") != sig:
                with st.spinner(f"Scanning document #{i+1}..."):
                    d, t, g, raw = parse_receipt_image(uploaded_file)
                st.session_state[f"desc_{i}"] = d
                st.session_state[f"amt_{i}"] = float(t)
                st.session_state[f"gst_{i}"] = float(g)
                st.session_state[f"ocr_{i}"] = raw
                st.session_state[f"sig_{i}"] = sig

        with r_col2:
            desc_val = st.text_input(f"Item Description #{i+1}", key=f"desc_{i}")
        with r_col3:
            total_val = st.number_input(f"Amount ($) #{i+1}", min_value=0.0, format="%.2f", key=f"amt_{i}")
        with r_col4:
            gst_val = st.number_input(f"GST ($) #{i+1}", min_value=0.0, format="%.2f", key=f"gst_{i}")

        if f"ocr_{i}" in st.session_state:
            with st.expander(f"Debug: raw OCR text for receipt #{i+1}"):
                st.text(st.session_state[f"ocr_{i}"])

        receipt_records.append({"desc": desc_val, "amount": total_val, "gst": gst_val})

    st.markdown("---")

    if st.button("Finalize and Submit Expense Claims Bundle", type="primary"):
        if not name or not bsb or not acc_num or not claimant_email:
            st.error("Submission Denied: Profile details, Claimant Email, BSB, and Account Numbers must be filled out.")
        else:
            payload = {
                "name": name, 
                "region": region, 
                "majlis": majlis, 
                "account": account,
                "total_receipts": total_receipts_input, 
                "total_amount": total_amount_input,
                "bsb": bsb, 
                "acc_num": acc_num
            }
            output_filename = "Expense_Claim_Statement.pdf"
            generate_pdf(output_filename, payload, receipt_records)
            st.success("🎉 Claim Bundle Form processed successfully!")
            st.balloons()
            
            # DYNAMIC ROUTING RESOLUTION PIPELINE
            lookup_key = f"{region}|{majlis}"
            targeted_routing_map = st.session_state.admin_lists.get("targeted_emails", {})
            
            recipient_emails = targeted_routing_map.get(lookup_key, [])
            is_fallback = False
            if not recipient_emails:
                recipient_emails = st.session_state.admin_lists.get("emails", [])
                is_fallback = True
                
            if recipient_emails:
                subject = f"New Expense Claim Submission - {name} ({region})"
                body = f"Hello,\n\nA new expense claim statement summary pack has been compiled and submitted by {name} for the '{majlis}' branch of '{region}' region.\n\nPlease find the summary document statement sheet attached below.\n\nBest regards,\nAutomated Expense Portal Gateway"
                
                with st.spinner("Transmitting secure PDF files bundle across outbound networks..."):
                    # Dispatches to target list and directly CCs the claimant
                    success = dispatch_real_email(
                        recipient_list=recipient_emails, 
                        subject_text=subject, 
                        body_text=body, 
                        attachment_path=output_filename,
                        cc_recipient=claimant_email.strip()
                    )
                
                if success:
                    routing_type = "Fallback" if is_fallback else "Targeted Match"
                    st.success(f"📧 Outbound Email Dispatched! ({routing_type}) PDF successfully delivered to regional targets and carbon-copied (CC) to claimant: {claimant_email}")
            else:
                st.warning("⚠️ Email transmission skipped: No operational email recipients defined in system settings.")
            
            pdf_bytes = open(output_filename, "rb").read()
            st.download_button(
                label="📥 Download Generated Summary Receipt PDF File",
                data=pdf_bytes,
                file_name=output_filename,
                mime="application/pdf"
            )  
