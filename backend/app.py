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

REASONING_EFFORT = "low"
VERBOSITY = "low"
MAX_OUTPUT_TOKENS = 1600

# ---------------------------------
# 사전테스트용 모델 설정
# ---------------------------------
#
# model_key는 프론트에서 gpt / claude / gemini / deepseek 중 하나를 보냅니다.
# 실제 API model id는 여기 한 곳에서 관리합니다.
# 사전테스트 전에 각 provider에서 사용할 정확한 model id를 최종 확정하세요.
# 환경변수로 model id를 덮어쓸 수도 있습니다.

MODEL_CONFIGS = {
    "gpt": {
        "provider": "openai",
        "model_id": os.getenv(
            "OPENAI_MODEL",
            "gpt-5.2-2025-12-11",
        ),
    },
    "claude": {
        "provider": "anthropic",
        "model_id": os.getenv(
            "ANTHROPIC_MODEL",
            "claude-sonnet-5",
        ),
    },
    "gemini": {
        "provider": "google",
        "model_id": os.getenv(
            "GEMINI_MODEL",
            "gemini-3.1-pro-preview",
        ),
    },
    "deepseek": {
        "provider": "deepseek",
        "model_id": os.getenv(
            "DEEPSEEK_MODEL",
            "deepseek-v4-pro",
        ),
    },
}

# 테스트용:
# True이면 Dean 1과 Dean 2를 강제로 reject 처리해서
# fallback 경로가 정상 작동하는지 확인합니다.
# 실제 실험 전에는 반드시 False로 바꾸세요.
FORCE_FALLBACK_TEST = False


app = FastAPI(
    title="Socrates Chatbot API",
    version="0.5.0",
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

ModelKey = Literal[
    "gpt",
    "claude",
    "gemini",
    "deepseek",
]


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

    # 기존 프론트와의 호환성을 위해 기본값은 gpt입니다.
    # 모델 선택 UI가 model_key를 보내면 해당 모델로 전체 파이프라인이 실행됩니다.
    model_key: ModelKey = "gpt"

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

    # 어떤 모델이 실제로 호출되었는지 프론트에서도 확인 가능
    model_key: str
    model_provider: str
    model_id: str

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
def health_check() -> dict:
    return {
        "status": "ok",
        "available_model_keys": list(MODEL_CONFIGS.keys()),
    }


# ---------------------------------
# 공통 함수: prompt / model config
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


def get_model_config(model_key: str) -> dict[str, str]:
    config = MODEL_CONFIGS.get(model_key)

    if config is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model_key: {model_key}",
        )

    return config


# ---------------------------------
# 공통 함수: provider client
# ---------------------------------

@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    return OpenAI(api_key=api_key)


@lru_cache(maxsize=1)
def get_anthropic_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured."
        )

    try:
        from anthropic import Anthropic
    except ImportError as error:
        raise RuntimeError(
            "The anthropic package is not installed. "
            "Run: pip install -U anthropic"
        ) from error

    return Anthropic(api_key=api_key)


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    try:
        from google import genai
    except ImportError as error:
        raise RuntimeError(
            "The google-genai package is not installed. "
            "Run: pip install -U google-genai"
        ) from error

    return genai.Client(api_key=api_key)


@lru_cache(maxsize=1)
def get_deepseek_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not configured."
        )

    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


# ---------------------------------
# 공통 함수: JSON parsing
# ---------------------------------

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


# ---------------------------------
# provider별 실제 API 호출
# ---------------------------------

def call_openai_text(
    instructions: str,
    payload_text: str,
    model_id: str,
) -> str:
    client = get_openai_client()

    response = client.responses.create(
        model=model_id,
        instructions=instructions,
        input=payload_text,
        reasoning={
            "effort": REASONING_EFFORT
        },
        text={
            "verbosity": VERBOSITY
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    return response.output_text.strip()


def call_anthropic_text(
    instructions: str,
    payload_text: str,
    model_id: str,
) -> str:
    client = get_anthropic_client()

    response = client.messages.create(
        model=model_id,
        system=instructions,
        messages=[
            {
                "role": "user",
                "content": payload_text,
            }
        ],
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking={
            "type": "adaptive"
        },
        output_config={
            "effort": REASONING_EFFORT
        },
    )

    text_parts = []

    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text:
                text_parts.append(text)

    return "\n".join(text_parts).strip()


def call_gemini_text(
    instructions: str,
    payload_text: str,
    model_id: str,
) -> str:
    client = get_gemini_client()

    # Google은 2026년 기준 새 개발에 Interactions API 사용을 권장합니다.
    interaction = client.interactions.create(
        model=model_id,
        system_instruction=instructions,
        input=payload_text,
        generation_config={
            "thinking_level": REASONING_EFFORT,
        },
    )

    return (interaction.output_text or "").strip()


def call_deepseek_text(
    instructions: str,
    payload_text: str,
    model_id: str,
) -> str:
    client = get_deepseek_client()

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "system",
                "content": instructions,
            },
            {
                "role": "user",
                "content": payload_text,
            },
        ],
        max_tokens=6000,
        response_format={
            "type": "json_object"
        },
        stream=False,
        extra_body={
            "thinking": {
                "type": "disabled"
            }
        },
    )

    choice = response.choices[0]
    message = choice.message

    print("DEEPSEEK finish_reason:", choice.finish_reason)
    print("DEEPSEEK content:", repr(message.content))

    reasoning_content = getattr(
        message,
        "reasoning_content",
        None
    )

    print(
        "DEEPSEEK reasoning length:",
        len(reasoning_content or "")
    )
    print("DEEPSEEK usage:", response.usage)

    content = message.content
    return (content or "").strip()

