# app.py: where the website starts.
# It sets up the browser tab, the look (style.css), the logo, and the pages in the top bar.
# Run it with:  streamlit run app.py

from pathlib import Path

import streamlit as st

import db

HERE = Path(__file__).parent

st.set_page_config(page_title="Training Coach", page_icon=":material/directions_run:", layout="wide")
st.html(HERE / "style.css")  # extra styling on top of .streamlit/config.toml

# The logo in the top bar. There's a light and a dark version so the name is readable in both.
dark = (st.context.theme or {}).get("type") == "dark"
st.logo(str(HERE / f"assets/logo-{'dark' if dark else 'light'}.svg"), size="large")

pages = [st.Page("home.py", title="Home", icon=":material/home:", default=True)]
if st.user.is_logged_in:
    pages.append(st.Page("settings.py", title="Coaching profile", icon=":material/tune:"))
    # Add the person to the database the first time we see them (once per visit).
    if "profile_ready" not in st.session_state:
        db.ensure_profile(st.user.email, st.user.get("name"))
        st.session_state.profile_ready = True
pages.append(st.Page("privacy.py", title="Privacy", icon=":material/lock:"))

st.navigation(pages, position="top").run()
