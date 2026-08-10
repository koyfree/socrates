import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field


# ---------------------------------
# 기본 설정
# ---------------------------------

BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

MODEL = "gpt-5.2-2025-12-11"
REASONING_EFFORT = "low"
VERBOSITY = "low"
MAX_OUTPUT_TOKENS = 1600
#---------------여기
FORCE_FALLBACK_TEST = FALSE


app = FastAPI(
    title="Socrates Chatbot API",
    version="0.4.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------
# 요청·응답 형식
# ---------------------------------

class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)

    condition: Literal[
        "control",
        "soc_pure",
        "soc_add",
    ]

    topic: str = Field(min_length=1)

    user_message: str = Field(
        min_length=1,
        max_length=4000,
    )

    turn_number: int = Field(ge=1)

    conversation_history: list[ConversationMessage] = Field(
        default_factory=list
    )


class ChatResponse(BaseModel):
    reply: str
    condition: str
    turn_number: int

    # Qualtrics의 __js_module_log에 저장할 내부 기록
    module_record: dict = Field(
        default_factory=dict
    )


# ---------------------------------
# 기본 엔드포인트
# ---------------------------------

@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Socrates Chatbot API is running."
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok"
    }


# ---------------------------------
# 공통 함수
# ---------------------------------

@lru_cache(maxsize=10)
def load_prompt(filename: str) -> str:
    prompt_path = PROMPT_DIR / filename

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_path}"
        )

    return prompt_path.read_text(
        encoding="utf-8"
    ).strip()


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    return OpenAI(api_key=api_key)


def safe_json_loads(text: str) -> dict:
    cleaned = text.strip()

    cleaned = (
        cleaned
        .replace("```json", "")
        .replace("```JSON", "")
        .replace("```", "")
        .strip()
    )

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if first_brace == -1 or last_brace == -1:
        raise ValueError(
            "The response does not contain a JSON object."
        )

    json_text = cleaned[
        first_brace:last_brace + 1
    ]

    parsed = json.loads(json_text)

    if not isinstance(parsed, dict):
        raise ValueError(
            "The response is not a JSON object."
        )

    return parsed


def call_model_json(
    instructions: str,
    payload: dict,
) -> dict:
    try:
        client = get_openai_client()

        response = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=json.dumps(
                payload,
                ensure_ascii=False,
            ),
            reasoning={
                "effort": REASONING_EFFORT
            },
            text={
                "verbosity": VERBOSITY
            },
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except Exception as error:
        print(
            "OpenAI API error:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=502,
            detail="Failed to generate model response.",
        ) from error

    raw_text = response.output_text.strip()

    if not raw_text:
        raise HTTPException(
            status_code=502,
            detail="The model returned an empty response.",
        )

    try:
        return safe_json_loads(raw_text)

    except Exception as error:
        print(
            "JSON parsing error:",
            type(error).__name__,
            str(error),
        )

        print(
            "Raw model output:",
            raw_text,
        )

        raise HTTPException(
            status_code=502,
            detail="The model returned invalid JSON.",
        ) from error


def build_conversation_history(
    request: ChatRequest
) -> list[dict]:
    """
    기존 Streamlit 코드와 마찬가지로
    현재 사용자 발화까지 포함한 전체 대화 기록을 만듭니다.
    """

    history = [
        {
            "role": message.role,
            "content": message.content,
        }
        for message
        in request.conversation_history
    ]

    history.append(
        {
            "role": "user",
            "content": request.user_message,
        }
    )

    return history


def get_previous_socratic_questions(
    request: ChatRequest
) -> list[str]:
    """
    대화 기록 속 assistant 발화 중
    첫 번째 opening 메시지를 제외한 이전 질문들을 추출합니다.
    """

    assistant_messages = [
        message.content
        for message
        in request.conversation_history
        if message.role == "assistant"
    ]

    if len(assistant_messages) <= 1:
        return []

    return assistant_messages[1:]


# ---------------------------------
# Control 조건
# ---------------------------------

def generate_control_reply(
    request: ChatRequest
) -> tuple[str, dict]:

    instructions = load_prompt(
        "control.txt"
    )

    conversation_history = (
        build_conversation_history(request)
    )

    payload = {
        "topic": request.topic,
        "user_response": request.user_message,
        "turn_number": request.turn_number,
        "conversation_history": conversation_history,
    }

    control_output = call_model_json(
        instructions=instructions,
        payload=payload,
    )

    reply = control_output.get(
        "control_response",
        ""
    )

    if not isinstance(reply, str) or not reply.strip():
        print(
            "Invalid control output:",
            json.dumps(
                control_output,
                ensure_ascii=False,
            ),
        )

        raise HTTPException(
            status_code=502,
            detail="The control response was missing.",
        )

    module_record = {
        "turn_number": request.turn_number,
        "condition": request.condition,
        "prompt_file": "control.txt",
        "control_output": control_output,
        "final_reply": reply.strip(),
    }

    return reply.strip(), module_record


