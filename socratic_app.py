# socratic_app.py

import json
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials


BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"
PROMPT_TEST_DIR = BASE_DIR / "prompt_test"

MODEL = "gpt-5.2-2025-12-11"

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
        "session_id", "topic", "prompt_file", "turn_number",  # ← prompt_file 추가
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


def save_to_gsheet(topic: str, prompt_file: str):  # ← prompt_file 파라미터 추가
    try:
        gc = get_gsheet_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1

        ensure_header(worksheet)

        session_id = st.session_state.get("session_id", "unknown")
        messages = st.session_state.messages
        socratic_outputs = st.session_state.socratic_outputs
        dean_outputs = st.session_state.dean_outputs
        was_revised_list = st.session_state.was_revised

        # user 메시지만 순서대로 추출
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        # assistant 메시지 (첫 opening 제외)
        assistant_messages = [m["content"] for m in messages if m["role"] == "assistant"][1:]

        last_saved = st.session_state.get("last_saved_turn", 0)

        rows = []
        for i, soc_out in enumerate(socratic_outputs):
            if i < last_saved:
                continue  # 이미 저장된 턴은 스킵
            diagnosis = soc_out.get("diagnosis", {})
            strategy = soc_out.get("selected_strategy", {})

            user_msg = user_messages[i] if i < len(user_messages) else ""
            asst_msg = assistant_messages[i] if i < len(assistant_messages) else ""
            dean_out = dean_outputs[i] if i < len(dean_outputs) else {}
            was_revised = was_revised_list[i] if i < len(was_revised_list) else False

            row = [
                session_id,
                topic,
                prompt_file or "",           # ← prompt_file 기록
                soc_out.get("turn_number", i + 1),
                user_msg,
                asst_msg,
                diagnosis.get("main_claim", ""),
                strategy.get("primary", ""),
                strategy.get("secondary", ""),
                strategy.get("strategy_rationale", ""),
                dean_out.get("decision", ""),
                str(was_revised),
                json.dumps(soc_out, ensure_ascii=False),
                json.dumps(dean_out, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ]
            rows.append(row)

        if rows:
            worksheet.append_rows(rows, value_input_option="RAW")
            st.session_state.last_saved_turn = len(socratic_outputs)
            return True, len(rows)
        else:
            return "already_saved", 0

    except Exception as e:
        return False, str(e)


# -----------------------------
# Prompt loading
# -----------------------------

def load_prompt(filename: str) -> str:
    prompt_path = PROMPT_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def get_socratic_prompt_from_file(filename: str) -> str:
    """prompt_test/ 폴더에서 Socratic 프롬프트를 직접 읽기"""
    prompt_path = PROMPT_TEST_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def get_dean_prompt_from_socratic_file(prompt_file: str) -> str:
    dean_map = {
        "soc_pure.txt": "dean_pure.txt",
        "soc_add.txt": "dean_add.txt",
    }

    if prompt_file not in dean_map:
        raise ValueError(f"Unknown socratic prompt file: {prompt_file}")

    return load_prompt(dean_map[prompt_file])


def safe_json_loads(text: str) -> dict:
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


def call_model_json(client: OpenAI, instructions: str, payload: dict) -> dict:
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        reasoning={"effort": "medium"},
        text={"verbosity": "low"},
        temperature=0.2,
        max_output_tokens=1600,
    )
    return safe_json_loads(response.output_text)


def make_conversation_history() -> list[dict]:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]


# -----------------------------
# Socratic & Dean modules
# -----------------------------

def run_socratic_module(
    client: OpenAI,
    prompt_file: str,
    topic: str,
    user_response: str,
    turn_number: int,
    previous_questions: list[str],
    conversation_history: list[dict],
    dean_feedback: str = "",
    rejected_socratic_question: str = ""
) -> dict:
    instructions = get_socratic_prompt_from_file(prompt_file)
    payload = {
        "topic": topic,
        "user_response": user_response,
        "turn_number": turn_number,
        "previous_socratic_questions": previous_questions,
        "conversation_history": conversation_history,
        "dean_feedback": dean_feedback,
        "rejected_socratic_question": rejected_socratic_question,
    }
    return call_model_json(client, instructions, payload)

def run_dean_module(
    client: OpenAI,
    prompt_file: str,
    topic: str,
    user_response: str,
    previous_questions: list[str],
    conversation_history: list[dict],
    socratic_output: dict,
    condition: str
) -> dict:
    instructions = get_dean_prompt_from_socratic_file(prompt_file)
    payload = {
        "topic": topic,
        "user_response": user_response,
        "previous_socratic_questions": previous_questions,
        "conversation_history": conversation_history,
        "socratic_module_output": socratic_output,
        "socratic_question": socratic_output.get("socratic_question", ""),
        "scaffolding_condition": condition,
        "scaffolding_stage": socratic_output.get("scaffolding_stage", "none"),
    }
    return call_model_json(client, instructions, payload)


