import streamlit as st
import re
import json
import os
import shutil
from datetime import datetime
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

# Relational mapping framework configuration dictionary supporting extended dependencies
DEFAULT_CONFIG = {
    "regions": ["National", "Region North", "Region South"],
    "regional_majlis": {
        "National": ["Head Office", "Finance Dept"],
        "Region North": ["Youth Majlis North", "Local Chapter A"],
        "Region South": ["Youth Majlis South", "Local Chapter B"]
    },
    "emails": ["finance@example.com", "approvals@example.com"],
    "admin_password": "123",
    "regional_details": {
        "National": {"nazim_name": "Tahir Ahmad", "nazim_email": "nazim.national@example.com", "sadr_majlis": "Sadr Sahib National", "qaid_mal": "Qaid Mal National"},
        "Region North": {"nazim_name": "Naseer Ahmad", "nazim_email": "nazim.north@example.com", "sadr_majlis": "Sadr Sahib North", "qaid_mal": "Qaid Mal North"},
        "Region South": {"nazim_name": "Mansoor Ahmad", "nazim_email": "nazim.south@example.com", "sadr_majlis": "Sadr Sahib South", "qaid_mal": "Qaid Mal South"}
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
                if "regions" not in data or not data["regions"]:
                    data["regions"] = DEFAULT_CONFIG["regions"]
                if "regional_majlis" not in data or not data["regional_majlis"]:
                    data["regional_majlis"] = DEFAULT_CONFIG["regional_majlis"]
                if "admin_password" not in data:
                    data["admin_password"] = DEFAULT_CONFIG["admin_password"]
                if "emails" not in data:
                    data["emails"] = DEFAULT_CONFIG["emails"]
                if "regional_details" not in data:
                    data["regional_details"] = DEFAULT_CONFIG["regional_details"]
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
    """Returns (description, total, gst, inferred_date, raw_text)."""
    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((2000, 2000))

        gray = ImageOps.autocontrast(image.convert("L"))
        raw_text = pytesseract.image_to_string(gray, config="--psm 6").lower()

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

        # Basic regex to grab receipt date style occurrences
        date_match = re.search(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', raw_text)
        inferred_date = date_match.group(0) if date_match else datetime.today().strftime('%d/%m/%Y')

        possible_items = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking',
                          'fuel', 'stationery', 'food', 'meals', 'woolworths', 'coles']
        keywords = [i.title() for i in possible_items if i in text]
        description = ", ".join(keywords) if keywords else "General Expense Purchases"

        return description, total_amount, gst_amount, inferred_date, raw_text
    except Exception as e:
        return "Scan failed", 0.0, 0.0, datetime.today().strftime('%d/%m/%Y'), f"ERROR: {e}"

def generate_pdf(filename, data, receipts):
    """Compiles a clean document statement using ReportLab canvas flowables."""
    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], spaceAfter=12, textColor=colors.HexColor("#1A365D"))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2B6CB0"))
    normal_style = styles['Normal']
    
    story.append(Paragraph("Expense Claim Summary Statement", title_style))
    story.append(Spacer(1, 12))
    
    profile_data = [
        [Paragraph(f"<b>Claimant Name:</b> {data['name']}", normal_style), Paragraph(f"<b>Region/National:</b> {data['region']}", normal_style)],
        [Paragraph(f"<b>Claimant Email:</b> {data['claimant_email']}", normal_style), Paragraph(f"<b>Majlis/Office:</b> {data['majlis']}", normal_style)],
        [Paragraph(f"<b>BSB No:</b> {data['bsb']}", normal_style), Paragraph(f"<b>Account Category:</b> {data['account']}", normal_style)],
        [Paragraph(f"<b>Account No:</b> {data['acc_num']}", normal_style), Paragraph(f"<b>Total Receipts:</b> {data['total_receipts']}", normal_style)],
        [Paragraph(f"<b>Total Claim Amount:</b> ${data['total_amount']:.2f}", normal_style), Paragraph(f"<b>Total GST Amount:</b> ${data['total_gst']:.2f}", normal_style)],
        [Paragraph(f"<b>Sadr Majlis:</b> {data['sadr_majlis']}", normal_style), Paragraph(f"<b>Qaid Mal:</b> {data['qaid_mal']}", normal_style)],
        [Paragraph(f"<b>Nazim-e-Ala:</b> {data['nazim_name']} ({data['nazim_email']})", normal_style), Paragraph("", normal_style)]
    ]
    
    t_profile = Table(profile_data, colWidths=[270, 270])
    t_profile.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#EDF2F7")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_profile)
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Itemized Receipt Breakdowns", heading_style))
    receipt_table_data = [["#", "Invoice Date", "Description", "GST Amt", "Total Amt"]]
    
    for idx, r in enumerate(receipts):
        receipt_table_data.append([
            str(idx + 1),
            r['date'],
            r['desc'],
            f"${r['gst']:.2f}",
            f"${r['amount']:.2f}"
        ])
        
    t_receipts = Table(receipt_table_data, colWidths=[30, 90, 240, 90, 90])
    t_receipts.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (3,1), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_receipts)
    
    doc.build(story)
