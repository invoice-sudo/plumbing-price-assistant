import streamlit as st

st.set_page_config(
    page_title="Plumbing Price Assistant",
    page_icon="🔧",
    layout="wide"
)

st.title("🔧 Plumbing Price Assistant")

st.write("AI-powered plumbing invoice and vendor price analysis.")

st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Invoices Processed", "0")

with col2:
    st.metric("Products Tracked", "0")

with col3:
    st.metric("Potential Savings", "$0")

st.divider()

st.subheader("Invoice Processing")

st.info("Your invoice analysis system is ready to be connected.")

if st.button("Process New Invoices"):
    st.success("Invoice processing will be connected next!")
