# app.py: where the website starts.
# It sets up the browser tab, the pages in the sidebar, and the sign-in box.
# Run it with:  streamlit run app.py

import streamlit as st

import db

st.set_page_config(page_title="Training Coach", page_icon="🏃", layout="wide")

pages = [st.Page("home.py", title="Home", icon="🏃", default=True)]

if st.user.is_logged_in:
    pages.append(st.Page("settings.py", title="Coaching profile", icon="⚙️"))
    # Add the person to the database the first time we see them (once per visit).
    if "profile_ready" not in st.session_state:
        db.ensure_profile(st.user.email, st.user.get("name"))
        st.session_state.profile_ready = True
    with st.sidebar:
        st.caption(f"Signed in as **{st.user.email}**")
        st.button("Sign out", on_click=st.logout)

pages.append(st.Page("privacy.py", title="Privacy", icon="🔒"))
st.navigation(pages).run()
