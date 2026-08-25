import io
import json
import re

import pandas as pd
import pdfplumber
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from openai import OpenAI


# =========================================================
# PAGE SETUP
# =========================================================

st.set_page_config(
    page_title="Plumbing Price Assistant",
    page_icon="🔧",
    layout="wide",
)

st.title("🔧 Plumbing Price Assistant")
st.write(
    "Analyze plumbing invoices, organize products by supplier, "
    "and compare pricing."
)


# =========================================================
# CONNECTIONS
# =========================================================

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


# =========================================================
# GOOGLE SHEETS HELPERS
# =========================================================

def get_sheet_values(range_name):
    result = (
        sheets_service
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=sheet_id,
            range=range_name,
        )
        .execute()
    )

    return result.get("values", [])


def append_rows(range_name, rows):
    if not rows:
        return

    (
        sheets_service
        .spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )


def overwrite_sheet(range_name, rows):
    (
        sheets_service
        .spreadsheets()
        .values()
        .clear(
            spreadsheetId=sheet_id,
            range=range_name,
            body={},
        )
        .execute()
    )

    if rows:
        (
            sheets_service
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=sheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            )
            .execute()
        )


# =========================================================
# DATABASE HEADERS
# =========================================================

def setup_headers():

    invoice_headers = [[
        "drive_file_id",
        "filename",
        "vendor",
        "invoice_number",
        "po_number",
        "status",
        "processed_at",
    ]]

    line_item_headers = [[
        "drive_file_id",
        "filename",
        "vendor",
        "invoice_number",
        "description",
        "quantity",
        "unit_price",
        "confidence",
        "standard_product",
        "product_type",
        "size",
        "material",
        "connection_type",
        "length",
        "manufacturer_part_number",
        "match_confidence",
    ]]

    review_headers = [[
        "drive_file_id",
        "filename",
        "vendor",
        "invoice_number",
        "description",
        "quantity",
        "unit_price",
        "confidence",
        "standard_product",
        "review_reason",
    ]]

    comparison_headers = [[
        "Standard Product",
        "Size",
        "Manufacturer Part #",
        "Home Depot Price",
        "Ferguson Price",
        "WinSupply Price",
        "Cheapest Supplier",
        "Cheapest Price",
        "Savings Per Unit",
    ]]

    invoices = get_sheet_values("'Invoices'!A1:G1")

    if not invoices:
        append_rows(
            "'Invoices'!A:G",
            invoice_headers,
        )

    line_items = get_sheet_values(
        "'Line Items'!A1:P1"
    )

    if not line_items:
        append_rows(
            "'Line Items'!A:P",
            line_item_headers,
        )
    else:
        overwrite_sheet(
            "'Line Items'!A1:P1",
            line_item_headers,
        )

    review = get_sheet_values(
        "'Review Queue'!A1:J1"
    )

    if not review:
        append_rows(
            "'Review Queue'!A:J",
            review_headers,
        )
    else:
        overwrite_sheet(
            "'Review Queue'!A1:J1",
            review_headers,
        )

    comparison = get_sheet_values(
        "'Price Comparison'!A1:I1"
    )

    if not comparison:
        append_rows(
            "'Price Comparison'!A:I",
            comparison_headers,
        )


# =========================================================
# VENDOR NORMALIZATION
# =========================================================

def normalize_vendor(vendor):
    if not vendor:
        return "Unknown"

    text = vendor.lower()

    if "home depot" in text:
        return "Home Depot"

    if "ferguson" in text:
        return "Ferguson"

    if (
        "winsupply" in text
        or "win supply" in text
    ):
        return "WinSupply"

    return vendor.strip()


# =========================================================
# GOOGLE DRIVE
# =========================================================

def get_drive_pdfs():
    query = (
        f"'{folder_id}' in parents "
        "and trashed = false "
        "and mimeType = 'application/pdf'"
    )

    result = (
        drive_service
        .files()
        .list(
            q=query,
            fields=(
                "files("
                "id,name,modifiedTime"
                ")"
            ),
            pageSize=1000,
        )
        .execute()
    )

    files = result.get("files", [])

    files.sort(
        key=lambda file: file.get(
            "modifiedTime",
            "",
        )
    )

    return files


