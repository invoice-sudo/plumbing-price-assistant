import io
import json

import pandas as pd
import pdfplumber
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from openai import OpenAI


st.set_page_config(
    page_title="Plumbing Price Assistant",
    page_icon="🔧",
    layout="wide",
)

st.title("🔧 Plumbing Price Assistant")
st.write("Analyze new plumbing invoices from Google Drive and save the results.")

# -----------------------------
# CONNECTIONS
# -----------------------------

google_info = json.loads(
    st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
)

credentials = service_account.Credentials.from_service_account_info(
    google_info,
    scopes=[
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
    ],
)

drive_service = build(
    "drive",
    "v3",
    credentials=credentials,
)

sheets_service = build(
    "sheets",
    "v4",
    credentials=credentials,
)

openai_client = OpenAI(
    api_key=st.secrets["OPENAI_API_KEY"]
)

folder_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
sheet_id = st.secrets["GOOGLE_SHEET_ID"]


# -----------------------------
# HELPERS
# -----------------------------

def get_drive_pdfs():
    query = (
        f"'{folder_id}' in parents "
        "and trashed = false "
        "and mimeType = 'application/pdf'"
    )

    results = drive_service.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        pageSize=1000,
    ).execute()

    return results.get("files", [])


def get_processed_file_ids():
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="'Invoices'!A:A",
        ).execute()

        rows = result.get("values", [])

        if not rows:
            return set()

        return {
            row[0]
            for row in rows[1:]
            if row
        }

    except Exception:
        return set()


def download_pdf(file_id):
    request = drive_service.files().get_media(
        fileId=file_id
    )

    file_buffer = io.BytesIO()

    downloader = MediaIoBaseDownload(
        file_buffer,
        request,
    )

    done = False

    while not done:
        _, done = downloader.next_chunk()

    file_buffer.seek(0)

    return file_buffer


def extract_pdf_text(file_buffer):
    text_parts = []

    with pdfplumber.open(file_buffer) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()

            if page_text:
                text_parts.append(page_text)

    return "\n".join(text_parts)


def analyze_invoice(invoice_text, filename):
    prompt = f"""
You are a controlled plumbing invoice extraction system.

STRICT RULES:
- Only use information actually shown on the invoice.
- Never invent products, prices, quantities, vendors, invoice numbers, or PO numbers.
- Never guess missing information.
- Ignore tax.
- Ignore subtotal.
- Ignore freight and shipping.
- Ignore invoice totals.
- Extract individual purchased products only.
- Preserve the original product description.
- Use UNIT PRICE whenever clearly available.
- If uncertain, use null.
- Confidence must be between 0 and 1.
- Return valid JSON only.
- Do not include markdown or explanations.

Return exactly:

{{
  "vendor": "vendor name or null",
  "invoice_number": "invoice number or null",
  "po_number": "PO number or null",
  "items": [
    {{
      "description": "original description",
      "quantity": 1,
      "unit_price": 0.00,
      "confidence": 0.95
    }}
  ]
}}

Source filename:
{filename}

Invoice text:
{invoice_text}
"""

    response = openai_client.responses.create(
        model="gpt-5-mini",
        input=prompt,
    )

    output = response.output_text.strip()
    output = output.replace("```json", "")
    output = output.replace("```", "")
    output = output.strip()

    return json.loads(output)


def append_rows(range_name, rows):
    if not rows:
        return

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


# -----------------------------
# FIND NEW INVOICES
# -----------------------------

all_pdfs = get_drive_pdfs()
processed_ids = get_processed_file_ids()

new_pdfs = [
    file
    for file in all_pdfs
    if file["id"] not in processed_ids
]

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "PDFs in Drive",
        len(all_pdfs),
    )

with col2:
    st.metric(
        "Already Processed",
        len(processed_ids),
    )

with col3:
    st.metric(
        "New Invoices",
        len(new_pdfs),
    )


st.divider()

# -----------------------------
# PROCESS BATCH
# -----------------------------

batch_size = min(10, len(new_pdfs))

if new_pdfs:
    st.write(
        f"Ready to process up to {batch_size} new invoice(s)."
    )

    if st.button("Process Next 10 New Invoices"):

        batch = new_pdfs[:10]

        progress = st.progress(0)

        processed_count = 0
        failed_count = 0
        review_count = 0

        results_summary = []

        for index, selected_file in enumerate(batch):

            try:
                with st.spinner(
                    f"Analyzing {selected_file['name']}..."
                ):

                    pdf_buffer = download_pdf(
                        selected_file["id"]
                    )

                    invoice_text = extract_pdf_text(
                        pdf_buffer
                    )

                    if not invoice_text.strip():
                        raise ValueError(
                            "No readable text found in PDF."
                        )

                    data = analyze_invoice(
                        invoice_text,
                        selected_file["name"],
                    )

                    vendor = data.get("vendor")
                    invoice_number = data.get(
                        "invoice_number"
                    )
                    po_number = data.get("po_number")
                    items = data.get("items", [])

                    append_rows(
                        "'Invoices'!A:G",
                        [[
                            selected_file["id"],
                            selected_file["name"],
                            vendor,
                            invoice_number,
                            po_number,
                            "Processed",
                            pd.Timestamp.utcnow().isoformat(),
                        ]],
                    )

                    line_item_rows = []
                    review_rows = []

                    for item in items:
                        description = item.get(
                            "description"
                        )
                        quantity = item.get(
                            "quantity"
                        )
                        unit_price = item.get(
                            "unit_price"
                        )
                        confidence = item.get(
                            "confidence",
                            0,
                        )

                        row = [
                            selected_file["id"],
                            selected_file["name"],
                            vendor,
                            invoice_number,
                            description,
                            quantity,
                            unit_price,
                            confidence,
                        ]

                        if confidence < 0.85:
                            review_rows.append(row)
                        else:
                            line_item_rows.append(row)

                    append_rows(
                        "'Line Items'!A:H",
                        line_item_rows,
                    )

                    append_rows(
                        "'Review Queue'!A:H",
                        review_rows,
                    )

                    processed_count += 1
                    review_count += len(review_rows)

                    results_summary.append(
                        {
                            "File": selected_file["name"],
                            "Vendor": vendor,
                            "Invoice": invoice_number,
                            "Items": len(items),
                            "Needs Review": len(review_rows),
                            "Status": "Processed",
                        }
                    )

            except Exception as error:

                failed_count += 1

                append_rows(
                    "'Invoices'!A:G",
                    [[
                        selected_file["id"],
                        selected_file["name"],
                        None,
                        None,
                        None,
                        "Failed",
                        pd.Timestamp.utcnow().isoformat(),
                    ]],
                )

                results_summary.append(
                    {
                        "File": selected_file["name"],
                        "Vendor": None,
                        "Invoice": None,
                        "Items": 0,
                        "Needs Review": 0,
                        "Status": "Failed",
                    }
                )

                st.warning(
                    f"Could not process {selected_file['name']}: {error}"
                )

            progress.progress(
                (index + 1) / len(batch)
            )

        st.success(
            f"Batch finished. "
            f"{processed_count} processed, "
            f"{failed_count} failed, "
            f"{review_count} item(s) need review."
        )

        if results_summary:
            st.subheader("Batch Results")

            st.dataframe(
                pd.DataFrame(results_summary),
                use_container_width=True,
            )

else:
    st.success(
        "No new invoices are waiting to be processed."
    )
