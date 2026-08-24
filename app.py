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

if st.button("Test Google Sheet Connection"):
    test_row = [
        [
            "TEST",
            "Test connection",
            "Not a real invoice"
        ]
    ]

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="Invoices!A:C",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": test_row}
    ).execute()

    st.success(
        "Google Sheet connection works! A test row was added to the Invoices tab."
    )
