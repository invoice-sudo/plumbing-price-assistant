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


# -------------------------------------------------
# CONNECTIONS
# -------------------------------------------------

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


# -------------------------------------------------
# GOOGLE DRIVE
# -------------------------------------------------

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

    files = results.get("files", [])

    # Oldest first so processing order is predictable
    files.sort(
        key=lambda x: x.get("modifiedTime", "")
    )

    return files


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


# -------------------------------------------------
# PDF READING
# -------------------------------------------------

def extract_pdf_text(file_buffer):
    text_parts = []

    with pdfplumber.open(file_buffer) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()

            if page_text:
                text_parts.append(page_text)

    return "\n".join(text_parts)


# -------------------------------------------------
# GOOGLE SHEETS
# -------------------------------------------------

def append_rows(range_name, rows):
    if not rows:
        return

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={
            "values": rows
        },
    ).execute()


def get_processed_file_ids():
    try:
        result = (
            sheets_service
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=sheet_id,
                range="'Invoices'!A:A",
            )
            .execute()
        )

        rows = result.get("values", [])

        processed_ids = set()

        for row in rows:
            if not row:
                continue

            value = str(row[0]).strip()

            # Ignore the header
            if value == "drive_file_id":
                continue

            # Ignore our old test row
            if value == "TEST":
                continue

            processed_ids.add(value)

        return processed_ids

    except Exception:
        return set()


# -------------------------------------------------
# OPENAI ANALYSIS
# -------------------------------------------------

def analyze_invoice(invoice_text, filename):
    prompt = f"""
You are a controlled plumbing purchasing invoice extraction system.

Your job is ONLY to extract information that is actually present
on the invoice.

STRICT RULES:

1. Never invent information.
2. Never guess a missing price.
3. Never guess a quantity.
4. Never invent a product.
5. Never change an invoice number.
6. Never change a PO number.
7. Ignore tax.
8. Ignore subtotal.
9. Ignore invoice total.
10. Ignore payment information.
11. Ignore balances.
12. Ignore freight or shipping unless it is clearly a purchased
    product rather than a fee.
13. Extract actual purchased line items only.
14. Preserve the original product description.
15. unit_price must be the UNIT PRICE when clearly shown.
16. Do not use an extended line total as the unit price.
17. If information cannot be determined reliably, use null.
18. Confidence must be between 0 and 1.
19. Return valid JSON only.
20. Do not provide explanations or markdown.

Return exactly this JSON structure:

{{
  "vendor": "vendor name or null",
  "invoice_number": "invoice number or null",
  "po_number": "PO number or null",
  "items": [
    {{
      "description": "original invoice description",
      "quantity": 1,
      "unit_price": 0.00,
      "confidence": 0.95
    }}
  ]
}}

SOURCE FILE:
{filename}

INVOICE TEXT:
{invoice_text}
"""

    response = openai_client.responses.create(
        model="gpt-5-mini",
        input=prompt,
    )

    output = response.output_text.strip()

    output = output.replace(
        "```json",
        ""
    )

    output = output.replace(
        "```",
        ""
    )

    output = output.strip()

    return json.loads(output)


# -------------------------------------------------
# VALIDATION
# -------------------------------------------------

def validate_item(item):
    description = item.get("description")
    quantity = item.get("quantity")
    unit_price = item.get("unit_price")
    confidence = item.get("confidence", 0)

    problems = []

    if not description:
        problems.append(
            "Missing description"
        )

    if unit_price is None:
        problems.append(
            "Missing unit price"
        )

    if isinstance(unit_price, (int, float)):
        if unit_price < 0:
            problems.append(
                "Negative unit price"
            )
    else:
        if unit_price is not None:
            problems.append(
                "Invalid unit price"
            )

    if quantity is not None:
        if isinstance(quantity, (int, float)):
            if quantity <= 0:
                problems.append(
                    "Invalid quantity"
                )
        else:
            problems.append(
                "Invalid quantity"
            )

    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0

    if confidence < 0.85:
        problems.append(
            "Low confidence"
        )

    return problems


