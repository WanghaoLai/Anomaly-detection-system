"""Keep the default test suite independent from remote vector stores."""

import os


# Set these before test modules import ``settings``.  Qdrant-specific tests
# explicitly opt back into Qdrant local mode with their own temporary path.
os.environ["AI_VECTOR_STORE_PROVIDER"] = "chroma"
os.environ["AI_QDRANT_MODE"] = "local"
os.environ["AI_RAG_RELEASE_SMOKE_REQUIRED"] = "false"
os.environ["AI_RAG_PHASE3_ROUTER_ENABLED"] = "false"
os.environ["AI_RAG_PHASE3_REWRITE_ENABLED"] = "false"
