import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field


# ---------------------------------
# 기본 설정
# ---------------------------------

BASE_DIR = Path(__file__).resolve().parent

PROMPT_FILE = (
    BASE_DIR / "participant_simulator.txt"
)

MODEL = "gpt-5.2-2025-12-11"
REASONING_EFFORT = "low"
VERBOSITY = "low"
MAX_OUTPUT_TOKENS = 300


app = FastAPI(
    title="Socrates Participant Simulator",
    version="0.1.0",
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
    role: str
    content: str = Field(min_length=1)


class SimulatorRequest(BaseModel):
    topic: str = Field(min_length=1)

    initial_position: str = Field(
        min_length=1
    )

    conversation_history: list[
        ConversationMessage
    ] = Field(
        default_factory=list
    )

    latest_question: str = Field(
        min_length=1
    )


class SimulatorResponse(BaseModel):
    participant_response: str
    simulator_model: str


# ---------------------------------
# 기본 엔드포인트
# ---------------------------------

@app.get("/")
def root():
    return {
        "message":
            "Socrates Participant Simulator is running."
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# ---------------------------------
# OpenAI client
# ---------------------------------

@lru_cache(maxsize=1)
def get_openai_client():
    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    return OpenAI(
        api_key=api_key
    )


# ---------------------------------
# Prompt
# ---------------------------------

@lru_cache(maxsize=1)
def load_simulator_prompt():
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Prompt file not found: "
            f"{PROMPT_FILE}"
        )

    return PROMPT_FILE.read_text(
        encoding="utf-8"
    ).strip()


def format_history(
    conversation_history
):
    if not conversation_history:
        return "(아직 이전 대화 없음)"

    lines = []

    for message in conversation_history:

        if message.role == "assistant":
            label = "Chatbot"

        elif message.role == "user":
            label = "Participant"

        else:
            label = message.role

        lines.append(
            f"{label}: {message.content}"
        )

    return "\n".join(lines)


# ---------------------------------
# Participant 생성
# ---------------------------------

def generate_participant_response(
    request: SimulatorRequest
):
    prompt_template = (
        load_simulator_prompt()
    )

    conversation_text = (
        format_history(
            request.conversation_history
        )
    )

    rendered_prompt = (
        prompt_template
        .replace(
            "{topic}",
            request.topic,
        )
        .replace(
            "{initial_position}",
            request.initial_position,
        )
        .replace(
            "{conversation_history}",
            conversation_text,
        )
        .replace(
            "{latest_question}",
            request.latest_question,
        )
    )

    try:
        client = get_openai_client()

        response = (
            client.responses.create(
                model=MODEL,

                instructions=(
                    rendered_prompt
                ),

                input=(
                    "Generate the participant's "
                    "response now. "
                    "Return only the "
                    "participant's response."
                ),

                reasoning={
                    "effort":
                        REASONING_EFFORT
                },

                text={
                    "verbosity":
                        VERBOSITY
                },

                max_output_tokens=(
                    MAX_OUTPUT_TOKENS
                ),
            )
        )

    except Exception as error:
        print(
            "OpenAI API error:",
            type(error).__name__,
            str(error),
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to generate "
                "participant response."
            ),
        ) from error

    participant_response = (
        response.output_text.strip()
    )

    if not participant_response:
        raise HTTPException(
            status_code=502,
            detail=(
                "The simulator returned "
                "an empty response."
            ),
        )

    return participant_response


# ---------------------------------
# Simulator endpoint
# ---------------------------------

@app.post(
    "/simulate",
    response_model=SimulatorResponse,
)
def simulate(
    request: SimulatorRequest
):
    try:
        participant_response = (
            generate_participant_response(
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
            detail=(
                "participant_simulator.txt "
                "was not found."
            ),
        ) from error

    return SimulatorResponse(
        participant_response=(
            participant_response
        ),
        simulator_model=MODEL,
    )
