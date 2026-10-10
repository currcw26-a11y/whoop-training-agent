# app.py: where the website starts.
# It sets up the browser tab and lists the pages in the sidebar.
# Run it with:  streamlit run app.py

import streamlit as st

st.set_page_config(page_title="Training Coach", page_icon="🏃", layout="wide")

pages = [
    st.Page("home.py", title="Home", icon="🏃", default=True),
    st.Page("privacy.py", title="Privacy", icon="🔒"),
]
st.navigation(pages).run()
