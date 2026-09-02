import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

import services.knowledge_service as knowledge_module  # noqa: E402
from services.knowledge_service import (  # noqa: E402
    INGESTION_SCHEMA_VERSION,
    KnowledgeService,
    _approx_token_len,
    _chunk_paragraphs,
    _preprocess_pdf_markdown,
    _split_paragraphs_with_headings,
)


class FakeMarkdownConverter:
    def __init__(self, markdown="# 标题\n\nMarkItDown 内容"):
        self.markdown = markdown
        self.calls = []

    def convert_stream(self, stream, **kwargs):
        self.calls.append({"bytes": stream.read(), "kwargs": kwargs})
        return SimpleNamespace(markdown=self.markdown)


class FakeCollection:
    def __init__(self):
        self.add_payload = None
        self.metadata = {}

    def add(self, **kwargs):
        self.add_payload = kwargs


class FakeSearchCollection:
    def __init__(self):
        self.query_payload = None

    def count(self):
        return 2

    def query(self, **kwargs):
        self.query_payload = kwargs
        return {
            "documents": [["第一段", "第二段"]],
            "metadatas": [[
                {"doc_id": "doc-a", "filename": "a.md", "heading_path": "Root"},
                {"filename": "b.md"},
            ]],
            "distances": [[0.1, 0.4]],
        }


class KnowledgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.config_patch = patch.dict(
            knowledge_module.AI_CONFIG,
            {
                "vector_store_provider": "chroma",
                "qdrant_mode": "local",
                "rag_release_smoke_required": False,
            },
            clear=False,
        )
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()

    def test_duplicate_detection_requires_current_ingestion_schema(self):
        snapshot = {
            "ids": ["chunk-1"],
            "metadatas": [{"doc_id": "doc-1", "content_hash": "same"}],
        }

        self.assertFalse(KnowledgeService._snapshot_is_same_content(snapshot, "same"))
        snapshot["metadatas"][0]["ingestion_schema_version"] = INGESTION_SCHEMA_VERSION
        self.assertTrue(KnowledgeService._snapshot_is_same_content(snapshot, "same"))

    def test_search_consistency_validation_is_cached_per_release(self):
        service = KnowledgeService(markdown_converter=FakeMarkdownConverter())
        active = {"release_id": "release-1"}
        service.current_release_id = Mock(
            side_effect=lambda: active["release_id"]
        )
        service.active_vector_store_provider = Mock(return_value="chroma")
        service.validate_embedding_config = Mock(return_value={
            "consistent": True,
            "issues": [],
        })

        self.assertTrue(service._ensure_consistent_or_warn("search-1"))
        self.assertTrue(service._ensure_consistent_or_warn("search-2"))
        service.validate_embedding_config.assert_called_once_with()

        active["release_id"] = "release-2"
        self.assertTrue(service._ensure_consistent_or_warn("search-3"))
        self.assertEqual(service.validate_embedding_config.call_count, 2)

    def test_pdf_preprocessing_removes_repeated_boundaries_and_detects_titles(self):
        markdown = """实验室手册
1. 接入流程
第一页正文。
内部资料
第 1 页
实验室手册
2. Windows 配置
第二页正文。
内部资料
第 2 页
实验室手册
2.1 安装客户端
第三页正文。
内部资料
第 3 页"""

        cleaned, diagnostics = _preprocess_pdf_markdown(markdown)

        self.assertEqual(diagnostics["page_count"], 3)
        self.assertEqual(diagnostics["page_markers_removed"], 3)
        self.assertEqual(diagnostics["headers_removed"], 2)
        self.assertEqual(diagnostics["footers_removed"], 3)
        self.assertEqual(diagnostics["detected_title_count"], 3)
        self.assertEqual(cleaned.count("实验室手册"), 1)
        self.assertNotIn("内部资料", cleaned)
        self.assertIn("# 1. 接入流程", cleaned)
        self.assertIn("## 2.1 安装客户端", cleaned)

    def test_pdf_title_recognition_does_not_promote_numbered_sentences(self):
        markdown = """1. 安装客户端并重新启动服务。

3. 故障排查

正文"""

        cleaned, diagnostics = _preprocess_pdf_markdown(markdown)

        self.assertTrue(cleaned.startswith("1. 安装客户端并重新启动服务。"))
        self.assertIn("# 3. 故障排查", cleaned)
        self.assertEqual(diagnostics["detected_title_count"], 1)

    def test_pdf_preview_does_not_create_embeddings_or_write_collection(self):
        service = KnowledgeService(
            markdown_converter=FakeMarkdownConverter("1. 接入流程\n\n正文内容"),
        )
        service._get_embeddings = Mock()

        preview = service.preview_document(b"pdf bytes", "manual.pdf")

        self.assertEqual(preview["extension"], ".pdf")
        self.assertEqual(preview["diagnostics"]["detected_title_count"], 1)
        self.assertEqual(preview["diagnostics"]["chunk_count"], 1)
        self.assertIn("# 1. 接入流程", preview["preview_markdown"])
        service._get_embeddings.assert_not_called()

    def test_approx_token_len_supports_mixed_chinese_and_english(self):
        # 中文字符和空白分词的估算会同时计入中文词组，保持与参考策略一致。
        self.assertEqual(_approx_token_len("工业 AI anomaly detection"), 6)
        self.assertEqual(_approx_token_len(""), 0)

    def test_split_paragraphs_tracks_heading_path_and_ignores_code_fences(self):
        markdown = (
            "# Root\n\n"
            "intro\n\n"
            "## Section\n\n"
            "body\n\n"
            "```python\n"
            "# not a heading\n"
            "print('x')\n"
            "```\n\n"
            "### Detail\n\n"
            "detail body"
        )

        paragraphs = _split_paragraphs_with_headings(markdown)

        self.assertEqual([p["heading_path"] for p in paragraphs], ["Root", "Root > Section", "Root > Section", "Root > Section > Detail"])
        self.assertEqual(paragraphs[0]["content"], "intro")
        self.assertIn("# not a heading", paragraphs[2]["content"])
        self.assertEqual(paragraphs[3]["heading_markdown"], "# Root\n## Section\n### Detail")

    def test_chunk_paragraphs_uses_complete_paragraph_overlap(self):
        paragraphs = [
            {"content": "甲乙", "start": 0, "end": 2, "heading_path": None},
            {"content": "丙丁", "start": 3, "end": 5, "heading_path": None},
            {"content": "戊己", "start": 6, "end": 8, "heading_path": None},
        ]

        chunks = _chunk_paragraphs(paragraphs, chunk_tokens=6, overlap_tokens=3)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["content"], "甲乙\n\n丙丁")
        self.assertEqual(chunks[1]["content"], "丙丁\n\n戊己")
        self.assertEqual(chunks[1]["paragraph_count"], 2)

    def test_convert_to_markdown_uses_binary_stream_and_filename_metadata(self):
        converter = FakeMarkdownConverter()
        service = KnowledgeService(markdown_converter=converter)

        result = service.convert_to_markdown(b"original bytes", "folder\\report.TXT")

        self.assertEqual(result, "# 标题\n\nMarkItDown 内容")
        self.assertEqual(converter.calls[0]["bytes"], b"original bytes")
        kwargs = converter.calls[0]["kwargs"]
        if "stream_info" in kwargs:
            self.assertEqual(kwargs["stream_info"].filename, "report.TXT")
            self.assertEqual(kwargs["stream_info"].extension, ".txt")
        else:
            self.assertEqual(kwargs["file_extension"], ".txt")

    def test_add_document_sends_only_converted_markdown_to_rag_pipeline(self):
        converter = FakeMarkdownConverter("## converted heading\n\nconverted body")
        collection = FakeCollection()
        service = KnowledgeService(
            embedding_model="test-embedding",
            markdown_converter=converter,
            chunk_tokens=100,
            overlap_tokens=10,
        )
        service._collection = collection
        service._probed_dim = 2
        service.validate_embedding_config = Mock(return_value={"consistent": True})
        service._ensure_consistent_or_raise = Mock()
        service._get_embeddings = Mock(return_value=[[0.1, 0.2]])
        service.split_markdown = Mock(return_value=[{
            "content": "## converted heading\n\nconverted body",
            "heading_path": "converted heading",
            "token_count": 5,
            "start": 0,
            "end": 35,
            "paragraph_count": 1,
        }])

        info = service.add_document(b"not markdown", "manual.pdf")

        service.split_markdown.assert_called_once_with("## converted heading\n\nconverted body")
        service._get_embeddings.assert_called_once_with(["## converted heading\n\nconverted body"])
        self.assertEqual(collection.add_payload["documents"], ["## converted heading\n\nconverted body"])
        self.assertEqual(collection.add_payload["metadatas"][0]["content_format"], "markdown")
        self.assertEqual(collection.add_payload["metadatas"][0]["converter"], "markitdown")
        self.assertEqual(collection.add_payload["metadatas"][0]["source_extension"], ".pdf")
        self.assertEqual(collection.add_payload["metadatas"][0]["heading_path"], "converted heading")
        self.assertEqual(info["content_format"], "markdown")

    def test_embeddings_are_ordered_normalized_and_typed_as_documents(self):
        service = KnowledgeService(
            embedding_model="text-embedding-v2",
            embedding_batch_size=2,
            embedding_max_retries=0,
        )
        response = SimpleNamespace(
            status_code=200,
            output={
                "embeddings": [
                    {"text_index": 1, "embedding": [0, 3]},
                    {"text_index": 0, "embedding": [4, 0]},
                ]
            },
        )

        with patch("services.knowledge_service.TextEmbedding.call", return_value=response) as call:
            vectors = service._get_embeddings(["第一段", "第二段"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(call.call_args.kwargs["text_type"], "document")
        self.assertEqual(service._probed_dim, 2)

    def test_embeddings_retry_transient_failure_and_query_type(self):
        service = KnowledgeService(
            embedding_model="text-embedding-v2",
            embedding_max_retries=1,
            embedding_retry_backoff_seconds=0,
        )
        transient = SimpleNamespace(status_code=503, message="temporarily unavailable")
        success = SimpleNamespace(
            status_code=200,
            output={"embeddings": [{"text_index": 0, "embedding": [3, 4]}]},
        )

        with patch(
            "services.knowledge_service.TextEmbedding.call",
            side_effect=[transient, success],
        ) as call:
            vectors = service._get_embeddings(["查询"], text_type="query")

        self.assertEqual(vectors, [[0.6, 0.8]])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs["text_type"], "query")

    def test_search_clamps_top_k_and_exposes_cosine_similarity(self):
        service = KnowledgeService(embedding_model="text-embedding-v2")
        collection = FakeSearchCollection()
        service._collection = collection
        service._ensure_consistent_or_warn = Mock(return_value=True)
        service._get_embeddings = Mock(return_value=[[1.0, 0.0]])

        results = service.search_documents("查询", top_k=99)

        self.assertEqual(collection.query_payload["n_results"], 2)
        service._get_embeddings.assert_called_once_with(["查询"], text_type="query")
        self.assertAlmostEqual(results[0]["score"], 0.9)
        self.assertAlmostEqual(results[0]["distance"], 0.1)
        self.assertEqual(results[0]["doc_id"], "doc-a")
        self.assertEqual(results[0]["heading_path"], "Root")

    def test_list_document_chunks_reads_existing_chroma_without_embeddings(self):
        service = KnowledgeService(embedding_model="text-embedding-v2")
        collection = Mock()
        collection.get.return_value = {
            "documents": ["分块正文"],
            "metadatas": [{
                "doc_id": "doc-1",
                "filename": "manual.md",
                "chunk_index": 2,
                "heading_path": "排障",
            }],
        }
        service._collection = collection
        service._ensure_consistent_or_warn = Mock(return_value=True)

        chunks = service.list_document_chunks()

        self.assertEqual(chunks[0]["content"], "分块正文")
        self.assertEqual(chunks[0]["chunk_index"], 2)
        collection.get.assert_called_once_with(include=["documents", "metadatas"])

    def test_same_name_legacy_collection_is_rebuilt_before_upload(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 新版本\n\n新内容"),
                )
                legacy = service.client.get_or_create_collection(
                    name=knowledge_module.DOC_COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
                legacy.add(
                    ids=["legacy_0"],
                    embeddings=[[1.0, 0.0]],
                    documents=["旧内容"],
                    metadatas=[{
                        "doc_id": "legacy-doc",
                        "filename": "manual.pdf",
                        "chunk_index": 0,
                        "embedding_model": "text-embedding-v2",
                        "embedding_dim": 2,
                    }],
                )
                service._get_embeddings = Mock(return_value=[[0.6, 0.8]])

                info = service.add_document(b"new pdf bytes", "manual.pdf")

                self.assertTrue(info["replaced_existing"])
                self.assertEqual(service.collection.count(), 1)
                self.assertEqual(service.collection.get()["ids"], [f"{info['doc_id']}_0"])
                self.assertEqual(
                    service.collection.metadata["embedding_schema_version"],
                    knowledge_module.EMBEDDING_SCHEMA_VERSION,
                )

    def test_empty_collection_with_stale_dimension_is_recreated(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 新文档\n\n内容"),
                )
                seeded = service.client.get_or_create_collection(
                    name=knowledge_module.DOC_COLLECTION,
                    metadata={
                        **knowledge_module.BASE_COLLECTION_METADATA,
                        "embedding_model": "text-embedding-v2",
                    },
                )
                seeded.add(
                    ids=["dimension_2"],
                    embeddings=[[1.0, 0.0]],
                    documents=["测试"],
                    metadatas=[{"filename": "old.md", "embedding_model": "text-embedding-v2"}],
                )
                seeded.delete(ids=["dimension_2"])
                service._get_embeddings = Mock(return_value=[[0.1, 0.2, 0.3]])

                info = service.add_document(b"new bytes", "new.md")

                self.assertEqual(info["chunk_count"], 1)
                stored = service.collection.get(include=["embeddings"])
                self.assertEqual(len(stored["embeddings"][0]), 3)

    def test_convert_to_markdown_rejects_unsupported_extensions_before_conversion(self):
        converter = FakeMarkdownConverter()
        service = KnowledgeService(markdown_converter=converter)

        with self.assertRaisesRegex(ValueError, "不支持的文件格式"):
            service.convert_to_markdown(b"content", "script.exe")

        self.assertEqual(converter.calls, [])

    def test_duplicate_upload_reuses_existing_document_without_reembedding(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 手册\n\n相同内容"),
                )
                service._get_embeddings = Mock(return_value=[[1.0, 0.0]])

                first = service.add_document(b"same file", "manual.md")
                service.complete_replacement(first["doc_id"])
                second = service.add_document(b"same file", "manual.md")

                self.assertTrue(second["unchanged"])
                self.assertFalse(second["replaced_existing"])
                self.assertEqual(second["doc_id"], first["doc_id"])
                self.assertEqual(service.collection.count(), 1)
                self.assertEqual(service._get_embeddings.call_count, 1)

    def test_same_name_changed_upload_can_rollback_to_old_vectors(self):
        import services.knowledge_service as knowledge_module

        converter = FakeMarkdownConverter("# 手册\n\n旧内容")
        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=converter,
                )
                service._get_embeddings = Mock(return_value=[[1.0, 0.0]])

                first = service.add_document(b"old file", "manual.md")
                service.complete_replacement(first["doc_id"])
                converter.markdown = "# 手册\n\n新内容"
                second = service.add_document(b"new file", "manual.md")

                self.assertTrue(second["replaced_existing"])
                self.assertEqual(service.collection.count(), 1)
                self.assertIn("新内容", service.collection.get()["documents"][0])

                self.assertTrue(service.rollback_replacement(second["doc_id"]))
                restored = service.collection.get(include=["documents", "metadatas"])
                self.assertEqual(restored["metadatas"][0]["doc_id"], first["doc_id"])
                self.assertIn("旧内容", restored["documents"][0])

    def test_delete_preflight_mismatch_keeps_vectors_unchanged(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 手册\n\n内容"),
                )
                service._get_embeddings = Mock(return_value=[[1.0, 0.0]])
                info = service.add_document(b"file", "manual.md")

                with self.assertRaisesRegex(ValueError, "分块数量不一致"):
                    service.snapshot_document(info["doc_id"], expected_count=2)
                with self.assertRaisesRegex(ValueError, "分块数量不一致"):
                    service.delete_document(info["doc_id"], expected_count=2)

                self.assertEqual(service.collection.count(), 1)

    def test_deleted_document_snapshot_can_be_restored(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 手册\n\n内容"),
                )
                service._get_embeddings = Mock(return_value=[[1.0, 0.0]])
                info = service.add_document(b"file", "manual.md")
                snapshot = service.snapshot_document(info["doc_id"], expected_count=1)

                self.assertEqual(
                    service.delete_document(info["doc_id"], expected_count=1),
                    1,
                )
                self.assertEqual(service.collection.count(), 0)
                service.restore_document_snapshot(snapshot)
                self.assertEqual(service.collection.count(), 1)

    def test_reconcile_metadata_reports_healthy_and_mismatched_documents(self):
        import services.knowledge_service as knowledge_module

        with tempfile.TemporaryDirectory() as db_path:
            with patch.object(knowledge_module, "CHROMA_PATH", db_path):
                service = KnowledgeService(
                    embedding_model="text-embedding-v2",
                    markdown_converter=FakeMarkdownConverter("# 手册\n\n内容"),
                )
                service._get_embeddings = Mock(return_value=[[1.0, 0.0]])
                info = service.add_document(b"file", "manual.md")
                service.complete_replacement(info["doc_id"])
                sql_row = {
                    "id": 1,
                    "filename": info["doc_id"],
                    "original_name": "manual.md",
                    "chunk_count": 1,
                }

                healthy = service.reconcile_metadata([sql_row])
                mismatched = service.reconcile_metadata([
                    {**sql_row, "chunk_count": 2},
                ])

                self.assertTrue(healthy["healthy"])
                self.assertEqual(healthy["status"], "healthy")
                self.assertFalse(mismatched["healthy"])
                self.assertIn(
                    "chunk_count_mismatch",
                    [issue["code"] for issue in mismatched["issues"]],
                )


if __name__ == "__main__":
    unittest.main()
