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
    # --- FIXED SUBMIT ROUTING REGION ---
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

    # --- FIXED SUBMIT ROUTING REGION ---
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