def download_pdf(file_id):
    request = (
        drive_service
        .files()
        .get_media(
            fileId=file_id
        )
    )

    buffer = io.BytesIO()

    downloader = MediaIoBaseDownload(
        buffer,
        request,
    )

    done = False

    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)

    return buffer


# =========================================================
# PDF EXTRACTION
# =========================================================

def extract_pdf_text(buffer):
    text_parts = []

    with pdfplumber.open(buffer) as pdf:

        for page in pdf.pages:

            page_text = page.extract_text()

            if page_text:
                text_parts.append(
                    page_text
                )

    return "\n".join(text_parts)


# =========================================================
# DUPLICATE PROTECTION
# =========================================================

def get_processed_file_ids():

    try:
        rows = get_sheet_values(
            "'Invoices'!A:A"
        )

        processed = set()

        for row in rows:

            if not row:
                continue

            value = str(
                row[0]
            ).strip()

            if value in [
                "drive_file_id",
                "TEST",
            ]:
                continue

            processed.add(
                value
            )

        return processed

    except Exception:
        return set()


# =========================================================
# AI INVOICE ANALYSIS
# =========================================================

def analyze_invoice(
    invoice_text,
    filename,
):

    prompt = f"""
You are a controlled plumbing invoice extraction system.

Your job is to extract purchased plumbing products
and create standardized product information that can
be used to compare the same product across suppliers.

STRICT RULES:

- Never invent information.
- Never guess a missing price.
- Never guess a model number.
- Never invent sizes or materials.
- Use the UNIT PRICE, not the extended total.
- Ignore tax.
- Ignore subtotal.
- Ignore freight.
- Ignore shipping.
- Ignore invoice totals.
- Extract actual purchased products only.
- Preserve the original invoice description.

For each product identify when possible:

- standardized product name
- product type
- size
- material
- connection type
- length
- manufacturer part number

IMPORTANT MATCHING RULE:

Only standardize products together when their important
physical characteristics match.

For example:

3/4 solder elbow
and
3/4 press elbow

ARE NOT the same product.

10 ft copper tube
and
20 ft copper tube

ARE NOT the same product.

If uncertain about product matching, lower match_confidence.

confidence refers to invoice extraction confidence.

match_confidence refers to confidence in the standardized
product identity.

Both must be numbers between 0 and 1.

Return ONLY valid JSON.

Return exactly this structure:

{{
  "vendor": "vendor name or null",
  "invoice_number": "invoice number or null",
  "po_number": "PO number or null",

  "items": [
    {{
      "description": "original invoice description",
      "quantity": 1,
      "unit_price": 0.00,

      "standard_product":
        "clean standardized plumbing product name",

      "product_type":
        "product type or null",

      "size":
        "size or null",

      "material":
        "material or null",

      "connection_type":
        "connection type or null",

      "length":
        "length or null",

      "manufacturer_part_number":
        "part number or null",

      "confidence": 0.95,

      "match_confidence": 0.95
    }}
  ]
}}

SOURCE FILE:

{filename}

INVOICE TEXT:

{invoice_text}
"""

    response = (
        openai_client
        .responses
        .create(
            model="gpt-5-mini",
            input=prompt,
        )
    )

    output = (
        response
        .output_text
        .strip()
    )

    output = output.replace(
        "```json",
        "",
    )

    output = output.replace(
        "```",
        "",
    )

    return json.loads(
        output.strip()
    )


# =========================================================
# VALIDATION
# =========================================================

def validate_item(item):

    problems = []

    description = item.get(
        "description"
    )

    unit_price = item.get(
        "unit_price"
    )

    confidence = item.get(
        "confidence",
        0,
    )

    match_confidence = item.get(
        "match_confidence",
        0,
    )

    standard_product = item.get(
        "standard_product"
    )

    if not description:
        problems.append(
            "Missing description"
        )

    if unit_price is None:
        problems.append(
            "Missing unit price"
        )

    if not standard_product:
        problems.append(
            "Missing standardized product"
        )

    try:
        confidence = float(
            confidence
        )
    except Exception:
        confidence = 0

    try:
        match_confidence = float(
            match_confidence
        )
    except Exception:
        match_confidence = 0

    if confidence < 0.85:
        problems.append(
            "Low extraction confidence"
        )

    if match_confidence < 0.90:
        problems.append(
            "Low product match confidence"
        )

    return problems


