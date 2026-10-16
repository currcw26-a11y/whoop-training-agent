# chat.py: the "Ask the coach" page. Message the coach about training, sleep, or a goal.
# Each message runs the same agent as "Plan my day" (agent.chat), with the same tools, so it
# answers from the person's real data. Messages are saved, so the conversation is still
# here after a reload. "New chat" starts fresh.

from datetime import datetime, timedelta, timezone

import anthropic
import streamlit as st

import agent
import db
import ui

SUGGESTIONS = [
    ("flag", "Help me plan for a 10K"),
    ("sports_tennis", "Can I play tennis tonight?"),
    ("bedtime", "How has my sleep been lately?"),
    ("help", "Why is today an easy day?"),
]

email = st.user.email
first_name = (st.user.get("given_name") or st.user.get("name") or email).split()[0]

ui.label("Ask the coach", "forum", "chat")
title_spot, new_spot = st.columns([3, 1], vertical_alignment="bottom")
title_spot.title(f"What's on your mind, {first_name}?")

if not db.is_connected(email, "whoop"):
    st.info("Connect your WHOOP first, so the coach has something to go on.", icon=":material/watch:")
    st.page_link("home.py", label="Connect WHOOP", icon=":material/link:")
    st.stop()

# The conversation: messages from the last 3 days, or since "New chat" was pressed.
since = st.session_state.get("chat_since") or (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
turns = []
for row in db.recent_chats(email, since):
    steps = row["steps"] or []
    if steps and steps[0]["kind"] == "question":
        turns.append((steps[0]["text"], row["final_decision"], steps))

if turns and new_spot.button("New chat", icon=":material/add_comment:", width="stretch"):
    st.session_state.chat_since = datetime.now(timezone.utc).isoformat()
    st.rerun()

ui.draw('<p class="chat-intro">Ask about a workout, your sleep, or a goal you\'re working toward. The coach looks at '
        'your WHOOP data, your calendar, the weather, and your plan before it answers, and it remembers what you '
        'tell it.</p>')
st.write("")

# Each message sits in a keyed container (msg-you-N / msg-coach-N) so style.css can tell them apart.
for number, (question, answer, steps) in enumerate(turns):
    with st.container(key=f"msg-you-{number}"), st.chat_message("user", avatar=":material/person:"):
        st.write(ui.plain(question))
    with st.container(key=f"msg-coach-{number}"), st.chat_message("assistant", avatar=":material/directions_run:"):
        st.markdown(ui.plain(answer))
        used = ui.tools_used(steps)
        if used:
            with st.expander(f"Looked at {used} thing{'s' if used != 1 else ''}", icon=":material/account_tree:"):
                ui.show_steps([s for s in steps if s["kind"] != "decision"])

# A new message: typed in the box, or one of the suggestions (only shown before the first message).
asked = st.chat_input("Message the coach")
if not turns:
    with st.container(horizontal=True, key="suggest-row"):
        for number, (icon, text) in enumerate(SUGGESTIONS):
            if st.button(text, icon=f":material/{icon}:", key=f"suggest-{number}"):
                asked = text

if asked:
    with st.container(key="msg-you-new"), st.chat_message("user", avatar=":material/person:"):
        st.write(ui.plain(asked))
    with st.container(key="msg-coach-new"), st.chat_message("assistant", avatar=":material/directions_run:"):
        if not agent.can_run(email):
            st.write(agent.LIMIT_MESSAGE)
        else:
            with st.status("Looking at your data...", expanded=False) as status:
                try:
                    agent.chat(email, asked, [(q, a) for q, a, _ in turns], ui.show_step)
                    status.update(label="Done", state="complete")
                    st.rerun()  # redraw the whole conversation from the saved messages
                except anthropic.APIError as error:
                    problem, explanation = ui.claude_problem(error)
                    status.update(label=problem, state="error")
                    st.error(explanation)

st.caption("Training suggestions only, not medical advice. If something hurts, see a doctor or athletic trainer.")
