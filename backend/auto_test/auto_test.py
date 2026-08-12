import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI


# =========================================================
# 설정
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
SIMULATOR_PROMPT_PATH = BASE_DIR / "participant_simulator.txt"
RESULT_DIR = BASE_DIR / "results"

BACKEND_URL = "https://socrates-backend-lac.vercel.app/chat"

# ---------------------------------------------------------
# 테스트할 Socrates 챗봇
# ---------------------------------------------------------

TARGET_MODEL = "gpt"
# gpt / claude / deepseek / gemini

CONDITION = "soc_pure"
# soc_pure / soc_add

NUMBER_OF_TURNS = 8


# ---------------------------------------------------------
# 테스트 주제
# 실제 Qualtrics가 backend에 보내는 형태와 비슷하게 맞춤
# ---------------------------------------------------------

TOPIC = """주제명: 탈모약의 건강보험 적용
세부 설명: 탈모 치료제에 건강보험을 적용해야 하는가?"""


# 참가자 시뮬레이터가 처음부터 가질 기본 입장
INITIAL_POSITION = (
    "탈모약에 건강보험을 어느 정도 적용하는 것에 찬성한다."
)


# Qualtrics에서 실제로 처음 보여주는 opening과 맞추면 됨
OPENING_QUESTION = (
    "이 주제에 대해 어떻게 생각하시나요? "
    "찬성이든 반대든, 아직 잘 모르겠다는 생각이라도 괜찮으니 "
    "자유롭게 말씀해 주세요."
)


# ---------------------------------------------------------
# 참가자 Simulator 설정
# ---------------------------------------------------------

SIMULATOR_MODEL = "gpt-5.2-2025-12-11"
SIMULATOR_REASONING_EFFORT = "low"
SIMULATOR_VERBOSITY = "low"
SIMULATOR_MAX_OUTPUT_TOKENS = 300


# =========================================================
# OpenAI client
# =========================================================

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다."
        )

    return OpenAI(api_key=api_key)


# =========================================================
# Participant simulator
# =========================================================

def load_simulator_prompt():
    if not SIMULATOR_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"participant_simulator.txt를 찾을 수 없습니다: "
            f"{SIMULATOR_PROMPT_PATH}"
        )

    return SIMULATOR_PROMPT_PATH.read_text(
        encoding="utf-8"
    ).strip()


def format_conversation_history(conversation_history):
    if not conversation_history:
        return "(아직 이전 대화 없음)"

    lines = []

    for message in conversation_history:
        role = message["role"]
        content = message["content"]

        if role == "assistant":
            label = "Chatbot"
        else:
            label = "Participant"

        lines.append(
            f"{label}: {content}"
        )

    return "\n".join(lines)


def generate_participant_response(
    client,
    prompt_template,
    conversation_history,
    latest_question,
):
    conversation_text = format_conversation_history(
        conversation_history
    )

    # .format() 대신 replace를 쓰는 이유:
    # 대화 내용에 중괄호가 있어도 문제없이 작동하도록 함
    rendered_prompt = (
        prompt_template
        .replace("{topic}", TOPIC)
        .replace(
            "{initial_position}",
            INITIAL_POSITION,
        )
        .replace(
            "{conversation_history}",
            conversation_text,
        )
        .replace(
            "{latest_question}",
            latest_question,
        )
    )

    start_time = time.perf_counter()

    response = client.responses.create(
        model=SIMULATOR_MODEL,
        instructions=rendered_prompt,
        input=(
            "Generate the participant's response now. "
            "Return only the response."
        ),
        reasoning={
            "effort":
                SIMULATOR_REASONING_EFFORT
        },
        text={
            "verbosity":
                SIMULATOR_VERBOSITY
        },
        max_output_tokens=
            SIMULATOR_MAX_OUTPUT_TOKENS,
    )

    latency = (
        time.perf_counter()
        - start_time
    )

    participant_response = (
        response.output_text.strip()
    )

    if not participant_response:
        raise RuntimeError(
            "Participant simulator가 빈 응답을 반환했습니다."
        )

    return participant_response, latency


# =========================================================
# Socrates backend 호출
# =========================================================

def request_socrates_reply(
    session_id,
    turn_number,
    participant_response,
    conversation_history,
):
    payload = {
        "session_id": session_id,
        "condition": CONDITION,
        "model_key": TARGET_MODEL,
        "topic": TOPIC,
        "user_message": participant_response,
        "turn_number": turn_number,
        "conversation_history":
            conversation_history,
    }

    start_time = time.perf_counter()

    response = requests.post(
        BACKEND_URL,
        json=payload,
        timeout=300,
    )

    latency = (
        time.perf_counter()
        - start_time
    )

    if not response.ok:
        raise RuntimeError(
            "Socrates backend error "
            f"{response.status_code}: "
            f"{response.text}"
        )

    data = response.json()

    reply = str(
        data.get("reply", "")
    ).strip()

    if not reply:
        raise RuntimeError(
            "Socrates backend 응답에 reply가 없습니다."
        )

    return (
        reply,
        data.get("module_record"),
        latency,
    )


# =========================================================
# 결과 저장
# =========================================================

def save_result(result, filepath):
    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    filepath.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# =========================================================
# 간단 요약
# =========================================================

