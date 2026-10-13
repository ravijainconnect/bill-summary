import streamlit as st
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

st.set_page_config(page_title="HISAB — New App", page_icon="📊", layout="wide")

# Fresh app scaffold: no dependency on the old HISAB code.
# Settlement rows are explicit and editable; do not silently infer unknown rules.
DEFAULT_DENA = [
    ("SONU_SN", 446093),
    ("RS", 128862),
    ("JAMES5", 78594),
    ("RITESH", 69852),
    ("TONY", 45908),
    ("AM80", 18673),
    ("AM 15", 4263),
    ("Ritesh-2", 102550),
]
DEFAULT_LENA = [
    ("VIKRAM", 307340),
    ("PATNI", 168485),
    ("SONU MAK", 131862),
    ("SUMIT", 62111),
    ("AM RAVI 30", 63994),
    ("LV", 25327),
]

def rupees(n):
    return f"₹{int(n):,}"

st.title("📊 HISAB — New App")
st.caption("Fresh build • Independent of the old app • Verify every calculation before settlement")

with st.sidebar:
    st.header("Module")
    module = st.selectbox(
        "Choose module",
        ["Merge module ver 1", "Module 1 — VER 1", "Module 2 — VER 1",
         "Module 3 — VER 1", "New Sharing Person Ver 1"]
    )
    st.info("Locked rules should be changed only after explicit confirmation.")

st.subheader(module)
st.write("Current merged summary is prefilled from the figures you confirmed. Edit amounts only if you intentionally revise the settlement.")

col1, col2 = st.columns(2)
with col1:
    st.markdown("### 🟦 DENA — Amounts to receive")
    dena_rows = []
    for i, (name, amount) in enumerate(DEFAULT_DENA):
        a, b = st.columns([2, 1])
        with a:
            nm = st.text_input("Party", value=name, key=f"d_name_{i}", label_visibility="collapsed")
        with b:
            val = st.number_input("Amount", min_value=0, value=int(amount), step=1, key=f"d_amt_{i}", label_visibility="collapsed")
        dena_rows.append((nm.strip(), int(val)))
    with st.expander("➕ Add DENA row"):
        new_d_name = st.text_input("New DENA party", key="new_d_name")
        new_d_amt = st.number_input("New DENA amount (₹)", min_value=0, value=0, step=1, key="new_d_amt")
        if st.button("Add DENA row") and new_d_name.strip():
            st.session_state.setdefault("extra_dena", []).append((new_d_name.strip(), int(new_d_amt)))
            st.rerun()
    for j, (nm, amt) in enumerate(st.session_state.get("extra_dena", [])):
        dena_rows.append((nm, amt))

with col2:
    st.markdown("### 🟩 LENA — Amounts to pay")
    lena_rows = []
    for i, (name, amount) in enumerate(DEFAULT_LENA):
        a, b = st.columns([2, 1])
        with a:
            nm = st.text_input("Party", value=name, key=f"l_name_{i}", label_visibility="collapsed")
        with b:
            val = st.number_input("Amount", min_value=0, value=int(amount), step=1, key=f"l_amt_{i}", label_visibility="collapsed")
        lena_rows.append((nm.strip(), int(val)))
    with st.expander("➕ Add LENA row"):
        new_l_name = st.text_input("New LENA party", key="new_l_name")
        new_l_amt = st.number_input("New LENA amount (₹)", min_value=0, value=0, step=1, key="new_l_amt")
        if st.button("Add LENA row") and new_l_name.strip():
            st.session_state.setdefault("extra_lena", []).append((new_l_name.strip(), int(new_l_amt)))
            st.rerun()
    for j, (nm, amt) in enumerate(st.session_state.get("extra_lena", [])):
        lena_rows.append((nm, amt))

total_dena = sum(v for _, v in dena_rows if _)
total_lena = sum(v for _, v in lena_rows if _)
difference = total_dena - total_lena

st.divider()
m1, m2, m3 = st.columns(3)
m1.metric("TOTAL DENA", rupees(total_dena))
m2.metric("TOTAL LENA", rupees(total_lena))
# User's established convention: DENA excess means LOSS.
pl_label = "LOSS" if difference > 0 else "PROFIT" if difference < 0 else "SETTLED"
m3.metric("FINAL P/L", f"{pl_label} {rupees(abs(difference))}")

st.markdown("### Final summary")
max_rows = max(len(dena_rows), len(lena_rows))
display_rows = []
for i in range(max_rows):
    d = dena_rows[i] if i < len(dena_rows) else ("", "")
    l = lena_rows[i] if i < len(lena_rows) else ("", "")
    display_rows.append({
        "DENA PARTY": d[0], "DENA AMOUNT": rupees(d[1]) if d[0] else "",
        "LENA PARTY": l[0], "LENA AMOUNT": rupees(l[1]) if l[0] else ""
    })
st.dataframe(display_rows, use_container_width=True, hide_index=True)

# Export a colorful Excel with simple file/sheet names.
wb = Workbook()
ws = wb.active
ws.title = "Final Summary"
ws.merge_cells("A1:D1")
ws["A1"] = "FINAL SUMMARY"
ws["A1"].font = Font(bold=True, size=16, color="FFFFFF")
ws["A1"].fill = PatternFill("solid", fgColor="4472C4")
ws["A1"].alignment = Alignment(horizontal="center")
for c, title in enumerate(["DENA PARTY", "DENA AMOUNT", "LENA PARTY", "LENA AMOUNT"], 1):
    cell = ws.cell(3, c, title)
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="5B9BD5" if c <= 2 else "70ADAD")
for i, row in enumerate(display_rows, 4):
    for c, key in enumerate(["DENA PARTY", "DENA AMOUNT", "LENA PARTY", "LENA AMOUNT"], 1):
        cell = ws.cell(i, c, row[key])
        cell.fill = PatternFill("solid", fgColor="E2F0D9" if c <= 2 else "DDEBF7")
        cell.alignment = Alignment(horizontal="right" if c in (2,4) else "left")
tr = 4 + len(display_rows)
for c, val in enumerate(["TOTAL DENA", total_dena, "TOTAL LENA", total_lena], 1):
    cell = ws.cell(tr, c, val)
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="305496" if c <= 2 else "548C8C")
ws.merge_cells(start_row=tr+2, start_column=1, end_row=tr+2, end_column=4)
ws.cell(tr+2, 1, f"FINAL P/L: {pl_label} {rupees(abs(difference))}")
ws.cell(tr+2, 1).font = Font(bold=True, size=16, color="FFFFFF")
ws.cell(tr+2, 1).fill = PatternFill("solid", fgColor="C00000" if pl_label == "LOSS" else "548235")
ws.cell(tr+2, 1).alignment = Alignment(horizontal="center")
for col, width in {"A":22, "B":18, "C":22, "D":18}.items():
    ws.column_dimensions[col].width = width
buf = BytesIO()
wb.save(buf)
st.download_button("⬇️ Download Final Summary.xlsx", data=buf.getvalue(),
                   file_name="Final Summary.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.warning("This is the fresh summary-app starter. Automated PDF parsing and full client-level locked sharing rules are not yet implemented; upload-based results must not be used until those rules are added and tested against the original PDFs.")
