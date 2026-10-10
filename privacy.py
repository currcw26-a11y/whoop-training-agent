# privacy.py: the privacy policy, plus a button to delete your data.
# WHOOP and Google both ask apps for a privacy policy link; this page is it.
# Anyone can read it without signing in.

import streamlit as st

import db

st.title("🔒 Privacy")

st.markdown("""
*Last updated October 9, 2026*

**Training Coach** is a student project built for an AI seminar class at Wake Forest.
It gives training suggestions, **not medical advice**.

### What it collects
- Your **Google name and email**, so it knows who you are.
- Your **WHOOP** recovery, heart rate variability, resting heart rate, sleep, strain,
  and workouts, but only after you press **Connect WHOOP**.
- Your **Google Calendar** events, but only if you connect your calendar. The coach
  uses them to find free time, and adds a workout only after you approve it.
- The **city** you enter, to look up the weather.

### How it's used
- Only to show you your own data and plan your training.
- When you press **Plan my day**, your recent numbers are sent to Anthropic's Claude
  API, which writes the plan.
- Your data is never sold, and never shared with anyone else.

### How it's protected
- It's stored in a database that only this website's server can open.
- Your WHOOP and Google login tokens are encrypted before they're saved.

### Deleting your data
Sign in and use the button below. It permanently deletes everything this site has
saved about you. You can also remove the app's access from inside the WHOOP app and
from your Google Account's security settings.

Questions? Contact the developer through GitHub: **currcw26-a11y**.
""")

if st.user.is_logged_in:
    st.subheader("Delete my data")
    sure = st.checkbox(f"Yes, permanently delete everything saved for {st.user.email}")
    if st.button("Delete my data", type="primary", disabled=not sure):
        db.delete_everything(st.user.email)
        st.logout()
