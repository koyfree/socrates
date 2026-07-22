import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field


app = FastAPI(
    title="Socrates Chatbot API",
    version="0.2.0",
)


# 현재는 Qualtrics 연결 테스트를 위해 모든 출처를 허용합니다.
# 실제 실험 전에는 접근 제한과 추가 보안을 적용할 예정입니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


MODEL = "gpt-5.2-2025-12-11"


# 현재는 API 연결을 확인하기 위한 임시 프롬프트입니다.
# 연결 확인 후 기존 control 프롬프트 파일로 교체합니다.
CONTROL_TEST_PROMPT = """
You are a research chatbot helping a user talk about a controversial
social issue.

Respond in Korean.

Ask exactly one brief, natural, and neutral follow-up question.
Focus on understanding the user's position and the reason behind it.
Do not introduce an opposing viewpoint.
Do not challenge the user.
Do not provide information, advice, evaluation, or a solution.
Do not ask more than one question.
Keep the response concise and conversational.
""".strip()


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    condition: Literal["control", "soc_pure", "soc_add"]
    topic: str = Field(min_length=1)
    user_message: str = Field(min_length=1, max_length=4000)
    turn_number: int = Field(ge=1)

    conversation_history: list[ConversationMessage] = Field(
        default_factory=list
    )


class ChatResponse(BaseModel):
    reply: str
    condition: str
    turn_number: int


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


def generate_control_reply(request: ChatRequest) -> str:
    """
    Control 조건의 실제 OpenAI 응답을 생성합니다.
    """

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured.",
        )

    client = OpenAI(api_key=api_key)

    messages = [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in request.conversation_history
    ]

    # Qualtrics에서는 현재 사용자 메시지를
    # user_message로 별도 전달하므로 마지막에 추가합니다.
    messages.append(
        {
            "role": "user",
            "content": request.user_message,
        }
    )

    instructions = (
        CONTROL_TEST_PROMPT
        + "\n\n"
        + "Main discussion topic:\n"
        + request.topic
    )

    try:
        response = client.responses.create(
            model=MODEL,
            instructions=instructions,
            input=messages,
            max_output_tokens=250,
        )

    except Exception as error:
        # 상세 오류는 Vercel 로그에서만 확인하고
        # 참여자 화면에는 노출하지 않습니다.
        print(
            "OpenAI API error:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=502,
            detail="Failed to generate chatbot response.",
        ) from error

    reply = response.output_text.strip()

    if not reply:
        raise HTTPException(
            status_code=502,
            detail="The model returned an empty response.",
        )

    return reply


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """
    control은 실제 OpenAI 응답을 반환합니다.

    soc_pure와 soc_add는 아직 연결 전이므로
    고정된 테스트 응답을 반환합니다.
    """

    if request.condition == "control":
        reply = generate_control_reply(request)

    elif request.condition == "soc_pure":
        reply = (
            "고정 응답 테스트입니다. "
            "그 입장이 어떤 가정에 기반하고 있는지 "
            "생각해 보실 수 있을까요?"
        )

    else:
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
