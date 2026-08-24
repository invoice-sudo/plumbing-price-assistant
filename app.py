import streamlit as st
import pdfplumber
import pandas as pd
import json
from openai import OpenAI

st.set_page_config(
    page_title="Plumbing Price Assistant",
    page_icon="🔧",
    layout="wide"
)

st.title("🔧 Plumbing Price Assistant")
st.write("AI-powered plumbing invoice and vendor price analysis.")

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

st.divider()

uploaded_file = st.file_uploader(
    "Upload ONE invoice PDF for our first test",
    type=["pdf"]
)

if uploaded_file is not None:

    st.success("Invoice uploaded successfully.")

    if st.button("Analyze Invoice"):

        with st.spinner("Reading and analyzing invoice..."):

            # Read PDF
            text = ""

            with pdfplumber.open(uploaded_file) as pdf:

                for page in pdf.pages:

                    page_text = page.extract_text()

                    if page_text:
                        text += page_text + "\n"

            if not text.strip():
                st.error("No readable text was found in this PDF.")
                st.stop()

            # Instructions for AI
            prompt = f"""
You are a controlled plumbing invoice extraction system.

Extract the purchasing information from this invoice.

STRICT RULES:

- Only use information actually shown on the invoice.
- NEVER invent information.
- NEVER guess a price.
- Do not include subtotal.
- Do not include tax.
- Do not include shipping or freight.
- Do not include invoice totals.
- Extract individual purchased products only.
- Preserve the original product description.
- Price should be the UNIT PRICE whenever clearly available.
- If information is uncertain, use null.
- Confidence must be between 0 and 1.

Return ONLY valid JSON.

Use exactly this structure:

{{
  "vendor": "vendor name or null",
  "invoice_number": "invoice number or null",
  "po_number": "PO number or null",
  "items": [
    {{
      "description": "original product description",
      "quantity": 1,
      "unit_price": 0.00,
      "confidence": 0.95
    }}
  ]
}}

INVOICE TEXT:

{text}
"""

            response = client.responses.create(
                model="gpt-5-mini",
                input=prompt
            )

            output = response.output_text.strip()

            # Remove markdown fences if model includes them
            output = output.replace("```json", "")
            output = output.replace("```", "")
            output = output.strip()

            try:
                data = json.loads(output)

            except json.JSONDecodeError:

                st.error(
                    "The AI returned information in an unexpected format."
                )

                st.code(output)

                st.stop()

        # Display invoice information
        st.success("Invoice analyzed!")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "Vendor",
                data.get("vendor") or "Unknown"
            )

        with col2:
            st.metric(
                "Invoice #",
                data.get("invoice_number") or "Unknown"
            )

        with col3:
            st.metric(
                "PO #",
                data.get("po_number") or "Unknown"
            )

        items = data.get("items", [])

        if items:

            df = pd.DataFrame(items)

            df["Needs Review"] = (
                df["confidence"].fillna(0) < 0.85
            )

            st.subheader("Products Found")

            st.dataframe(
                df,
                use_container_width=True
            )

        else:

            st.warning(
                "No product line items were found."
            )
