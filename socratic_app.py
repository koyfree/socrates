# socratic_app.py

import json
from pathlib import Path

import streamlit as st
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

MODEL = "gpt-5.2"


def load_prompt(filename: str) -> str:
    prompt_path = PROMPT_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def get_socratic_prompt(condition: str) -> str:
    if condition == "scaffolding":
        return load_prompt("soc_scaf_new.txt")
    elif condition == "non_scaffolding":
        return load_prompt("soc_no_scaf_new.txt")
    else:
        raise ValueError(f"Unknown condition: {condition}")


def get_dean_prompt() -> str:
    return load_prompt("dean.txt")


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
    """
    Return the full conversation history currently visible in the chat.
    This includes user messages and assistant Socratic questions.
    """
    return [
        {
            "role": m["role"],
            "content": m["content"]
        }
        for m in st.session_state.messages
    ]


def run_socratic_module(
    client: OpenAI,
    condition: str,
    topic: str,
    user_response: str,
    turn_number: int,
    previous_questions: list[str],
    conversation_history: list[dict],
    dean_feedback: str = ""
) -> dict:
    instructions = get_socratic_prompt(condition)

    payload = {
        "topic": topic,
        "user_response": user_response,
        "turn_number": turn_number,
        "previous_socratic_questions": previous_questions,
        "conversation_history": conversation_history,
        "dean_feedback": dean_feedback,
    }

    return call_model_json(client, instructions, payload)


def run_dean_module(
    client: OpenAI,
    topic: str,
    user_response: str,
    previous_questions: list[str],
    conversation_history: list[dict],
    socratic_output: dict,
    condition: str
) -> dict:
    instructions = get_dean_prompt()

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
    topic: str,
    user_response: str,
    turn_number: int,
    previous_questions: list[str],
    conversation_history: list[dict]
) -> tuple[dict, dict, bool]:

    first_socratic = run_socratic_module(
        client=client,
        condition=condition,
        topic=topic,
        user_response=user_response,
        turn_number=turn_number,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
    )

    first_dean = run_dean_module(
        client=client,
        topic=topic,
        user_response=user_response,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        socratic_output=first_socratic,
        condition=condition,
    )

    if first_dean.get("decision") == "ok":
        return first_socratic, first_dean, False

    revised_socratic = run_socratic_module(
        client=client,
        condition=condition,
        topic=topic,
        user_response=user_response,
        turn_number=turn_number,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        dean_feedback=first_dean.get("feedback", ""),
    )

    revised_dean = run_dean_module(
        client=client,
        topic=topic,
        user_response=user_response,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        socratic_output=revised_socratic,
        condition=condition,
    )

    return revised_socratic, revised_dean, True


def init_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "socratic_outputs" not in st.session_state:
        st.session_state.socratic_outputs = []

    if "dean_outputs" not in st.session_state:
        st.session_state.dean_outputs = []

    if "was_revised" not in st.session_state:
        st.session_state.was_revised = []


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
        "scaffolding_condition": latest_socratic.get("scaffolding_condition"),
        "scaffolding_stage": latest_socratic.get("scaffolding_stage"),
        "diagnosis": latest_socratic.get("diagnosis"),
        "selected_strategy": latest_socratic.get("selected_strategy"),
    }

    st.sidebar.markdown("### Socratic Module")
    st.sidebar.json(meta)

    st.sidebar.markdown("### Dean Module")
    st.sidebar.json(latest_dean)

    st.sidebar.markdown("### Revised?")
    st.sidebar.write(latest_revised)


def reset_chat():
    st.session_state.messages = []
    st.session_state.socratic_outputs = []
    st.session_state.dean_outputs = []
    st.session_state.was_revised = []


def run(condition: str, topic: str, language: str = "eng"):
    init_session_state()

    if not st.session_state.messages:
        opening = (
            f"주제: **{topic}**\n\n"
            "이 주제에 대해 어떻게 생각하시나요?? "
            "찬성이든 반대든, 아직 잘 모르겠다는 생각이라도 괜찮으니 "
            "자유롭게 말씀해 주세요."
        )
        st.session_state.messages.append({
            "role": "assistant",
            "content": opening
        })

    client = OpenAI(api_key=st.secrets["openai"]["api_key"])

    render_debug_panel()
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Type the user's response here...")

    if user_input:
        st.session_state.messages.append({
            "role": "user",
            "content": user_input
        })

        # 사용자의 입력을 즉시 화면에 표시
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

        # 핵심 추가 부분:
        # 현재 user_input까지 포함한 전체 대화 내역을 Socratic module과 Dean module에 전달
        conversation_history = make_conversation_history()

        with st.spinner("Chatbot is typing..."):
            try:
                socratic_output, dean_output, was_revised = generate_with_dean(
                    client=client,
                    condition=condition,
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

        # 챗봇 답변도 즉시 화면에 표시
        with st.chat_message("assistant"):
            st.markdown(question)

        st.rerun()
