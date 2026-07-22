from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(
    title="Socrates Chatbot API",
    version="0.1.0",
)


# 지금은 테스트를 위해 모든 출처를 허용합니다.
# 실제 배포 전에는 Qualtrics 주소만 허용하도록 좁힐 예정입니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    condition: Literal["control", "soc_pure", "soc_add"]
    topic: str = Field(min_length=1)
    user_message: str = Field(min_length=1, max_length=4000)
    turn_number: int = Field(ge=1)
    conversation_history: list[ConversationMessage] = []


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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """
    현재는 GPT를 호출하지 않고 고정된 테스트 답변을 반환합니다.
    다음 단계에서 condition별 프롬프트와 OpenAI 호출을 붙입니다.
    """

    test_replies = {
        "control": (
            "고정 응답 테스트입니다. "
            "그렇게 생각하게 된 이유를 조금 더 말씀해 주실 수 있나요?"
        ),
        "soc_pure": (
            "고정 응답 테스트입니다. "
            "그 입장이 어떤 가정에 기반하고 있는지 생각해 보실 수 있을까요?"
        ),
        "soc_add": (
            "고정 응답 테스트입니다. "
            "다른 관점에서는 어떤 우려를 제기할 수 있다고 생각하시나요?"
        ),
    }

    return ChatResponse(
        reply=test_replies[request.condition],
        condition=request.condition,
        turn_number=request.turn_number,
    )