# -------------------------------------------------
# PROCESS ONE FILE
# -------------------------------------------------

def process_invoice_file(selected_file):
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
    po_number = data.get(
        "po_number"
    )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):
        raise ValueError(
            "AI returned an invalid items list."
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

        problems = validate_item(
            item
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

        if problems:
            review_rows.append(
                row
            )
        else:
            line_item_rows.append(
                row
            )

    # Save line items first
    append_rows(
        "'Line Items'!A:H",
        line_item_rows,
    )

    # Save uncertain items separately
    append_rows(
        "'Review Queue'!A:H",
        review_rows,
    )

    # Mark invoice processed LAST.
    # This prevents a partially failed invoice from being
    # permanently treated as completed.
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

    return {
        "File": selected_file["name"],
        "Vendor": vendor,
        "Invoice": invoice_number,
        "PO": po_number,
        "Items": len(items),
        "Approved Items": len(
            line_item_rows
        ),
        "Needs Review": len(
            review_rows
        ),
        "Status": "Processed",
    }


# -------------------------------------------------
# LOAD CURRENT STATUS
# -------------------------------------------------

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


# -------------------------------------------------
# BATCH BUTTONS
# -------------------------------------------------

st.subheader(
    "Process New Invoices"
)

st.caption(
    "Only invoices that have not already been processed will be analyzed."
)


button_col1, button_col2, button_col3 = st.columns(3)

with button_col1:
    process_10 = st.button(
        "Process 10",
        use_container_width=True,
        disabled=len(new_pdfs) == 0,
    )

with button_col2:
    process_50 = st.button(
        "Process 50",
        use_container_width=True,
        disabled=len(new_pdfs) == 0,
    )

with button_col3:
    process_all = st.button(
        "Process All",
        use_container_width=True,
        disabled=len(new_pdfs) == 0,
    )


batch = None


if process_10:
    batch = new_pdfs[:10]


if process_50:
    batch = new_pdfs[:50]


if process_all:
    batch = new_pdfs


# -------------------------------------------------
# PROCESS SELECTED BATCH
# -------------------------------------------------

if batch is not None:

    total = len(batch)

    if total == 0:
        st.info(
            "There are no new invoices to process."
        )

    else:
        st.info(
            f"Processing {total} new invoice(s)."
        )

        progress = st.progress(0)

        status_text = st.empty()

        processed_count = 0
        failed_count = 0
        total_review_items = 0

        results_summary = []

        for index, selected_file in enumerate(
            batch
        ):

            status_text.write(
                f"Analyzing {index + 1} of {total}: "
                f"{selected_file['name']}"
            )

            try:
                result = process_invoice_file(
                    selected_file
                )

                processed_count += 1

                total_review_items += (
                    result["Needs Review"]
                )

                results_summary.append(
                    result
                )

            except Exception as error:
                failed_count += 1

                # Failed invoices are intentionally NOT marked
                # as processed. That lets you retry them later.

                results_summary.append(
                    {
                        "File": selected_file[
                            "name"
                        ],
                        "Vendor": None,
                        "Invoice": None,
                        "PO": None,
                        "Items": 0,
                        "Approved Items": 0,
                        "Needs Review": 0,
                        "Status": "Failed",
                    }
                )

                st.warning(
                    f"Failed: "
                    f"{selected_file['name']} "
                    f"— {error}"
                )

            progress.progress(
                (index + 1) / total
            )

        status_text.empty()

        st.success(
            f"Finished. "
            f"{processed_count} invoice(s) processed, "
            f"{failed_count} failed, "
            f"{total_review_items} item(s) sent to review."
        )

        if results_summary:
            st.subheader(
                "Batch Results"
            )

            results_df = pd.DataFrame(
                results_summary
            )

            st.dataframe(
                results_df,
                use_container_width=True,
            )


# -------------------------------------------------
# NO NEW INVOICES
# -------------------------------------------------

if len(new_pdfs) == 0:
    st.success(
        "All invoices currently in Google Drive have been processed."
    )
