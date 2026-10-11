# app.py: where the website starts.
# It sets up the browser tab, the look (style.css), the pages in the sidebar, and the sign-in box.
# Run it with:  streamlit run app.py

from pathlib import Path

import streamlit as st

import db

st.set_page_config(page_title="Training Coach", page_icon=":material/directions_run:", layout="wide")
st.html(Path(__file__).parent / "style.css")  # extra styling on top of .streamlit/config.toml

pages = [st.Page("home.py", title="Home", icon=":material/home:", default=True)]

if st.user.is_logged_in:
    pages.append(st.Page("settings.py", title="Coaching profile", icon=":material/tune:"))
    # Add the person to the database the first time we see them (once per visit).
    if "profile_ready" not in st.session_state:
        db.ensure_profile(st.user.email, st.user.get("name"))
        st.session_state.profile_ready = True
    with st.sidebar:
        st.caption(f":material/account_circle: {st.user.email}")
        st.button("Sign out", icon=":material/logout:", on_click=st.logout)

pages.append(st.Page("privacy.py", title="Privacy", icon=":material/lock:"))
st.navigation(pages).run()
