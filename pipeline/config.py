# pipeline/config.py
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL     = os.getenv("DATABASE_URL", "postgresql://localhost/teachcopilot")
RAG_TOP_K        = int(os.getenv("RAG_TOP_K", "3"))
RAG_MIN_SCORE    = float(os.getenv("RAG_MIN_SCORE", "0.5"))
LOG_LEVEL        = os.getenv("LOG_LEVEL", "INFO")
DEFAULT_CHILD_ID = os.getenv("DEFAULT_CHILD_ID", "")

# Embedding model: try multilingual (better for Russian), fallback to MiniLM
_EMBEDDING_PREFERRED = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDING_FALLBACK  = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL      = os.getenv("EMBEDDING_MODEL", "auto")

# Device for embedding model: "auto" detects CUDA at runtime, or set "cuda"/"cpu" explicitly
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")

# Profile Agent — uses SEPARATE LLM endpoint (not Open WebUI proxy)
PROFILE_LLM_API_URL = os.getenv("PROFILE_LLM_API_URL", "http://127.0.0.1:1234/v1")
PROFILE_LLM_MODEL   = os.getenv("PROFILE_LLM_MODEL", "qwen/qwen3-vl-4b")
PROFILE_UPDATE_INTERVAL = int(os.getenv("PROFILE_UPDATE_INTERVAL", "5"))
