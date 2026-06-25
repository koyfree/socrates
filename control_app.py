# control_app.py

import json
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials


BASE_DIR = Path(__file__).resolve().parent
PROMPT_TEST_DIR = BASE_DIR / "prompt_test"

MODEL = "gpt-5.2"

SPREADSHEET_ID = "1cZMIsynHca9PoIhDthEdOowHxZ-jG_m5Vacv2AIqPbE"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# -----------------------------
# Google Sheets
# -----------------------------

def get_gsheet_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def ensure_header(worksheet):
    header = [
        "session_id", "topic", "prompt_file", "turn_number",
        "user_message", "assistant_message",
        "main_claim",
        "primary_strategy", "secondary_strategy", "strategy_rationale",
        "dean_decision", "was_revised",
        "full_socratic_json", "full_dean_json",
        "saved_at"
    ]
    existing = worksheet.row_values(1)
    if existing != header:
        worksheet.insert_row(header, index=1)


def save_to_gsheet(topic: str, prompt_file: str):
    try:
        gc = get_gsheet_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1

        ensure_header(worksheet)

        session_id = st.session_state.get("session_id", "unknown")
        messages = st.session_state.messages
        control_outputs = st.session_state.control_outputs

        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        assistant_messages = [m["content"] for m in messages if m["role"] == "assistant"][1:]

        last_saved = st.session_state.get("last_saved_turn", 0)

        rows = []
        for i, control_out in enumerate(control_outputs):
            if i < last_saved:
                continue

            diagnosis = control_out.get("diagnosis", {})

            user_msg = user_messages[i] if i < len(user_messages) else ""
            asst_msg = assistant_messages[i] if i < len(assistant_messages) else ""

            row = [
                session_id,
                topic,
                prompt_file or "",
                control_out.get("turn_number", i + 1),
                user_msg,
                asst_msg,
                diagnosis.get("main_claim", ""),
                "",        # primary_strategy
                "",        # secondary_strategy
                "",        # strategy_rationale
                "",        # dean_decision
                "False",   # was_revised
                json.dumps(control_out, ensure_ascii=False),
                "",        # full_dean_json
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ]
            rows.append(row)

        if rows:
            worksheet.append_rows(rows, value_input_option="RAW")
            st.session_state.last_saved_turn = len(control_outputs)
            return True, len(rows)
        else:
            return "already_saved", 0

    except Exception as e:
        return False, str(e)


# -----------------------------
# Prompt loading
# -----------------------------

def get_control_prompt_from_file(filename: str) -> str:
    prompt_path = PROMPT_TEST_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def safe_json_loads(text: str) -> dict:
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


def call_model_json(client: OpenAI, instructions: str, payload: dict) -> dict:
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
    )
    return safe_json_loads(response.output_text)


def make_conversation_history() -> list[dict]:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]


# -----------------------------
# Control module
# -----------------------------

def run_control_module(
    client: OpenAI,
    prompt_file: str,
    topic: str,
    user_response: str,
    turn_number: int,
    conversation_history: list[dict],
) -> dict:
    instructions = get_control_prompt_from_file(prompt_file)

    payload = {
        "topic": topic,
        "user_response": user_response,
        "turn_number": turn_number,
        "conversation_history": conversation_history,
    }

    return call_model_json(client, instructions, payload)


# -----------------------------
# Session state
# -----------------------------

def init_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "control_outputs" not in st.session_state:
        st.session_state.control_outputs = []
    if "debug_trace" not in st.session_state:
        st.session_state.debug_trace = {}
    if "session_id" not in st.session_state:
        st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]
    if "save_status" not in st.session_state:
        st.session_state.save_status = None
    if "last_saved_turn" not in st.session_state:
        st.session_state.last_saved_turn = 0


def reset_chat():
    st.session_state.messages = []
    st.session_state.control_outputs = []
    st.session_state.debug_trace = {}
    st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]
    st.session_state.save_status = None
    st.session_state.last_saved_turn = 0


