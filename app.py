# app.py: the website.
# Right now it's just a test page that proves the setup works.
# In Phase 1 it becomes the real dashboard.

import streamlit as st

st.set_page_config(page_title="Training Coach", page_icon="🏃")

st.title("🏃 Training Coach")

# Try loading every library the project needs. If one is missing,
# Python raises an error here and the page shows it in red.
import anthropic
import cryptography
import requests
import supabase

st.success("Setup works! All the libraries are installed.")
st.write("The real app gets built in Phase 1.")
