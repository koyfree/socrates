# main.py

import streamlit as st
import socratic_app as app


# -----------------------------
# Page settings
# -----------------------------

st.set_page_config(
    page_title="Socratic Chatbot Internal Test",
    layout="wide"
)

st.title("Socratic Chatbot Internal Test")


# -----------------------------
# Session state
# -----------------------------
if "condition_locked" not in st.session_state:
    st.session_state.condition_locked = False

if "selected_condition" not in st.session_state:
    st.session_state.selected_condition = "scaffolding"

if "selected_topic" not in st.session_state:
    st.session_state.selected_topic = ""


# -----------------------------
# Topic options
# -----------------------------
TOPIC_OPTIONS = {
    "기본소득": "Should the government introduce a universal basic income?",
    "검찰개혁": "Should the prosecution system be reformed?",
    "탈원전": "Should the country phase out nuclear power?",
    "한미동맹": "Should South Korea maintain a strong alliance with the United States?",
    "주4일제 노동시간 단축": "Should the workweek be reduced to four days?",
    "군가산점": "Should military service points be given in public employment exams?"
}


# -----------------------------
# Sidebar: condition and topic selection
# -----------------------------
with st.sidebar:
    st.header("Experiment Settings")

    condition_label = st.radio(
        "Experimental condition",
        options=["Scaffolding", "Non-scaffolding"],
        index=0 if st.session_state.selected_condition == "scaffolding" else 1,
        disabled=st.session_state.condition_locked
    )

    if condition_label == "Scaffolding":
        condition_key = "scaffolding"
    else:
        condition_key = "non_scaffolding"

    topic_label = st.selectbox(
    "Controversial topic",
    options=list(TOPIC_OPTIONS.keys()),
    disabled=st.session_state.condition_locked
)

    topic_text = TOPIC_OPTIONS[topic_label]

    st.session_state.selected_condition = condition_key
    st.session_state.selected_topic = topic_text

    st.markdown("---")
    st.markdown("### Selected settings")
    st.write("Condition:", condition_key)
    st.write("Topic:", topic_text)

    if st.button("Reset entire test"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# -----------------------------
# Main area: show selected setup
# -----------------------------
st.markdown("### Current setup")
st.markdown(f"**Condition:** `{st.session_state.selected_condition}`")
st.markdown(f"**Topic:** {st.session_state.selected_topic}")


# -----------------------------
# Run chatbot app
# -----------------------------
app.run(
    condition=st.session_state.selected_condition,
    topic=st.session_state.selected_topic,
    language="eng"
)
