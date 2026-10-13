# HISAB — New App

A fresh Streamlit app scaffold, independent from the old HISAB repository.

## Run locally
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy
Create a **new private GitHub repository** (suggested name: `HISAB-NEW-APP`), upload `streamlit_app.py` and `requirements.txt`, then create a new app on Streamlit Community Cloud using:
- Repository: your new repository
- Branch: `main`
- Main file: `streamlit_app.py`

## Important
This starter currently provides a colourful editable settlement summary with the confirmed merged figures and the user's P/L convention (DENA excess = LOSS). It does **not** yet implement automated PDF parsing or all locked client-level sharing rules. Do not use it for automated settlement calculations until those rules have been implemented and independently tested against the original PDFs.