# -----------------------------
# Debug panel
# -----------------------------

def render_debug_panel():
    st.sidebar.subheader("Debug panel")

    if not st.session_state.control_outputs:
        st.sidebar.info("아직 생성된 Control JSON이 없습니다.")
        return

    latest_control = st.session_state.control_outputs[-1]

    meta = {
        "turn_number": latest_control.get("turn_number"),
        "diagnosis": latest_control.get("diagnosis"),
        "control_response": latest_control.get("control_response"),
    }

    st.sidebar.markdown("### Control Module")
    st.sidebar.json(meta)

    if st.session_state.get("debug_trace"):
        st.sidebar.markdown("### Debug Trace")
        st.sidebar.json(st.session_state.debug_trace)


# -----------------------------
# Main run
# -----------------------------

def run(condition: str, topic: str, language: str = "eng", prompt_file: str = None):
    init_session_state()

    if st.session_state.get("current_topic") != topic:
        reset_chat()
        st.session_state.current_topic = topic

    if not st.session_state.messages:
        opening = (
            f"주제: **{topic}**\n\n"
            "이 주제에 대해 어떻게 생각하시나요? "
            "찬성이든 반대든, 아직 잘 모르겠다는 생각이라도 괜찮으니 "
            "자유롭게 말씀해 주세요."
        )
        st.session_state.messages.append({
            "role": "assistant",
            "content": opening
        })

    client = OpenAI(api_key=st.secrets["openai"]["api_key"])

    render_debug_panel()

    # Save 버튼
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 대화 저장")

    has_data = len(st.session_state.control_outputs) > 0

    if st.sidebar.button("💾 Save to Google Sheets", disabled=not has_data):
        with st.spinner("저장 중..."):
            success, result = save_to_gsheet(topic, prompt_file or "")

        if success == True:
            st.session_state.save_status = ("success", result)
        elif success == "already_saved":
            st.session_state.save_status = ("already_saved", result)
        else:
            st.session_state.save_status = ("error", result)

    if st.session_state.save_status:
        status, result = st.session_state.save_status
        if status == "success":
            st.sidebar.success(f"✅ {result}개 턴 저장 완료!")
        elif status == "already_saved":
            st.sidebar.info("ℹ️ 새로 저장할 턴이 없습니다.")
        else:
            st.sidebar.error(f"❌ 저장 실패: {result}")

    # 대화 렌더링
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Type the user's response here...")

    if user_input:
        st.session_state.messages.append({
            "role": "user",
            "content": user_input
        })

        with st.chat_message("user"):
            st.markdown(user_input)

        turn_number = sum(
            1 for m in st.session_state.messages
            if m["role"] == "user"
        )

        conversation_history = make_conversation_history()

        with st.spinner("Chatbot is typing..."):
            try:
                control_output = run_control_module(
                    client=client,
                    prompt_file=prompt_file,
                    topic=topic,
                    user_response=user_input,
                    turn_number=turn_number,
                    conversation_history=conversation_history,
                )

                bot_response = control_output.get("control_response", "")

                if not bot_response:
                    bot_response = "말씀해주신 의견을 확인했습니다."

                st.session_state.debug_trace = {
                    "raw_control_output": control_output,
                    "final_response_shown": bot_response,
                }

            except Exception as e:
                control_output = {
                    "turn_number": turn_number,
                    "diagnosis": {
                        "main_claim": ""
                    },
                    "control_response": "말씀해주신 의견을 확인했습니다.",
                    "error": str(e)
                }

                bot_response = control_output["control_response"]

                st.session_state.debug_trace = {
                    "error": str(e),
                    "fallback_response": bot_response,
                }

        st.session_state.control_outputs.append(control_output)

        st.session_state.messages.append({
            "role": "assistant",
            "content": bot_response
        })

        with st.chat_message("assistant"):
            st.markdown(bot_response)

        st.rerun()
