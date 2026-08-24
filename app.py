import json

import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build


st.set_page_config(
    page_title="Plumbing Price Assistant",
    page_icon="🔧",
    layout="wide"
)

st.title("🔧 Plumbing Price Assistant")
st.write("Testing Google Drive and Google Sheets connections.")

google_info = json.loads(
    st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
)

credentials = service_account.Credentials.from_service_account_info(
    google_info,
    scopes=[
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets"
    ]
)

drive_service = build(
    "drive",
    "v3",
    credentials=credentials
)

sheets_service = build(
    "sheets",
    "v4",
    credentials=credentials
)

folder_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
sheet_id = st.secrets["GOOGLE_SHEET_ID"]


if st.button("Find Invoice PDFs"):
    with st.spinner("Checking Google Drive..."):

        query = (
            f"'{folder_id}' in parents "
            "and trashed = false "
            "and mimeType = 'application/pdf'"
        )

        results = drive_service.files().list(
            q=query,
            fields="files(id, name, modifiedTime)",
            pageSize=1000
        ).execute()

        files = results.get("files", [])

    st.success(f"Connected! Found {len(files)} PDF invoice(s).")

    for file in files:
        st.write("📄", file["name"])


st.divider()

if st.button("Check Spreadsheet Access"):
    try:
        sheet_file = drive_service.files().get(
            fileId=sheet_id,
            fields="id,name,mimeType"
        ).execute()

        st.success(
            f"Spreadsheet found: {sheet_file['name']}"
        )

    except Exception as e:
        st.error("The service account cannot see this spreadsheet.")
        st.write(str(e))
