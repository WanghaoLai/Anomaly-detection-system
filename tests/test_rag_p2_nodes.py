import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from llama_index.core import Document as LlamaIndexDocument  # noqa: E402
from llama_index.core.node_parser import NodeParser as LlamaIndexNodeParser  # noqa: E402
from llama_index.core.schema import NodeRelationship, TextNode  # noqa: E402

from services.rag.core.contracts import Document, SourceInfo  # noqa: E402
from services.rag.document.parsing import MarkdownNodeParser  # noqa: E402
from services.rag.document.splitting import (  # noqa: E402
    DEFAULT_EMBEDDING_SAFE_CHARS,
    PARSER_SCHEMA_VERSION,
)


class RagP2NodeParserTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceInfo(
            filename="server-manual.md",
            extension=".md",
            media_type="text/markdown",
            byte_size=4096,
            sha256="a" * 64,
            storage_key="raw/aa/server-manual.md",
            uploaded_at="2026-08-18T00:00:00+00:00",
        )

    def _document(self, text: str) -> Document:
        return Document(
            text=text,
            metadata={"filename": self.source.filename},
            document_id="stable-document-id",
            source=self.source,
        )

    def test_is_native_llamaindex_parser_and_emits_text_nodes(self):
        parser = MarkdownNodeParser(40, 4)
        self.assertIsInstance(parser, LlamaIndexNodeParser)
        native_document = LlamaIndexDocument(
            id_="native-doc",
            text="# 手册\n\n" + "异常检测配置。" * 30,
            metadata={"filename": "manual.md"},
        )

        nodes = parser.get_nodes_from_documents([native_document])

        self.assertGreater(len(nodes), 1)
        self.assertTrue(all(isinstance(node, TextNode) for node in nodes))
        self.assertTrue(all(node.ref_doc_id == "native-doc" for node in nodes))
        self.assertIn(NodeRelationship.NEXT, nodes[0].relationships)
        self.assertIn(NodeRelationship.PREVIOUS, nodes[-1].relationships)

    def test_every_node_has_stable_source_section_and_position(self):
        markdown = (
            "# 平台手册\n\n上传平台使用说明。\n\n"
            "## 服务器部署\n\n服务器启动前要核对环境变量。\n\n"
            "## 故障排查\n\n查看日志并保留异常时间。"
        )
        nodes = MarkdownNodeParser(45, 5).parse(self._document(markdown))

        self.assertGreaterEqual(len(nodes), 2)
        for index, node in enumerate(nodes):
            metadata = node.metadata
            self.assertEqual(len(node.node_id or ""), 64)
            self.assertEqual(metadata["chunk_index"], index)
            self.assertEqual(metadata["document_id"], "stable-document-id")
            self.assertEqual(metadata["source_filename"], self.source.filename)
            self.assertEqual(metadata["source_sha256"], self.source.sha256)
            self.assertEqual(metadata["source_uri"], self.source.storage_key)
            self.assertTrue(metadata["section_path"])
            self.assertGreaterEqual(metadata["char_start"], 0)
            self.assertGreaterEqual(metadata["char_end"], metadata["char_start"])
            self.assertGreaterEqual(metadata["line_start"], 1)
            self.assertGreaterEqual(metadata["line_end"], metadata["line_start"])
            self.assertIn("chars:", metadata["position"])
            self.assertIn("lines:", metadata["position"])
            self.assertEqual(metadata["parser_schema_version"], PARSER_SCHEMA_VERSION)
            self.assertIn(self.source.filename, metadata["citation_label"])
            self.assertEqual(metadata["source_node_id"], "stable-document-id")

    def test_fenced_and_shell_commands_are_never_split(self):
        fenced = (
            "```bash\n"
            "sudo systemctl daemon-reload\n"
            "sudo systemctl restart anomaly-api\n"
            "sudo systemctl status anomaly-api\n"
            "```"
        )
        shell = (
            "docker compose pull anomaly-api\n"
            "docker compose up -d anomaly-api\n"
            "docker compose logs --tail=200 anomaly-api"
        )
        markdown = (
            "# 部署\n\n执行前请备份并核对配置。\n\n"
            f"{fenced}\n\n## 容器\n\n{shell}\n\n执行后进行健康检查。"
        )

        nodes = MarkdownNodeParser(30, 3).parse(self._document(markdown))

        fenced_hits = [node for node in nodes if "systemctl" in node.text]
        shell_hits = [node for node in nodes if "docker compose" in node.text]
        self.assertEqual(len(fenced_hits), 1)
        self.assertEqual(len(shell_hits), 1)
        self.assertIn(fenced, fenced_hits[0].text)
        self.assertIn(shell, shell_hits[0].text)
        self.assertTrue(fenced_hits[0].metadata["protected"])
        self.assertTrue(shell_hits[0].metadata["protected"])

    def test_at_least_95_percent_nodes_are_in_target_range(self):
        sections = []
        for index in range(100):
            sections.append(
                f"## 操作步骤 {index}\n\n"
                f"这是工业异常检测平台的第 {index} 段配置说明。"
                "请根据服务器使用文档核对参数，保存后检查服务状态，"
                "并完整记录执行结果和异常时间。"
            )
        nodes = MarkdownNodeParser(120, 12).parse(
            self._document("# 平台运维手册\n\n" + "\n\n".join(sections))
        )

        in_range = sum(
            1 for node in nodes if node.metadata["within_target_range"]
        )
        self.assertGreaterEqual(in_range / len(nodes), 0.95)
        self.assertTrue(
            all(node.metadata["token_count"] <= 120 for node in nodes)
        )

    def test_same_version_rebuild_has_identical_ids_count_and_content(self):
        markdown = (
            "# 设置\n\n" + "配置项需要完整记录。" * 80
            + "\n\n## 验证\n\n" + "检查运行状态和日志。" * 60
        )

        first = MarkdownNodeParser(80, 8).parse(self._document(markdown))
        second = MarkdownNodeParser(80, 8).parse(self._document(markdown))

        self.assertEqual(len(first), len(second))
        self.assertEqual([node.node_id for node in first], [node.node_id for node in second])
        self.assertEqual([node.text for node in first], [node.text for node in second])
        self.assertEqual(
            [dict(node.metadata) for node in first],
            [dict(node.metadata) for node in second],
        )

    def test_oversized_protected_and_unbroken_text_are_embedding_safe(self):
        long_table = "| column |\n| --- |\n| " + ("formula_value " * 500) + "|"
        unbroken = "x" * (DEFAULT_EMBEDDING_SAFE_CHARS * 2 + 73)
        markdown = f"# Appendix\n\n{long_table}\n\n## Raw\n\n{unbroken}"

        first = MarkdownNodeParser(500, 50).parse(self._document(markdown))
        second = MarkdownNodeParser(500, 50).parse(self._document(markdown))

        self.assertGreater(len(first), 2)
        self.assertTrue(
            all(len(node.text) <= DEFAULT_EMBEDDING_SAFE_CHARS for node in first)
        )
        self.assertTrue(
            any(node.metadata["embedding_safe_fragment"] for node in first)
        )
        self.assertTrue(
            any(node.metadata["oversized_protected"] for node in first)
        )
        self.assertEqual([node.node_id for node in first], [node.node_id for node in second])


if __name__ == "__main__":
    unittest.main()
