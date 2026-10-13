import io
import json
import re
import base64
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import requests
import streamlit as st
import pdfplumber
from openpyxl import Workbook

st.set_page_config(page_title="HISAB Version 2.0", page_icon="📊", layout="wide")

OWNER = "ravijainconnect"
REPO = "bill-summary"
BRANCH = "version-2.0"
RULES_PATH = "saved_rules.json"

st.title("📊 HISAB")
st.caption("Version 2.0 — separate development branch. Version 1.0 / main is not modified.")

# Version selector defaults to latest. V1 is intentionally not emulated in this V2 branch.
version = st.radio("Select App Version", ["2.0 (Latest)", "1.0 (Existing)"], index=0, horizontal=True)
if version.startswith("1.0"):
    st.warning("Version 1.0 is a separate existing deployment. Open that deployment to use it; this Version 2.0 branch does not overwrite it.")
    st.stop()

st.subheader("1. Upload settlement PDFs")
files = st.file_uploader("Upload PDF files", type=["pdf"], accept_multiple_files=True)

def gh_headers():
    token = st.secrets.get("GITHUB_TOKEN", "")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

def load_rules():
    headers = gh_headers()
    if not headers:
        return [], None, "GitHub persistent storage is not configured. Add GITHUB_TOKEN to Streamlit secrets."
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{RULES_PATH}?ref={BRANCH}"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 404:
            return [], None, None
        response.raise_for_status()
        payload = response.json()
        raw = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(raw), payload["sha"], None
    except Exception as exc:
        return [], None, f"Could not load saved rules: {exc}"

def save_rules(rules, sha):
    headers = gh_headers()
    if not headers:
        return False, "Permanent saving is unavailable until GITHUB_TOKEN is configured in Streamlit secrets."
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{RULES_PATH}"
    body = {
        "message": "Save HISAB Version 2.0 client sharing rule",
        "content": base64.b64encode(json.dumps(rules, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        return True, "Rule saved permanently to the Version 2.0 branch."
    except Exception as exc:
        return False, f"Save failed: {exc}"

rules, rules_sha, rules_error = load_rules()
if rules_error:
    st.warning(rules_error)
else:
    st.success(f"Saved rules loaded: {len(rules)}")

detected_names = []
if files:
    for uploaded in files:
        try:
            with pdfplumber.open(io.BytesIO(uploaded.getvalue())) as pdf:
                txt = "\n".join(p.extract_text(x_tolerance=2, y_tolerance=3) or "" for p in pdf.pages)
            for code, name in re.findall(r"\((\d{6})\)\s*([^\n]+)", txt):
                candidate = name.strip()
                if candidate and candidate not in detected_names:
                    detected_names.append(candidate)
        except Exception:
            pass
    st.caption(f"PDF files selected: {len(files)}")
    if detected_names:
        st.write("Names detected from uploaded PDFs (review before creating a rule):")
        st.write(", ".join(detected_names))
    else:
        st.info("No client names detected in the uploaded PDFs yet. PDF parser needs validation against the actual report format.")

st.divider()
st.subheader("New Client Module")
st.caption("Unknown client workflow: review the client, define the rule, save it, then continue calculation. Saving requires configured GitHub credentials.")

# Point 1: auto-detected and locked
detected_client = detected_names[0] if detected_names else ""
st.markdown("**1. FOUND NEW CLIENT**")
st.text_input("Auto-detected client (locked)", value=detected_client or "Waiting for PDF detection", disabled=True)

# All known clients are dynamically sourced from saved rules; no hard-coded full list.
client_names = sorted({str(r.get("first_person", "")) for r in rules if r.get("first_person")} |
                      {str(r.get("second_person", "")) for r in rules if r.get("second_person")})
st.markdown("**2. 1ST LENDEN PERSON**")
client_choice = st.selectbox("Select from saved client/person names", [""] + client_names + ["＋ ADD NEW"], key="first_person_choice")
first_person = st.text_input("Enter new first lenden person name", key="first_person_new") if client_choice == "＋ ADD NEW" else client_choice

st.markdown("**3. 1ST LENDEN PERSON SHARING**")
share1 = st.number_input("Percentage (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="share1")
st.markdown("**4. 1ST LENDEN PERSON SHARING BASIS**")
basis1 = st.radio("First person basis", ["Final Bill", "Brokerage", "Final Bill + Return Brokerage"], key="basis1", horizontal=False)
st.markdown("**5. 1ST LENDEN PERSON BROKER BROKERAGE SHARING**")
broker1 = st.number_input("Percentage (%) ", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="broker1")
st.markdown("**6. 1ST LENDEN PERSON TOTAL BROKERAGE SHARING**")
total1 = st.number_input("Total brokerage sharing (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="total1")

st.markdown("**7. 2ND LENDEN PERSON**")
second = st.radio("Does a second lenden person exist?", ["NO", "YES"], index=0, horizontal=True, key="second")
if second == "YES":
    st.markdown("**8. 2ND LENDEN PERSON SHARING BASIS**")
    basis2 = st.radio("Second person basis", ["Final Bill", "Brokerage", "Final Bill + Return Brokerage"], key="basis2")
    st.markdown("**9. 2ND LENDEN PERSON SHARING**")
    share2 = st.number_input("Second person sharing (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="share2")
    st.markdown("**10. 2ND LENDEN PERSON BROKER BROKERAGE SHARING**")
    broker2 = st.number_input("Second person broker brokerage sharing (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="broker2")
    st.markdown("**11. 2ND LENDEN PERSON TOTAL BROKERAGE SHARING**")
    total2 = st.number_input("Second person total brokerage sharing (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.2f", key="total2")
else:
    basis2, share2, broker2, total2 = "", 0.0, 0.0, 0.0

st.markdown("**Rule type**")
rule_type = st.radio("Sharing behavior", ["Additional sharing", "Replacement sharing"], horizontal=False)
if st.button("Save Rule Permanently", type="primary", use_container_width=True):
    if not first_person:
        st.error("Select or enter the 1st Lenden Person before saving.")
    elif not detected_client:
        st.error("Upload a PDF and ensure the new client is detected before saving this rule.")
    else:
        new_rule = {
            "detected_client": detected_client,
            "first_person": first_person,
            "first_sharing_pct": share1,
            "first_basis": basis1,
            "first_broker_brokerage_sharing_pct": broker1,
            "first_total_brokerage_sharing_pct": total1,
            "has_second_person": second,
            "second_basis": basis2,
            "second_sharing_pct": share2,
            "second_broker_brokerage_sharing_pct": broker2,
            "second_total_brokerage_sharing_pct": total2,
            "rule_type": rule_type,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        rules.append(new_rule)
        ok, message = save_rules(rules, rules_sha)
        if ok:
            st.success(message)
            st.rerun()
        else:
            rules.pop()
            st.error(message)

st.divider()
st.subheader("Saved Rules")
if rules:
    st.dataframe(rules, use_container_width=True, hide_index=True)
    st.download_button("Download saved rules backup (JSON)", data=json.dumps(rules, ensure_ascii=False, indent=2), file_name="hisab_v2_saved_rules_backup.json", mime="application/json")
else:
    st.info("No rules saved yet.")

st.divider()
st.subheader("Settlement Summary")
st.info("The final merged calculation is not enabled in this build yet. PDF extraction and all 11 rule fields need validation against your real PDFs and saved Module 1–4 / Merge rules before the summary can safely calculate. No static figures are presented as live calculations.")