# =========================================================
# PROCESS ONE INVOICE
# =========================================================

def process_invoice_file(
    selected_file,
):

    pdf_buffer = download_pdf(
        selected_file["id"]
    )

    invoice_text = extract_pdf_text(
        pdf_buffer
    )

    if not invoice_text.strip():
        raise ValueError(
            "No readable text found."
        )

    data = analyze_invoice(
        invoice_text,
        selected_file["name"],
    )

    vendor = normalize_vendor(
        data.get("vendor")
    )

    invoice_number = data.get(
        "invoice_number"
    )

    po_number = data.get(
        "po_number"
    )

    items = data.get(
        "items",
        [],
    )

    approved_rows = []
    review_rows = []

    for item in items:

        problems = validate_item(
            item
        )

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

        standard_product = item.get(
            "standard_product"
        )

        product_type = item.get(
            "product_type"
        )

        size = item.get(
            "size"
        )

        material = item.get(
            "material"
        )

        connection_type = item.get(
            "connection_type"
        )

        length = item.get(
            "length"
        )

        manufacturer_part_number = (
            item.get(
                "manufacturer_part_number"
            )
        )

        match_confidence = item.get(
            "match_confidence",
            0,
        )

        line_row = [
            selected_file["id"],
            selected_file["name"],
            vendor,
            invoice_number,
            description,
            quantity,
            unit_price,
            confidence,
            standard_product,
            product_type,
            size,
            material,
            connection_type,
            length,
            manufacturer_part_number,
            match_confidence,
        ]

        if problems:

            review_rows.append([
                selected_file["id"],
                selected_file["name"],
                vendor,
                invoice_number,
                description,
                quantity,
                unit_price,
                confidence,
                standard_product,
                ", ".join(problems),
            ])

        else:

            approved_rows.append(
                line_row
            )

    append_rows(
        "'Line Items'!A:P",
        approved_rows,
    )

    append_rows(
        "'Review Queue'!A:J",
        review_rows,
    )

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
        "Items": len(items),
        "Approved": len(
            approved_rows
        ),
        "Needs Review": len(
            review_rows
        ),
    }


# =========================================================
# PRICE COMPARISON
# =========================================================

def rebuild_price_comparison():

    rows = get_sheet_values(
        "'Line Items'!A:P"
    )

    if len(rows) <= 1:
        return 0

    headers = rows[0]

    data_rows = rows[1:]

    records = []

    for row in data_rows:

        padded = row + [""] * (
            len(headers) - len(row)
        )

        record = dict(
            zip(
                headers,
                padded,
            )
        )

        standard_product = record.get(
            "standard_product"
        )

        vendor = normalize_vendor(
            record.get(
                "vendor"
            )
        )

        price = record.get(
            "unit_price"
        )

        match_confidence = record.get(
            "match_confidence"
        )

        if not standard_product:
            continue

        try:
            price = float(price)
        except Exception:
            continue

        try:
            match_confidence = float(
                match_confidence
            )
        except Exception:
            continue

        if match_confidence < 0.90:
            continue

        records.append({
            "standard_product":
                standard_product,

            "size":
                record.get("size"),

            "manufacturer_part_number":
                record.get(
                    "manufacturer_part_number"
                ),

            "vendor":
                vendor,

            "price":
                price,
        })

    if not records:
        return 0

    df = pd.DataFrame(
        records
    )

    comparison_rows = []

    grouped = df.groupby(
        "standard_product"
    )

    for product, group in grouped:

        prices = {}

        for vendor in [
            "Home Depot",
            "Ferguson",
            "WinSupply",
        ]:

            vendor_rows = group[
                group["vendor"] == vendor
            ]

            if vendor_rows.empty:
                prices[vendor] = None
            else:
                prices[vendor] = (
                    vendor_rows[
                        "price"
                    ].min()
                )

        available = {
            vendor: price
            for vendor, price
            in prices.items()
            if price is not None
        }

        if available:

            cheapest_supplier = min(
                available,
                key=available.get,
            )

            cheapest_price = available[
                cheapest_supplier
            ]

            highest_price = max(
                available.values()
            )

            savings = (
                highest_price
                - cheapest_price
            )

        else:

            cheapest_supplier = ""
            cheapest_price = ""
            savings = ""

        first = group.iloc[0]

        comparison_rows.append([
            product,
            first.get(
                "size",
                "",
            ),
            first.get(
                "manufacturer_part_number",
                "",
            ),
            prices["Home Depot"]
                if prices["Home Depot"]
                is not None
                else "",
            prices["Ferguson"]
                if prices["Ferguson"]
                is not None
                else "",
            prices["WinSupply"]
                if prices["WinSupply"]
                is not None
                else "",
            cheapest_supplier,
            cheapest_price,
            savings,
        ])

    comparison_rows.sort(
        key=lambda row:
        str(row[0]).lower()
    )

    final_rows = [[
        "Standard Product",
        "Size",
        "Manufacturer Part #",
        "Home Depot Price",
        "Ferguson Price",
        "WinSupply Price",
        "Cheapest Supplier",
        "Cheapest Price",
        "Savings Per Unit",
    ]] + comparison_rows

    overwrite_sheet(
        "'Price Comparison'!A:I",
        final_rows,
    )

    return len(
        comparison_rows
    )