def generate_with_dean(
    client: OpenAI,
    condition: str,
    prompt_file: str,           # ← 추가
    topic: str,
    user_response: str,
    turn_number: int,
    previous_questions: list[str],
    conversation_history: list[dict]
) -> tuple[dict, dict, bool]:

    first_socratic = run_socratic_module(
        client=client,
        prompt_file=prompt_file,    # ← 변경
        topic=topic,
        user_response=user_response,
        turn_number=turn_number,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
    )

    first_dean = run_dean_module(
        client=client,
        prompt_file=prompt_file,
        topic=topic,
        user_response=user_response,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        socratic_output=first_socratic,
        condition=condition,
    )

    st.session_state.debug_trace = {
        "first_socratic_question": first_socratic.get("socratic_question", ""),
        "first_dean_decision": first_dean.get("decision", ""),
        "first_dean_feedback": first_dean.get("feedback", ""),
        "revised_socratic_question": "",
        "final_question_shown": first_socratic.get("socratic_question", ""),
    }
    
    decision = first_dean.get("decision", "").strip().lower()
    if decision == "ok":
        return first_socratic, first_dean, False

    revised_socratic = run_socratic_module(
        client=client,
        prompt_file=prompt_file,
        topic=topic,
        user_response=user_response,
        turn_number=turn_number,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        dean_feedback=first_dean.get("feedback", ""),
        rejected_socratic_question=first_socratic.get(
            "socratic_question",
            ""
        ),
    )

    st.session_state.debug_trace["revised_socratic_question"] = revised_socratic.get("socratic_question", "")
    st.session_state.debug_trace["final_question_shown"] = revised_socratic.get("socratic_question", "")

    return revised_socratic, first_dean, True


# -----------------------------
# Session state
# -----------------------------

def init_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "socratic_outputs" not in st.session_state:
        st.session_state.socratic_outputs = []
    if "dean_outputs" not in st.session_state:
        st.session_state.dean_outputs = []
    if "was_revised" not in st.session_state:
        st.session_state.was_revised = []
    if "debug_trace" not in st.session_state:
        st.session_state.debug_trace = {}
    if "session_id" not in st.session_state:
        st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]
    if "save_status" not in st.session_state:
        st.session_state.save_status = None  # None | "success" | "error"
    if "last_saved_turn" not in st.session_state:
        st.session_state.last_saved_turn = 0


def reset_chat():
    st.session_state.messages = []
    st.session_state.socratic_outputs = []
    st.session_state.dean_outputs = []
    st.session_state.was_revised = []
    st.session_state.session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]
    st.session_state.save_status = None
    st.session_state.last_saved_turn = 0


# -----------------------------
# Debug panel
# -----------------------------

def render_debug_panel():
    st.sidebar.subheader("Debug panel")

    if not st.session_state.socratic_outputs:
        st.sidebar.info("아직 생성된 Socratic JSON이 없습니다.")
        return

    latest_socratic = st.session_state.socratic_outputs[-1]
    latest_dean = st.session_state.dean_outputs[-1]
    latest_revised = st.session_state.was_revised[-1]

    meta = {
        "turn_number": latest_socratic.get("turn_number"),
        "diagnosis": latest_socratic.get("diagnosis"),
        "selected_strategy": latest_socratic.get("selected_strategy"),
        "socratic_question": latest_socratic.get("socratic_question"),
    }

    st.sidebar.markdown("### Socratic Module")
    st.sidebar.json(meta)
    st.sidebar.markdown("### Dean Module")
    st.sidebar.json(latest_dean)
    st.sidebar.markdown("### Revised?")
    st.sidebar.write(latest_revised)
    st.sidebar.markdown("### Revision Trace")
    st.sidebar.json(st.session_state.get("debug_trace", {}))


# -----------------------------
# Main run
# -----------------------------

def run(condition: str, topic: str, language: str = "eng", prompt_file: str = None):  # ← prompt_file 추가
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

    # Save 버튼 (사이드바)
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 대화 저장")

    has_data = len(st.session_state.socratic_outputs) > 0

    if st.sidebar.button("💾 Save to Google Sheets", disabled=not has_data):
        with st.spinner("저장 중..."):
            success, result = save_to_gsheet(topic, prompt_file or "")   # ← prompt_file 전달
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

        previous_questions = [
            item.get("socratic_question", "")
            for item in st.session_state.socratic_outputs
            if item.get("socratic_question")
        ]

        conversation_history = make_conversation_history()

        with st.spinner("Chatbot is typing..."):
            try:
                socratic_output, dean_output, was_revised = generate_with_dean(
                    client=client,
                    condition=condition,
                    prompt_file=prompt_file,    # ← 추가
                    topic=topic,
                    user_response=user_input,
                    turn_number=turn_number,
                    previous_questions=previous_questions,
                    conversation_history=conversation_history,
                )

                question = socratic_output.get("socratic_question", "")

                if not question:
                    question = "방금 답변에서 가장 중요한 표현 하나를 고른다면 무엇이고, 그 표현을 어떤 의미로 사용했는지 조금 더 설명해볼 수 있을까요?"

            except Exception as e:
                socratic_output = {
                    "turn_number": turn_number,
                    "scaffolding_condition": condition,
                    "scaffolding_stage": "error",
                    "diagnosis": {
                        "main_claim": "",
                        "reasoning_state": "",
                        "main_issue_to_address": "error"
                    },
                    "selected_strategy": {
                        "primary": "Maieutics",
                        "secondary": "none"
                    },
                    "socratic_question": "방금 답변에서 가장 중요한 표현 하나를 고른다면 무엇이고, 그 표현을 어떤 의미로 사용했는지 조금 더 설명해볼 수 있을까요?"
                }
                dean_output = {
                    "decision": "error",
                    "failure_types": [],
                    "feedback": str(e)
                }
                was_revised = False
                question = socratic_output["socratic_question"]

        st.session_state.socratic_outputs.append(socratic_output)
        st.session_state.dean_outputs.append(dean_output)
        st.session_state.was_revised.append(was_revised)

        st.session_state.messages.append({
            "role": "assistant",
            "content": question
        })

        with st.chat_message("assistant"):
            st.markdown(question)

        st.rerun()
