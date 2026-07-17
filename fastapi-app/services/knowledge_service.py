"""知识库服务 - 文档解析、分块、向量化、ChromaDB 存储与检索"""
import asyncio
import logging
import os
import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings
from dashscope import TextEmbedding
from langchain_text_splitters import RecursiveCharacterTextSplitter

from settings import AI_CONFIG

logger = logging.getLogger(__name__)

CHROMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")

DOC_COLLECTION = "knowledge_base"


class KnowledgeService:
    def __init__(self, embedding_model: str = None):
        self.embedding_model = embedding_model or AI_CONFIG.get("embedding_model", "text-embedding-v2")
        self._client = None
        self._collection = None
        self._text_splitter = None

    @property
    def client(self):
        if self._client is None:
            os.makedirs(CHROMA_PATH, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=CHROMA_PATH, settings=ChromaSettings(anonymized_telemetry=False)
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(name=DOC_COLLECTION)
        return self._collection

    @property
    def text_splitter(self):
        if self._text_splitter is None:
            self._text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,
                separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""],
            )
        return self._text_splitter

    # ==================== 文档知识库 ====================

    def parse_file(self, file_bytes: bytes, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".txt":
            return file_bytes.decode("utf-8", errors="ignore")
        elif ext == ".pdf":
            return self._parse_pdf(file_bytes)
        elif ext == ".docx":
            return self._parse_docx(file_bytes)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def _parse_pdf(self, file_bytes: bytes) -> str:
        from PyPDF2 import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(file_bytes))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n".join(text_parts)

    def _parse_docx(self, file_bytes: bytes) -> str:
        from docx import Document
        from io import BytesIO
        doc = Document(BytesIO(file_bytes))
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)
        return "\n".join(text_parts)

    def split_text(self, text: str) -> list:
        return self.text_splitter.split_text(text)

    def _get_embeddings(self, texts: list) -> list:
        embeddings = []
        batch_size = 25
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = TextEmbedding.call(model=self.embedding_model, input=batch)
            if response.status_code == 200:
                for item in response.output["embeddings"]:
                    embeddings.append(item["embedding"])
            else:
                raise Exception(f"Embedding 调用失败: {response.message}")
        return embeddings

    def add_document(self, file_bytes: bytes, filename: str) -> dict:
        text = self.parse_file(file_bytes, filename)
        if not text.strip():
            raise ValueError("文档内容为空，无法解析")

        chunks = self.split_text(text)
        if not chunks:
            raise ValueError("文档分块后无有效内容")

        embeddings = self._get_embeddings(chunks)
        doc_uuid = uuid.uuid4().hex
        ids = [f"{doc_uuid}_{i}" for i in range(len(chunks))]
        metadatas = [{"doc_id": doc_uuid, "filename": filename, "chunk_index": i, "type": "document"}
                     for i in range(len(chunks))]

        self.collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

        return {"doc_id": doc_uuid, "chunk_count": len(chunks), "file_size": len(file_bytes)}

    def delete_document(self, doc_id: str):
        results = self.collection.get(where={"doc_id": doc_id})
        if results.get("ids"):
            self.collection.delete(ids=results["ids"])

    # ==================== 统一检索 ====================

    def _query_collection(self, col, query: str, top_k: int, source_label: str) -> list:
        query_embedding = self._get_embeddings([query])
        results = col.query(query_embeddings=query_embedding, n_results=top_k)

        docs = []
        if results.get("documents") and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                docs.append({
                    "content": doc,
                    "source": source_label,
                    "filename": meta.get("filename", meta.get("name", "")),
                    "score": float(results["distances"][0][i]) if results.get("distances") else 0,
                })
        return docs

    def search_documents(self, query: str, top_k: int = 3) -> list:
        """仅检索文档知识库"""
        return self._query_collection(self.collection, query, top_k, "knowledge_base")

    def search(self, query: str, top_k: int = 3) -> list:
        """检索文档知识库"""
        return self.search_documents(query, top_k)

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        doc_count = self.collection.count()
        return {"document_chunks": doc_count, "total_chunks": doc_count}


knowledge_service = KnowledgeService()
