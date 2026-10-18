from fastapi import FastAPI

app = FastAPI(
    title="Email Assistant API",
    version="1.0.0",
)


@app.get("/api/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "email-assistant-api",
    }
