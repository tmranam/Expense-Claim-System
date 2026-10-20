import streamlit as st
import re
import json
import os
import shutil
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
    "admin_password": "AdminPassword123"
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
                # This guarantees that if you have an old file on disk, it won't trigger a KeyError
                if "regions" not in data or not data["regions"]:
                    data["regions"] = DEFAULT_CONFIG["regions"]
                if "regional_majlis" not in data or not data["regional_majlis"]:
                    data["regional_majlis"] = DEFAULT_CONFIG["regional_majlis"]
                if "admin_password" not in data:
                    data["admin_password"] = DEFAULT_CONFIG["admin_password"]
                if "emails" not in data:
                    data["emails"] = DEFAULT_CONFIG["emails"]
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
                gst_amount = float(gst_numbers[0].replace(',', '.'))
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
    
    if pwd_input == st.session_state.admin_lists.get("admin_password", "AdminPassword123"):
        st.success("Access Verified. Secure Configuration Screen Unlocked.")
        
        new_pwd = st.text_input("Modify System Admin Password Key", value=st.session_state.admin_lists.get("admin_password", "AdminPassword123"))
        regions_text = st.text_area("Regions Master List Configuration (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
        
        st.markdown("#### 🔄 Relational Majlis Mapping Schema Matrix (JSON Format)")
        current_map_json = json.dumps(st.session_state.admin_lists["regional_majlis"], indent=4)
        majlis_json_text = st.text_area("Majlis Relational JSON Schema Payload", value=current_map_json, height=220)
        
        emails_text = st.text_area("Notification Distribution Target Emails (One entry per line)", value="\n".join(st.session_state.admin_lists["emails"]))
        
        if st.button("Save System Configuration Schema Matrix", type="primary"):
            try:
                parsed_majlis_map = json.loads(majlis_json_text)
                parsed_regions_list = [line.strip() for line in regions_text.split("\n") if line.strip()]
                
                updated_config = {
                    "regions": parsed_regions_list,
                    "regional_majlis": parsed_majlis_map,
                    "emails": [line.strip() for line in emails_text.split("\n") if line.strip()],
                    "admin_password": new_pwd.strip()
                }
                
                save_admin_settings(updated_config)
                st.session_state.admin_lists = updated_config
                st.success("Global application schema matrix written to storage successfully!")
            except json.JSONDecodeError:
                st.error("JSON Evaluation Error: Failed to compile the Majlis schema. Check key quote marks and trailing commas.")
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
        
        configured_regions = st.session_state.admin_lists["regions"]
        region = st.selectbox("Select Region / National Location", options=configured_regions)
        
        majlis_lookup_map = st.session_state.admin_lists["regional_majlis"]
        available_majlis_options = majlis_lookup_map.get(region, ["No entries declared under this region boundary"])
        
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
        
        # --- FIXED BLOCK PARAMETERS BELOW ---
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
        if not name or not bsb or not acc_num:
            st.error("Submission Denied: Profile details, BSB, and Account Numbers must be filled out.")
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
            st.info(f"📧 Simulation Email Sent: PDF dispatched to targets: {', '.join(st.session_state.admin_lists['emails'])}")
            
            pdf_bytes = open(output_filename, "rb").read()
            st.download_button(
                label="📥 Download Generated Summary Receipt PDF File",
                data=pdf_bytes,
                file_name=output_filename,
                mime="application/pdf"
            )

