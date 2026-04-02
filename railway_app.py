from fastapi import FastAPI, Response
from pydantic import BaseModel


class EnhanceRequest(BaseModel):
    text: str | None = None


app = FastAPI(title="Ghost Voice TTS Lite", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/enhance")
async def enhance(_: EnhanceRequest, response: Response) -> dict:
    response.headers["x-ghost-source"] = "ghost"
    return {
        "audioBase64": "test",
        "deltas": {
            "prosody": 42,
            "emotional_clarity": 67,
        },
    }