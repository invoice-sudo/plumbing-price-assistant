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
st.write("Testing connection to your plumbing invoice Google Drive folder.")

# Read Google credentials from Streamlit Secrets
google_info = json.loads(
    st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
)

credentials = service_account.Credentials.from_service_account_info(
    google_info,
    scopes=["https://www.googleapis.com/auth/drive.readonly"]
)

drive_service = build(
    "drive",
    "v3",
    credentials=credentials
)

folder_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]


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

    if files:

        st.success(
            f"Connected! Found {len(files)} PDF invoice(s)."
        )

        for file in files:
            st.write("📄", file["name"])

    else:

        st.warning(
            "Connection worked, but no PDFs were found in the folder."
        )
