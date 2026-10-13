import io
import re
from decimal import Decimal, ROUND_HALF_UP
import streamlit as st
import pdfplumber

st.set_page_config(page_title="HISAB — Fresh App", page_icon="📊", layout="wide")

st.title("📊 HISAB")
st.caption("Fresh PDF-based workspace — no old locked-rules panel, no “Start New Week” workflow.")

st.markdown("Upload the GMGlobal PDF reports for the settlement you want to calculate.")
files = st.file_uploader(
    "Upload PDF files",
    type=["pdf"],
    accept_multiple_files=True,
    help="Select one or more supplier PDF reports."
)

def parse_amount(value):
    value = value.strip().replace(",", "")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    return Decimal(value)

def extract_report(uploaded):
    data = uploaded.getvalue()
    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
    text = "\n".join(text_parts)

    supplier_match = re.search(r'^\s*\((\d{6})\)\s*(.+?)\s*$', text, re.M)
    supplier_code = supplier_match.group(1) if supplier_match else "Unknown"
    supplier_name = supplier_match.group(2).strip() if supplier_match else "Supplier not detected"

    client_pattern = re.compile(
        r'^\s*\((\d{6})\)\s*(.+?)\s*$.*?'
        r'Total Brokerage:\s*([\-\d,]+\.\d+)\s+'
        r'Broker Brokerage:\s*([\-\d,]+\.\d+)\s+'
        r'Final Bill Amt\s*:\s*([\-\d,]+\.\d+)',
        re.M | re.S
    )
    clients = []
    for m in client_pattern.finditer(text):
        code, name, brokerage, broker_brokerage, final_bill = m.groups()
        if code == supplier_code and name.strip() == supplier_name:
            continue
        clients.append({
            "Client Code": code,
            "Client": name.strip(),
            "Total Brokerage": parse_amount(brokerage),
            "Broker Brokerage": parse_amount(broker_brokerage),
            "Final Bill": parse_amount(final_bill),
        })

    total_match = re.search(
        r'Total Gross:\s*([\-\d,]+\.\d+)\s+'
        r'Total Brokerage:\s*([\-\d,]+\.\d+)\s+'
        r'Broker Brokerage:\s*([\-\d,]+\.\d+)\s+'
        r'Final Bill Amt\s*:\s*([\-\d,]+\.\d+)',
        text, re.S
    )
    totals = None
    if total_match:
        totals = {
            "Total Gross": parse_amount(total_match.group(1)),
            "Total Brokerage": parse_amount(total_match.group(2)),
            "Broker Brokerage": parse_amount(total_match.group(3)),
            "Final Bill": parse_amount(total_match.group(4)),
        }
    return supplier_code, supplier_name, clients, totals, text

def fmt(value):
    return f"₹{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"

if not files:
    st.info("👆 Start by uploading your PDF report(s). No settlement figures are prefilled.")
else:
    st.write(f"**Files uploaded:** {len(files)}")
    reports = []
    errors = []
    for uploaded in files:
        try:
            reports.append((uploaded.name, extract_report(uploaded)))
        except Exception as exc:
            errors.append((uploaded.name, str(exc)))

    if errors:
        st.error("Some PDFs could not be read.")
        for filename, error in errors:
            st.write(f"- **{filename}**: {error}")

    if reports:
        summary_rows = []
        for filename, (code, name, clients, totals, raw_text) in reports:
            with st.expander(f"{filename} — {name} ({code})", expanded=True):
                st.write(f"**Supplier:** {name} | **Code:** {code}")
                if totals:
                    cols = st.columns(4)
                    for col, label, key in zip(
                        cols,
                        ["Total Gross", "Total Brokerage", "Broker Brokerage", "Final Bill"],
                        ["Total Gross", "Total Brokerage", "Broker Brokerage", "Final Bill"]
                    ):
                        col.metric(label, fmt(totals[key]))
                    summary_rows.append({
                        "PDF": filename,
                        "Supplier": name,
                        "Supplier Code": code,
                        "Client Rows": len(clients),
                        "Supplier Final Bill": fmt(totals["Final Bill"]),
                    })
                else:
                    st.warning("Supplier totals were not detected in the expected GMGlobal format.")
                st.write(f"**Client rows detected:** {len(clients)}")
                if clients:
                    st.dataframe([
                        {
                            "Client Code": c["Client Code"],
                            "Client": c["Client"],
                            "Total Brokerage": fmt(c["Total Brokerage"]),
                            "Broker Brokerage": fmt(c["Broker Brokerage"]),
                            "Final Bill": fmt(c["Final Bill"]),
                        } for c in clients
                    ], use_container_width=True, hide_index=True)
                else:
                    st.warning("No client rows detected. Check whether this is a text-based GMGlobal PDF.")

        if summary_rows:
            st.subheader("PDF extraction summary")
            st.dataframe(summary_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Settlement calculation")
    st.warning(
        "This fresh build currently uploads and extracts PDF supplier/client figures only. "
        "The merged Module 1 + 2 + 3 sharing formulas and final DENA/LENA/P&L are not yet enabled here. "
        "Do not use extracted totals alone as a final settlement."
    )
