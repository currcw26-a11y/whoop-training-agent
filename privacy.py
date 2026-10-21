# privacy.py: the privacy policy, plus a button to delete your data.
# WHOOP and Google both ask apps for a privacy policy link; this page is it.
# Anyone can read it without signing in.

import streamlit as st

import db
import ui

ui.label("Your data", "lock", "privacy")
st.title("Privacy")

st.markdown("""
*Last updated October 20, 2026*

**Training Coach** is a student project built by Clive Curry for an AI seminar class at
Wake Forest. It gives training suggestions, **not medical advice**.

### What it collects
- Your **Google name and email**, so it knows who you are.
- Your **WHOOP** recovery, heart rate variability, resting heart rate, sleep, strain,
  and workouts, but only after you press **Connect WHOOP**.
- Your **Google Calendar** event titles and times, but only if you connect your calendar.
  The coach uses them to find free time. It adds a workout only after you approve it, and
  it never changes or deletes your other events.
- The **city** you enter, to look up the weather.
- Your **messages to the coach** on the Ask the coach page, and short **notes** the coach saves
  from them (like an injury you mention). You can see, edit, or delete the notes on your
  Coaching profile page.
- Your **morning check-in**, if you fill it in: how you rate your energy, stress, soreness, and
  sleep (1 to 5), and any pain you report (how much, where, and the yes/no warning-sign questions).

### How it's used
- Only to show you your own data and plan your training.
- When you press **Plan my day** or send the coach a message, your recent numbers, your check-in, the
  calendar events it needs, and the coach's notes are sent to Anthropic's Claude API,
  which writes the answer.
- Your data is never sold, never used for ads, and never shared with anyone except as
  described here.
- Training Coach's use of information received from Google APIs adheres to the
  [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
  including the Limited Use requirements.

### How it's protected
- It's stored in a database that only this website's server can open.
- Your WHOOP and Google login tokens are encrypted before they're saved.
- The site's font (Inter) loads from Google Fonts, so your browser contacts Google when a page opens.

### Deleting your data
Sign in and use the button below. It permanently deletes everything this site has
saved about you. You can also remove the app's access from inside the WHOOP app and
from your Google Account's security settings.

Questions? Contact the developer, Clive Curry, through GitHub: **currcw26-a11y**.
""")

if st.user.is_logged_in:
    st.subheader("Delete my data")
    sure = st.checkbox(f"Yes, permanently delete everything saved for {st.user.email}")
    if st.button("Delete my data", type="primary", disabled=not sure):
        db.delete_everything(st.user.email)
        st.logout()
