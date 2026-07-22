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

# 연구 도중 모델 동작이 바뀌지 않도록 고정 스냅샷 사용
MODEL = "gpt-5.2-2025-12-11"


app = FastAPI(
    title="Socrates Chatbot API",
    version="0.3.0",
)


# 현재는 Qualtrics 연결 테스트를 위해 모든 출처를 허용합니다.
# 실제 실험 전에는 보안 설정을 추가할 예정입니다.
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
    """
    backend/prompts 폴더에서 프롬프트를 읽습니다.
    한 번 읽은 프롬프트는 메모리에 저장해 재사용합니다.
    """

    prompt_path = PROMPT_DIR / filename

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_path}"
        )

    return prompt_path.read_text(
        encoding="utf-8"
    ).strip()


def safe_json_loads(text: str) -> dict:
    """
    모델이 반환한 문자열에서 JSON을 추출합니다.

    ```json 코드 블록이나 앞뒤의 짧은 설명이
    섞이는 경우도 처리합니다.
    """

    cleaned = text.strip()

    cleaned = cleaned.replace(
        "```json",
        ""
    ).replace(
        "```JSON",
        ""
    ).replace(
        "```",
        ""
    ).strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if first_brace == -1 or last_brace == -1:
        raise ValueError(
            "The model response does not contain a JSON object."
        )

    json_text = cleaned[
        first_brace:last_brace + 1
    ]

    parsed = json.loads(json_text)

    if not isinstance(parsed, dict):
        raise ValueError(
            "The model response is not a JSON object."
        )

    return parsed


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured.",
        )

    return OpenAI(api_key=api_key)


def call_model_json(
    client: OpenAI,
    instructions: str,
    payload: dict,
) -> dict:
    """
    프롬프트 파일을 instructions로,
    대화 정보를 JSON payload로 전달합니다.
    """

    try:
        response = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=json.dumps(
                payload,
                ensure_ascii=False,
            ),
            max_output_tokens=1200,
        )

    except Exception as error:
        print(
            "OpenAI API error:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=502,
            detail="Failed to generate chatbot response.",
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


# ---------------------------------
# Control 조건
# ---------------------------------

def generate_control_reply(
    request: ChatRequest
) -> str:
    """
    backend/prompts/control.txt를 사용해
    실제 Control 응답을 생성합니다.
    """

    try:
        instructions = load_prompt(
            "control.txt"
        )

    except FileNotFoundError as error:
        print(str(error))

        raise HTTPException(
            status_code=500,
            detail="Control prompt file was not found.",
        ) from error

    payload = {
        "topic": request.topic,
        "user_response": request.user_message,
        "turn_number": request.turn_number,
        "conversation_history": [
            {
                "role": message.role,
                "content": message.content,
            }
            for message
            in request.conversation_history
        ],
    }

    client = get_openai_client()

    control_output = call_model_json(
        client=client,
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
            detail=(
                "The control response was missing "
                "from the model output."
            ),
        )

    return reply.strip()


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

    if request.condition == "control":
        reply = generate_control_reply(
            request
        )

    elif request.condition == "soc_pure":
        # 다음 단계에서 soc_pure.txt와 Dean을 연결합니다.
        reply = (
            "고정 응답 테스트입니다. "
            "그 입장이 어떤 가정에 기반하고 있는지 "
            "생각해 보실 수 있을까요?"
        )

    else:
        # 다음 단계에서 soc_add.txt와 Dean을 연결합니다.
        reply = (
            "고정 응답 테스트입니다. "
            "다른 관점에서는 어떤 우려를 제기할 수 있다고 "
            "생각하시나요?"
        )

    return ChatResponse(
        reply=reply,
        condition=request.condition,
        turn_number=request.turn_number,
    )
