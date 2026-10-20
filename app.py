import streamlit as st
import re
import json
import os
from PIL import Image
import easyocr
import numpy as np
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

CONFIG_FILE = "admin_config.json"
DEFAULT_CONFIG = {
    "regions": ["National", "Region North", "Region South", "Region East", "Region West"],
    "majlis": ["Head Office", "Finance Dept", "Youth Majlis", "Local Chapter A"],
    "emails": ["finance@example.com", "approvals@example.com"]
}

# -------------------------------------------------------------------------
# PERSISTENT STORAGE MANAGEMENT FOR FREE STREAMLIT TIER
# -------------------------------------------------------------------------
def load_admin_settings():
    """Loads admin configurations from a local JSON file to survive app sleeping."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
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

# Load OCR reader with gpu=False to prevent cloud environment driver crashes
@st.cache_resource
def load_ocr_reader():
    return easyocr.Reader(['en'], gpu=False)

try:
    reader = load_ocr_reader()
except Exception as e:
    reader = None

# -------------------------------------------------------------------------
# OCR PARSER & PDF GENERATOR
# -------------------------------------------------------------------------
def parse_receipt_image(uploaded_file):
    """Scans receipt content with safe resource constraints for free servers."""
    if reader is None:
        return "OCR Engine Initializing (Check packages.txt)", 0.0, 0.0
    try:
        image = Image.open(uploaded_file)
        # Downscale image if it is too large to protect the 1GB RAM ceiling
        image.thumbnail((800, 800))
        img_np = np.array(image)
        
        # Run OCR extraction
        result = reader.readtext(img_np, detail=0)
        full_text = " ".join(result).lower()
        
        # 1. Clean the text to make parsing reliable (remove spaces between numbers/dots)
        cleaned_text = re.sub(r'(?<=\d)\s+(?=\d)', '', full_text)
        
        # 2. Extract anything that looks like an amount (e.g., 12.50, 105.00, 45.9)
        found_amounts = []
        raw_matches = re.findall(r'\d+[\.,]\d{2}|\d+[\.,]\d{1}', cleaned_text)
        
        for match in raw_matches:
            try:
                # Standardize commas to dots for float conversion
                clean_num = match.replace(',', '.')
                found_amounts.append(float(clean_num))
            except ValueError:
                continue
                
        # The largest value printed on a receipt is almost always the Total
        total_amount = max(found_amounts) if found_amounts else 0.0
        
        # 3. Handle GST Extraction (Standard 10% Australian GST)
        gst_amount = 0.0
        if "gst" in cleaned_text or "tax" in cleaned_text:
            # Check if a specific GST amount is printed nearby
            gst_matches = re.findall(r'(?:gst|tax)\s*(?:\$?)\s*(\d+[\.,]\d{2})', cleaned_text)
            if gst_matches:
                gst_amount = float(gst_matches[0].replace(',', '.'))
            else:
                # If "GST" is found but no specific number, estimate it from the total (Total / 11)
                gst_amount = round(total_amount / 11, 2)
        
        # 4. Extract Category Descriptions
        keywords = []
        possible_items = ['uber', 'cafe', 'officeworks', 'bunnings', 'parking', 'fuel', 'stationery', 'food', 'meals', 'woolworths', 'coles']
        for item in possible_items:
            if item in cleaned_text:
                keywords.append(item.title())
        
        description = ", ".join(keywords) if keywords else "General Expense Purchases"
        return description, total_amount, gst_amount
    except Exception as e:
        return "Scan read timeout", 0.0, 0.0

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

if app_mode == "Admin Panel Configuration":
    st.title("⚙️ Admin Control Panel")
    st.info("Settings adjusted here are saved to disk to stay persistent across free server cycles.")
    
    regions_text = st.text_area("Regions (One entry per line)", value="\n".join(st.session_state.admin_lists["regions"]))
    majlis_text = st.text_area("Majlis / Offices (One entry per line)", value="\n".join(st.session_state.admin_lists["majlis"]))
    emails_text = st.text_area("Notification Emails (One entry per line)", value="\n".join(st.session_state.admin_lists["emails"]))
    
    if st.button("Save System Lists Updates", type="primary"):
        updated_config = {
            "regions": [line.strip() for line in regions_text.split("\n") if line.strip()],
            "majlis": [line.strip() for line in majlis_text.split("\n") if line.strip()],
            "emails": [line.strip() for line in emails_text.split("\n") if line.strip()]
        }
        save_admin_settings(updated_config)
        st.session_state.admin_lists = updated_config
        st.success("Admin updates written permanently to cloud storage successfully!")

else:
    st.title("📋 Online Expense Claim Submission System")
    
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full Employee / Claimant Name")
        region = st.selectbox("Select Region / National Location", options=st.session_state.admin_lists["regions"])
        majlis = st.selectbox("Select Majlis / Office Destination", options=st.session_state.admin_lists["majlis"])
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
            uploaded_file = st.file_uploader(f"Upload Image Asset for Receipt #{i+1}", type=["png", "jpg", "jpeg"], key=f"file_{i}")
            initial_desc, initial_total, initial_gst = "No file parsed", 0.0, 0.0
if uploaded_file is not None:
with st.spinner(f"Scanning document #{i+1}..."):
initial_desc, initial_total, initial_gst = parse_receipt_image(uploaded_file)
with r_col2:
desc_val = st.text_input(f"Item Description #{i+1}", value=initial_desc, key=f"desc_{i}")
with r_col3:
total_val = st.number_input(f"Amount ($) #{i+1}", min_value=0.0, value=float(initial_total), format="%.2f", key=f"amt_{i}")
with r_col4:
gst_val = st.number_input(f"GST ($) #{i+1}", min_value=0.0, value=float(initial_gst), format="%.2f", key=f"gst_{i}")
receipt_records.append({"desc": desc_val, "amount": total_val, "gst": gst_val})
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