# -------------------------------------------------------------------------
# INTERFACE FRONT-END DESIGN
# -------------------------------------------------------------------------
st.set_page_config(page_title="Expense Portal (Cloud Ready)", layout="wide")

# Navigation Sidebar
app_mode = st.sidebar.selectbox("Navigate Mode", ["User Claim Portal", "Admin Panel Configuration"])

# -------------------------------------------------------------------------
# PASSWORD-PROTECTED SECURE ADMIN INTERFACE PANEL
# -------------------------------------------------------------------------
if app_mode == "Admin Panel Configuration":
    st.title("⚙️ Admin Control Panel")
    
    pwd_input = st.text_input("Enter Master Admin Access Key Password", type="password")
    
    if pwd_input == st.session_state.admin_lists.get("admin_password", "123"):
        st.success("Access Verified. Secure Configuration Screen Unlocked.")
        
        new_pwd = st.text_input("Modify System Admin Password Key", value=st.session_state.admin_lists.get("admin_password", "123"))
        
        # 1. Master configurations for Regions
        regions_text = st.text_area("Regions Master List Configuration (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
        parsed_regions_list = [line.strip() for line in regions_text.split("\n") if line.strip()]
        
        st.markdown("---")
        st.markdown("#### 🗺️ Regional Details & Assignment Matrix Panel")
        st.caption("Select a region to configure its Majlis list, Nazim-e-Ala, Sadr Majlis, and Qaid Mal fields.")
        
        selected_admin_region = st.selectbox("Select a region to configure", options=parsed_regions_list)
        
        current_map = st.session_state.admin_lists.get("regional_majlis", {})
        existing_majlis_for_region = current_map.get(selected_admin_region, [])
        
        majlis_text = st.text_area(f"Enter Majlis names for '{selected_admin_region}' (One entry per line)", value="\n".join(existing_majlis_for_region), height=100)
        parsed_majlis_list = [line.strip() for line in majlis_text.split("\n") if line.strip()]
        
        # Load extended regional parameters dictionary securely
        current_details = st.session_state.admin_lists.get("regional_details", {})
        reg_data = current_details.get(selected_admin_region, {"nazim_name": "", "nazim_email": "", "sadr_majlis": "", "qaid_mal": ""})
        
        col_adm1, col_adm2 = st.columns(2)
        with col_adm1:
            adm_nazim_name = st.text_input(f"Nazim-e-Ala Name for {selected_admin_region}", value=reg_data.get("nazim_name", ""))
            adm_nazim_email = st.text_input(f"Nazim-e-Ala Email for {selected_admin_region}", value=reg_data.get("nazim_email", ""))
        with col_adm2:
            adm_sadr_majlis = st.text_input(f"Sadr Majlis Name for {selected_admin_region}", value=reg_data.get("sadr_majlis", ""))
            adm_qaid_mal = st.text_input(f"Qaid Mal Name for {selected_admin_region}", value=reg_data.get("qaid_mal", ""))
        
        st.markdown("---")
        st.markdown("#### ✉️ Target Email Assignment Matrix")
        
        email_region_select = st.selectbox("Step 1: Pick Target Region for Emails", options=parsed_regions_list, key="email_reg")
        available_majlis_for_email = parsed_majlis_list if email_region_select == selected_admin_region else current_map.get(email_region_select, [])
        
        if available_majlis_for_email:
            email_majlis_select = st.selectbox(f"Step 2: Pick Target Majlis in '{email_region_select}'", options=available_majlis_for_email)
            routing_key = f"{email_region_select}|{email_majlis_select}"
            current_routing_map = st.session_state.admin_lists.get("targeted_emails", {})
            existing_emails = current_routing_map.get(routing_key, [])
            
            majlis_emails_text = st.text_area(f"Notification Distribution Emails for '{email_majlis_select}' (One entry per line)", value="\n".join(existing_emails), height=100)
            parsed_majlis_emails = [line.strip() for line in majlis_emails_text.split("\n") if line.strip()]
        else:
            st.warning("Please declare at least one Majlis entry above to map specific notification routing.")
            routing_key = None
            parsed_majlis_emails = []

        st.markdown("---")
        emails_text = st.text_area("Global Fallback Emails (Used if no specific Majlis email is set)", value="\n".join(st.session_state.admin_lists.get("emails", [])))
        
        if st.button("Save System Configuration Schema Matrix", type="primary"):
            updated_map = dict(current_map)
            updated_map[selected_admin_region] = parsed_majlis_list
            updated_map = {k: v for k, v in updated_map.items() if k in parsed_regions_list}
            for r in parsed_regions_list:
                if r not in updated_map:
                    updated_map[r] = []
            
            # Pack nested details map entries
            updated_details = dict(current_details)
            updated_details[selected_admin_region] = {
                "nazim_name": adm_nazim_name.strip(),
                "nazim_email": adm_nazim_email.strip(),
                "sadr_majlis": adm_sadr_majlis.strip(),
                "qaid_mal": adm_qaid_mal.strip()
            }
            
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
                "regional_details": updated_details
            }
            
            save_admin_settings(updated_config)
            st.session_state.admin_lists = updated_config
            st.success("Global application schema matrix and routing pipelines successfully written!")
            st.rerun()
            
    elif pwd_input != "":
        st.error("Access Refused: Secure validation key does not match records.")
    # =========================================================================