def print_module_summary(turns):
    first_pass_ok = 0
    fallback_count = 0
    second_generation_count = 0
    strategies = {}

    for turn in turns:
        module_record = (
            turn.get("module_record")
            or {}
        )

        if (
            module_record.get(
                "first_dean_decision"
            )
            == "ok"
        ):
            first_pass_ok += 1

        if module_record.get(
            "fallback_used"
        ):
            fallback_count += 1

        if module_record.get(
            "second_socratic_output"
        ):
            second_generation_count += 1

        final_socratic = (
            module_record.get(
                "final_socratic_output"
            )
            or {}
        )

        selected_strategy = (
            final_socratic.get(
                "selected_strategy"
            )
            or {}
        )

        primary = (
            selected_strategy.get(
                "primary"
            )
        )

        if primary:
            strategies[primary] = (
                strategies.get(
                    primary,
                    0,
                )
                + 1
            )

    print("\n")
    print("=" * 60)
    print("AUTO TEST SUMMARY")
    print("=" * 60)

    print(
        f"Target model      : {TARGET_MODEL}"
    )
    print(
        f"Condition         : {CONDITION}"
    )
    print(
        f"Simulator         : {SIMULATOR_MODEL}"
    )
    print(
        f"Turns             : {len(turns)}"
    )

    print(
        f"First-pass OK     : "
        f"{first_pass_ok}/{len(turns)}"
    )

    print(
        f"Second generation : "
        f"{second_generation_count}/{len(turns)}"
    )

    print(
        f"Fallback          : "
        f"{fallback_count}/{len(turns)}"
    )

    print("\nStrategies")

    if not strategies:
        print("  (strategy information 없음)")
    else:
        for strategy, count in strategies.items():
            print(
                f"  {strategy}: {count}"
            )

    print("=" * 60)


# =========================================================
# Main
# =========================================================

def main():
    client = get_openai_client()

    prompt_template = (
        load_simulator_prompt()
    )

    session_id = (
        "auto_"
        + uuid.uuid4().hex[:12]
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = (
        f"{timestamp}_"
        f"{TARGET_MODEL}_"
        f"{CONDITION}.json"
    )

    result_path = (
        RESULT_DIR / filename
    )

    # -----------------------------------------------------
    # 실제 Qualtrics 대화 구조처럼
    # opening assistant message부터 history에 넣음
    # -----------------------------------------------------

    conversation_history = [
        {
            "role": "assistant",
            "content": OPENING_QUESTION,
        }
    ]

    result = {
        "session_id": session_id,
        "created_at":
            datetime.now().isoformat(),
        "target_model": TARGET_MODEL,
        "condition": CONDITION,
        "simulator_model":
            SIMULATOR_MODEL,
        "topic": TOPIC,
        "initial_position":
            INITIAL_POSITION,
        "opening_question":
            OPENING_QUESTION,
        "turns": [],
    }

    latest_question = OPENING_QUESTION

    print("=" * 60)
    print("SOCRATES AUTO TEST")
    print("=" * 60)

    print(
        f"Target model : {TARGET_MODEL}"
    )
    print(
        f"Condition    : {CONDITION}"
    )
    print(
        f"Simulator    : {SIMULATOR_MODEL}"
    )

    print("=" * 60)

    for turn_number in range(
        1,
        NUMBER_OF_TURNS + 1,
    ):
        print(
            f"\n\n--- TURN {turn_number} ---"
        )

        print(
            "\n[Chatbot]"
        )
        print(
            latest_question
        )

        # -------------------------------------------------
        # 1. GPT-5.2 참가자 응답 생성
        # -------------------------------------------------

        (
            participant_response,
            simulator_latency,
        ) = generate_participant_response(
            client=client,
            prompt_template=
                prompt_template,
            conversation_history=
                conversation_history,
            latest_question=
                latest_question,
        )

        print(
            "\n[Participant]"
        )
        print(
            participant_response
        )

        # -------------------------------------------------
        # Backend에는 현재 participant response 직전의
        # history를 보내야 함
        # -------------------------------------------------

        backend_history = [
            {
                "role":
                    message["role"],
                "content":
                    message["content"],
            }
            for message
            in conversation_history
        ]

        # -------------------------------------------------
        # 2. Socrates backend 호출
        # -------------------------------------------------

        (
            socrates_reply,
            module_record,
            backend_latency,
        ) = request_socrates_reply(
            session_id=session_id,
            turn_number=turn_number,
            participant_response=
                participant_response,
            conversation_history=
                backend_history,
        )

        print(
            "\n[Socrates]"
        )
        print(
            socrates_reply
        )

        print(
            "\n"
            f"Simulator latency: "
            f"{simulator_latency:.1f}s"
        )

        print(
            f"Backend latency:   "
            f"{backend_latency:.1f}s"
        )

        # -------------------------------------------------
        # 3. 결과 기록
        # -------------------------------------------------

        turn_record = {
            "turn_number":
                turn_number,

            "chatbot_question":
                latest_question,

            "participant_response":
                participant_response,

            "socrates_reply":
                socrates_reply,

            "simulator_latency_seconds":
                round(
                    simulator_latency,
                    3,
                ),

            "backend_latency_seconds":
                round(
                    backend_latency,
                    3,
                ),

            "module_record":
                module_record,
        }

        result["turns"].append(
            turn_record
        )

        # -------------------------------------------------
        # 4. conversation history 갱신
        # -------------------------------------------------

        conversation_history.append(
            {
                "role": "user",
                "content":
                    participant_response,
            }
        )

        conversation_history.append(
            {
                "role": "assistant",
                "content":
                    socrates_reply,
            }
        )

        latest_question = (
            socrates_reply
        )

        # 매 턴 저장
        # 중간에 오류나도 앞부분이 날아가지 않도록
        save_result(
            result,
            result_path,
        )

    # -----------------------------------------------------
    # 완료
    # -----------------------------------------------------

    result["completed_at"] = (
        datetime.now().isoformat()
    )

    save_result(
        result,
        result_path,
    )

    print_module_summary(
        result["turns"]
    )

    print(
        "\n저장 완료:"
    )

    print(
        result_path
    )


if __name__ == "__main__":
    main()