# ---------------------------------
# Socratic·Dean 조건
# ---------------------------------

def get_socratic_prompt_file(
    condition: str
) -> str:

    prompt_map = {
        "soc_pure": "soc_pure.txt",
        "soc_add": "soc_add.txt",
    }

    if condition not in prompt_map:
        raise ValueError(
            f"Unknown Socratic condition: {condition}"
        )

    return prompt_map[condition]


def get_dean_prompt_file(
    condition: str
) -> str:

    prompt_map = {
        "soc_pure": "dean_pure.txt",
        "soc_add": "dean_add.txt",
    }

    if condition not in prompt_map:
        raise ValueError(
            f"Unknown Dean condition: {condition}"
        )

    return prompt_map[condition]


def run_socratic_module(
    request: ChatRequest,
    prompt_file: str,
    previous_questions: list[str],
    conversation_history: list[dict],
    dean_review: dict | None = None,
    generation_mode: str = "normal",
    rejected_socratic_questions: list[str] | None = None,
) -> dict:

    instructions = load_prompt(
        prompt_file
    )

    payload = {
        "topic": request.topic,
        "user_response": request.user_message,
        "turn_number": request.turn_number,
        "previous_socratic_questions": previous_questions,
        "conversation_history": conversation_history,
        "dean_review": dean_review or {},
        "generation_mode": generation_mode,
        "rejected_socratic_questions": (
            rejected_socratic_questions or []
        ),
    }

    return call_model_json(
        instructions=instructions,
        payload=payload,
    )


def run_dean_module(
    request: ChatRequest,
    dean_prompt_file: str,
    previous_questions: list[str],
    conversation_history: list[dict],
    socratic_output: dict,
) -> dict:

    instructions = load_prompt(
        dean_prompt_file
    )

    payload = {
        "topic": request.topic,
        "user_response": request.user_message,
        "previous_socratic_questions": previous_questions,
        "conversation_history": conversation_history,
        "socratic_module_output": socratic_output,
        "socratic_question": socratic_output.get(
            "socratic_question",
            "",
        ),
        "scaffolding_condition": request.condition,
        "scaffolding_stage": socratic_output.get(
            "scaffolding_stage",
            "none",
        ),
    }

    return call_model_json(
        instructions=instructions,
        payload=payload,
    )