def call_model_json(
    instructions: str,
    payload: dict,
    model_key: ModelKey,
) -> dict:
    config = get_model_config(model_key)
    provider = config["provider"]
    model_id = config["model_id"]

    payload_text = json.dumps(
        payload,
        ensure_ascii=False,
    )

    try:
        if provider == "openai":
            raw_text = call_openai_text(
                instructions=instructions,
                payload_text=payload_text,
                model_id=model_id,
            )

        elif provider == "anthropic":
            raw_text = call_anthropic_text(
                instructions=instructions,
                payload_text=payload_text,
                model_id=model_id,
            )

        elif provider == "google":
            raw_text = call_gemini_text(
                instructions=instructions,
                payload_text=payload_text,
                model_id=model_id,
            )

        elif provider == "deepseek":
            raw_text = call_deepseek_text(
                instructions=instructions,
                payload_text=payload_text,
                model_id=model_id,
            )

        else:
            raise RuntimeError(
                f"Unsupported provider: {provider}"
            )

    except HTTPException:
        raise

    except Exception as error:
        print(
            f"{provider} API error:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=502,
            detail=(
                f"Failed to generate model response "
                f"with {model_key}."
            ),
        ) from error

    if not raw_text:
        raise HTTPException(
            status_code=502,
            detail=(
                f"The {model_key} model returned "
                "an empty response."
            ),
        )

    try:
        return safe_json_loads(raw_text)

    except Exception as error:
        print(
            f"JSON parsing error ({model_key}):",
            type(error).__name__,
            str(error),
        )

        print(
            f"Raw {model_key} output:",
            raw_text,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                f"The {model_key} model returned "
                "invalid JSON."
            ),
        ) from error


# ---------------------------------
# 대화 기록 함수
# ---------------------------------

def build_conversation_history(
    request: ChatRequest
) -> list[dict]:
    """
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


def get_model_log_info(
    request: ChatRequest
) -> dict[str, str]:
    config = get_model_config(request.model_key)

    return {
        "model_key": request.model_key,
        "model_provider": config["provider"],
        "model_id": config["model_id"],
    }


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
        model_key=request.model_key,
    )

    reply = control_output.get(
        "control_response",
        ""
    )

    if (
        not isinstance(reply, str)
        or not reply.strip()
    ):
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

    reply = reply.strip()

    module_record = {
        "turn_number": request.turn_number,
        "condition": request.condition,
        **get_model_log_info(request),
        "prompt_file": "control.txt",
        "control_output": control_output,
        "final_reply": reply,
    }

    return reply, module_record


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
        model_key=request.model_key,
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
        model_key=request.model_key,
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

    # 테스트 중에는 첫 번째 Dean을 강제로 reject 처리
    if FORCE_FALLBACK_TEST:
        first_dean_decision = "regenerate"

    # ---------------------------------
    # 3. Dean 1이 ok이면 최초 질문 사용
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

        # 테스트 중에는 두 번째 Dean도 강제로 reject 처리
        if FORCE_FALLBACK_TEST:
            second_dean_decision = "regenerate"

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
    # 테스트용 표시
    # ---------------------------------

    if FORCE_FALLBACK_TEST and fallback_used:
        final_question = (
            "[FALLBACK TEST] "
            + final_question
        )

    # ---------------------------------
    # 8. 최종 로그 저장
    # ---------------------------------

    module_record = {
        "turn_number": request.turn_number,
        "condition": request.condition,
        **get_model_log_info(request),
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

    model_info = get_model_log_info(request)

    return ChatResponse(
        reply=reply,
        condition=request.condition,
        turn_number=request.turn_number,
        model_key=model_info["model_key"],
        model_provider=model_info["model_provider"],
        model_id=model_info["model_id"],
        module_record=module_record,
    )