# BLOCK 4 OF 4: INTERACTIVE USER Portal & PROCESS SUBMISSION PIPELINE
# =========================================================================

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
        total_receipts_input = st.number_input("Total Number of Receipts", min_value=1, max_value=50, value=1, step=1)
        
        st.markdown("**🏦 Direct Reimbursement Bank Segment**")
        bsb = st.text_input("Bank BSB Code (e.g., 000-000)", max_chars=7)
        acc_num = st.text_input("Account Number")

    st.markdown("---")
    st.subheader("📎 Individual Itemized Receipt Image Ingestion Block")
    
    receipt_records = []
    raw_files_tracker = []
    
    for i in range(int(total_receipts_input)):
        st.markdown(f"#### 📄 Receipt Section Item #{i+1}")
        
        # Explicit width scaling arrays to allocate screen real estate across elements perfectly
        r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([2.5, 2.5, 2, 1.5, 1.5])

        with r_col1:
            uploaded_file = st.file_uploader(
                f"Upload Image Asset for Receipt #{i+1}",
                type=["png", "jpg", "jpeg"], key=f"file_{i}"
            )
            raw_files_tracker.append(uploaded_file)

        if uploaded_file is not None:
            sig = f"{uploaded_file.name}_{uploaded_file.size}"
            if st.session_state.get(f"sig_{i}") != sig:
                with st.spinner(f"Scanning document #{i+1}..."):
                    d, t, g, dt, raw = parse_receipt_image(uploaded_file)
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

    # Real-time state aggregation loops reading live calculated user edits instantly
    calculated_total_claim = sum(r["amount"] for r in receipt_records)
    calculated_total_gst = sum(r["gst"] for r in receipt_records)

    st.markdown("---")
    st.markdown("### 📊 Dynamic Live Aggregated Verification Totals")
    
    sum_col1, sum_col2 = st.columns(2)
    with sum_col1:
        st.metric(label="Total Aggregated Claim Amount (Calculated)", value=f"${calculated_total_claim:,.2f}")
    with sum_col2:
        st.metric(label="Total Aggregated GST Amount (Calculated)", value=f"${calculated_total_gst:,.2f}")

    st.markdown("---")

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
            
            # Step 1: Compile primary formal ReportLab voucher sheet structure
            generate_official_voucher_pdf(output_filename, payload, receipt_records)
            
            # Step 2: Extract uploaded images and stitch them as consecutive backend attachment pages
            with st.spinner("Stitching image assets into multi-page formal PDF binder format..."):
                stitch_receipt_images_to_pdf(output_filename, raw_files_tracker)
                
            st.success("🎉 Comprehensive Claim Payment Voucher Pack compiled successfully with attached images!")
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
