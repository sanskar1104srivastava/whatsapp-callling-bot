"""Local-only runner: `uvicorn dev_main:app --port 8101 --reload`. Not copied into Rekon-AI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai_calling import router

app = FastAPI(title="ai-calling (local)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
