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
    "기본소득": "기본소득 제도를 도입해야 하는가?",
    "검찰개혁": "검찰 권한을 축소하는 방향의 검찰개혁은 필요한가?",
    "탈원전": "한국은 원자력 발전을 단계적으로 줄여야 하는가?",
    "한미동맹": "한국은 한미동맹을 현재보다 더 강화해야 하는가?",
    "주4일제 노동시간 단축": "한국 사회는 주4일제 근무를 도입해야 하는가?",
    "군가산점": "공공기관 채용에서 군복무자에게 가산점을 부여해야 하는가?"
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
# Run chatbot app
# -----------------------------
app.run(
    condition=st.session_state.selected_condition,
    topic=st.session_state.selected_topic,
    language="eng"
)