def generate_socratic_reply(
    request: ChatRequest
) -> tuple[str, dict]:

    socratic_prompt_file = (
        get_socratic_prompt_file(
            request.condition
        )
    )

    dean_prompt_file = (
        get_dean_prompt_file(
            request.condition
        )
    )

    previous_questions = (
        get_previous_socratic_questions(
            request
        )
    )

    conversation_history = (
        build_conversation_history(
            request
        )
    )

    # ---------------------------------
    # 1. Socratic Module 최초 생성
    # ---------------------------------

    first_socratic = run_socratic_module(
        request=request,
        prompt_file=socratic_prompt_file,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        generation_mode="normal",
    )

    first_question = first_socratic.get(
        "socratic_question",
        "",
    )

    if (
        not isinstance(first_question, str)
        or not first_question.strip()
    ):
        raise HTTPException(
            status_code=502,
            detail=(
                "The first Socratic question "
                "was missing."
            ),
        )

    first_question = first_question.strip()

    # ---------------------------------
    # 2. 첫 번째 Dean 검토
    # ---------------------------------

    first_dean_output = run_dean_module(
        request=request,
        dean_prompt_file=dean_prompt_file,
        previous_questions=previous_questions,
        conversation_history=conversation_history,
        socratic_output=first_socratic,
    )

    first_dean_decision = str(
        first_dean_output.get(
            "decision",
            "",
        )
    ).strip().lower()

    # ------------ 여기
    if FORCE_FALLBACK_TEST:
        first_dean_decision = "regenerate"

    
    # ---------------------------------
    # 3. Dean 1이 ok이면 바로 사용
    # ---------------------------------

    if first_dean_decision == "ok":

        final_socratic = first_socratic
        final_question = first_question

        second_socratic = None
        second_dean_output = None
        second_dean_decision = None

        fallback_used = False
        fallback_socratic = None

    # ---------------------------------
    # 4. Dean 1이 reject하면
    #    Socratic Module 두 번째 생성
    # ---------------------------------

    else:

        first_dean_review = {
            "decision": first_dean_decision,
            "failure_types": first_dean_output.get(
                "failure_types",
                [],
            ),
            "failure_reason": first_dean_output.get(
                "failure_reason",
                "",
            ),
            "rejected_question": first_question,
        }

        second_socratic = run_socratic_module(
            request=request,
            prompt_file=socratic_prompt_file,
            previous_questions=previous_questions,
            conversation_history=conversation_history,
            dean_review=first_dean_review,
            generation_mode="normal",
        )

        second_question = second_socratic.get(
            "socratic_question",
            "",
        )

        if (
            not isinstance(second_question, str)
            or not second_question.strip()
        ):
            raise HTTPException(
                status_code=502,
                detail=(
                    "The second Socratic question "
                    "was missing."
                ),
            )

        second_question = second_question.strip()

        # ---------------------------------
        # 5. 두 번째 Dean 검토
        # ---------------------------------

        second_dean_output = run_dean_module(
            request=request,
            dean_prompt_file=dean_prompt_file,
            previous_questions=previous_questions,
            conversation_history=conversation_history,
            socratic_output=second_socratic,
        )

        second_dean_decision = str(
            second_dean_output.get(
                "decision",
                "",
            )
        ).strip().lower()

        #------------여기
        if FORCE_FALLBACK_TEST:
            first_dean_decision = "regenerate"



        
        # ---------------------------------
        # 6. Dean 2가 ok이면
        #    두 번째 질문 사용
        # ---------------------------------

        if second_dean_decision == "ok":

            final_socratic = second_socratic
            final_question = second_question

            fallback_used = False
            fallback_socratic = None

        # ---------------------------------
        # 7. Dean 2도 reject하면
        #    Socratic fallback 생성
        # ---------------------------------

        else:

            fallback_socratic = run_socratic_module(
                request=request,
                prompt_file=socratic_prompt_file,
                previous_questions=previous_questions,
                conversation_history=conversation_history,
                dean_review={},
                generation_mode="fallback",
                rejected_socratic_questions=[
                    first_question,
                    second_question,
                ],
            )

            fallback_question = (
                fallback_socratic.get(
                    "socratic_question",
                    "",
                )
            )

            if (
                not isinstance(fallback_question, str)
                or not fallback_question.strip()
            ):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "The fallback Socratic "
                        "question was missing."
                    ),
                )

            final_socratic = fallback_socratic
            final_question = (
                fallback_question.strip()
            )

            fallback_used = True

    # ---------------------------------
    # 8. 최종 로그 저장
    # ---------------------------------

    module_record = {
        "turn_number": request.turn_number,
        "condition": request.condition,
        "previous_socratic_questions": (
            previous_questions
        ),

        "first_socratic_output": (
            first_socratic
        ),
        "first_dean_output": (
            first_dean_output
        ),
        "first_dean_decision": (
            first_dean_decision
        ),

        "second_socratic_output": (
            second_socratic
        ),
        "second_dean_output": (
            second_dean_output
        ),
        "second_dean_decision": (
            second_dean_decision
        ),

        "fallback_used": (
            fallback_used
        ),
        "fallback_socratic_output": (
            fallback_socratic
        ),

        "final_socratic_output": (
            final_socratic
        ),
        "final_reply": (
            final_question
        ),

        # 기존 로그 필드와의 호환성을 위해 유지
        "dean_output": (
            first_dean_output
        ),
        "dean_decision": (
            first_dean_decision
        ),
        "was_revised": (
            first_dean_decision != "ok"
        ),
    }

    return final_question, module_record


# ---------------------------------
# 채팅 엔드포인트
# ---------------------------------

@app.post(
    "/chat",
    response_model=ChatResponse,
)
def chat(
    request: ChatRequest
) -> ChatResponse:

    try:
        if request.condition == "control":
            reply, module_record = (
                generate_control_reply(
                    request
                )
            )

        else:
            reply, module_record = (
                generate_socratic_reply(
                    request
                )
            )

    except FileNotFoundError as error:
        print(
            "Prompt file error:",
            str(error),
        )

        raise HTTPException(
            status_code=500,
            detail="A prompt file was not found.",
        ) from error

    return ChatResponse(
        reply=reply,
        condition=request.condition,
        turn_number=request.turn_number,
        module_record=module_record,
    )
