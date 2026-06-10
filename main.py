import streamlit as st
import socratic_app as app
from pathlib import Path

st.set_page_config(
    page_title="Socratic Chatbot Internal Test",
    layout="wide"
)
st.title("Socratic Chatbot Internal Test")

if "condition_locked" not in st.session_state:
    st.session_state.condition_locked = False
if "selected_topic" not in st.session_state:
    st.session_state.selected_topic = ""
if "started" not in st.session_state:
    st.session_state.started = False

TOPIC_OPTIONS = {
    "기본소득": "기본소득 제도를 도입해야 하는가?",
    "검찰개혁": "검찰 권한을 축소하는 방향의 검찰개혁은 필요한가?",
    "탈원전": "한국은 원자력 발전을 단계적으로 줄여야 하는가?",
    "한미동맹": "한국은 한미동맹을 현재보다 더 강화해야 하는가?",
    "주4일제 노동시간 단축": "한국 사회는 주4일제 근무를 도입해야 하는가?",
    "군가산점": "공공기관 채용에서 군복무자에게 가산점을 부여해야 하는가?",
    "기타(직접 입력)": ""
}

with st.sidebar:
    st.header("Experiment Settings")

    # 프롬프트 선택
    prompt_test_dir = Path("prompt_test")
    prompt_files = sorted([f.name for f in prompt_test_dir.glob("*.txt")]) if prompt_test_dir.exists() else []

    if prompt_files:
        selected_prompt = st.selectbox(
            "Socratic prompt",
            options=prompt_files,
            disabled=st.session_state.condition_locked
        )
    else:
        st.warning("prompt_test/ 폴더에 .txt 파일이 없습니다.")
        selected_prompt = None

    # 주제 선택
    topic_label = st.selectbox(
        "Controversial topic",
        options=list(TOPIC_OPTIONS.keys()),
        disabled=st.session_state.condition_locked
    )
    if topic_label == "기타(직접 입력)":
        custom_topic = st.text_area(
            "직접 주제를 입력하고 ctrl+enter를 누르세요",
            placeholder="예: 대학 입시에서 정시 비중을 확대해야 하는가?",
            height=80,
            disabled=st.session_state.condition_locked
        )
        topic_text = custom_topic.strip()
    else:
        topic_text = TOPIC_OPTIONS[topic_label]

    st.session_state.selected_topic = topic_text

    if not st.session_state.started:
        if topic_text and selected_prompt:
            if st.button("▶ 시작", type="primary"):
                st.session_state.started = True
                st.session_state.condition_locked = True
                st.session_state.selected_prompt = selected_prompt
                st.rerun()

    st.markdown("---")
    st.markdown("### Selected settings")
    st.write("Condition: non_scaffolding (fixed)")
    st.write("Socratic prompt:", st.session_state.get("selected_prompt", selected_prompt) or "선택 대기 중")
    st.write("Topic:", topic_text if topic_text else "직접 입력 대기 중")

    if st.button("Reset entire test"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

if not st.session_state.selected_topic:
    st.warning("주제를 먼저 입력하거나 선택해주세요.")
    st.stop()

if not st.session_state.started:
    st.info("왼쪽 사이드바에서 주제를 선택한 후 시작 버튼을 눌러주세요.")
    st.stop()

app.run(
    condition="non_scaffolding",
    topic=st.session_state.selected_topic,
    language="eng",
    prompt_file=st.session_state.get("selected_prompt"),
)
