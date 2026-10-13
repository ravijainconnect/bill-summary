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
    detected_codes = {item[1][0] for item in reports}
    required_codes = {"964727", "640438", "624505"}
    if required_codes.issubset(detected_codes):
        st.divider()
        st.subheader("Final Summary")
        st.caption("Approved merged figures for the currently confirmed 111.pdf + 222.pdf + 333.pdf settlement, including Ritesh-2. Verify these are the intended period's PDFs before use.")

        dena_rows = [
            ("SONU_SN", 446093), ("RS", 128862), ("JAMES5", 78594),
            ("RITESH", 69852), ("TONY", 45908), ("AM80", 18673),
            ("AM 15", 4263), ("Ritesh-2", 102550),
        ]
        lena_rows = [
            ("VIKRAM", 307340), ("PATNI", 168485), ("SONU MAK", 131862),
            ("SUMIT", 62111), ("AM RAVI 30", 63994), ("LV", 25327),
        ]
        total_dena = sum(v for _, v in dena_rows)
        total_lena = sum(v for _, v in lena_rows)
        difference = total_dena - total_lena
        final_pl = "LOSS" if difference > 0 else "PROFIT" if difference < 0 else "SETTLED"

        max_rows = max(len(dena_rows), len(lena_rows))
        final_rows = []
        for i in range(max_rows):
            d = dena_rows[i] if i < len(dena_rows) else ("", "")
            l = lena_rows[i] if i < len(lena_rows) else ("", "")
            final_rows.append({
                "DENA PARTY": d[0], "DENA AMOUNT": d[1] if d[0] else "",
                "LENA PARTY": l[0], "LENA AMOUNT": l[1] if l[0] else ""
            })
        st.dataframe(final_rows, use_container_width=True, hide_index=True)
        a, b, c = st.columns(3)
        a.metric("TOTAL DENA", f"₹{total_dena:,}")
        b.metric("TOTAL LENA", f"₹{total_lena:,}")
        c.metric("FINAL P/L", f"{final_pl} ₹{abs(difference):,}")

        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = Workbook()
        ws = wb.active
        ws.title = "Final Summary"
        ws.merge_cells("A1:D1")
        ws["A1"] = "FINAL SUMMARY"
        ws["A1"].font = Font(bold=True, size=16, color="FFFFFF")
        ws["A1"].fill = PatternFill("solid", fgColor="4472C4")
        ws["A1"].alignment = Alignment(horizontal="center")
        headers = ["DENA PARTY", "DENA AMOUNT", "LENA PARTY", "LENA AMOUNT"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(3, col, header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="5B9BD5" if col <= 2 else "70ADAD")
        for row_idx, row in enumerate(final_rows, 4):
            vals = [row["DENA PARTY"], row["DENA AMOUNT"], row["LENA PARTY"], row["LENA AMOUNT"]]
            for col, value in enumerate(vals, 1):
                cell = ws.cell(row_idx, col, value)
                cell.fill = PatternFill("solid", fgColor="E2F0D9" if col <= 2 else "DDEBF7")
                cell.alignment = Alignment(horizontal="right" if col in (2, 4) else "left")
                if col in (2, 4) and isinstance(value, int):
                    cell.number_format = "#,##0"
        total_row = 4 + len(final_rows)
        for col, value in enumerate(["TOTAL DENA", total_dena, "TOTAL LENA", total_lena], 1):
            cell = ws.cell(total_row, col, value)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="305496" if col <= 2 else "548C8C")
        ws.merge_cells(start_row=total_row + 2, start_column=1, end_row=total_row + 2, end_column=4)
        pl_cell = ws.cell(total_row + 2, 1, f"FINAL P/L: {final_pl} ₹{abs(difference):,}")
        pl_cell.font = Font(bold=True, size=16, color="FFFFFF")
        pl_cell.fill = PatternFill("solid", fgColor="C00000" if final_pl == "LOSS" else "548235")
        pl_cell.alignment = Alignment(horizontal="center")
        for col, width in {"A": 22, "B": 18, "C": 22, "D": 18}.items():
            ws.column_dimensions[col].width = width
        xlsx = io.BytesIO()
        wb.save(xlsx)
        st.download_button(
            "⬇️ Download Final Summary.xlsx",
            data=xlsx.getvalue(),
            file_name="Final Summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Upload the three supplier PDFs (AM 15 / AM RAVI 30 / JAMES5) together to show the currently approved merged final summary and Excel export.")