# =========================================================
# SETUP DATABASE
# =========================================================

setup_headers()


# =========================================================
# CURRENT STATUS
# =========================================================

all_pdfs = get_drive_pdfs()

processed_ids = (
    get_processed_file_ids()
)

new_pdfs = [
    file
    for file in all_pdfs
    if file["id"]
    not in processed_ids
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


# =========================================================
# PROCESSING BUTTONS
# =========================================================

st.subheader(
    "Process New Invoices"
)

button1, button2, button3 = (
    st.columns(3)
)

with button1:
    process_10 = st.button(
        "Process 10",
        use_container_width=True,
        disabled=not new_pdfs,
    )

with button2:
    process_50 = st.button(
        "Process 50",
        use_container_width=True,
        disabled=not new_pdfs,
    )

with button3:
    process_all = st.button(
        "Process All",
        use_container_width=True,
        disabled=not new_pdfs,
    )


batch = None

if process_10:
    batch = new_pdfs[:10]

elif process_50:
    batch = new_pdfs[:50]

elif process_all:
    batch = new_pdfs


# =========================================================
# RUN BATCH
# =========================================================

if batch is not None:

    total = len(batch)

    progress = st.progress(0)

    status = st.empty()

    results = []

    failures = 0

    for index, selected_file in enumerate(
        batch
    ):

        status.write(
            f"Analyzing "
            f"{index + 1} of {total}: "
            f"{selected_file['name']}"
        )

        try:

            result = process_invoice_file(
                selected_file
            )

            results.append(
                result
            )

        except Exception as error:

            failures += 1

            st.warning(
                f"Failed: "
                f"{selected_file['name']} "
                f"— {error}"
            )

        progress.progress(
            (index + 1) / total
        )

    status.write(
        "Building supplier price comparison..."
    )

    comparison_count = (
        rebuild_price_comparison()
    )

    status.empty()

    st.success(
        f"Finished processing. "
        f"{len(results)} invoice(s) completed, "
        f"{failures} failed. "
        f"{comparison_count} standardized products "
        f"are now in Price Comparison."
    )

    if results:

        st.dataframe(
            pd.DataFrame(results),
            use_container_width=True,
        )


# =========================================================
# MANUAL COMPARISON REBUILD
# =========================================================

st.divider()

st.subheader(
    "Supplier Price Comparison"
)

if st.button(
    "Rebuild Price Comparison",
    use_container_width=True,
):

    count = (
        rebuild_price_comparison()
    )

    st.success(
        f"Price Comparison updated "
        f"with {count} standardized products."
    )
