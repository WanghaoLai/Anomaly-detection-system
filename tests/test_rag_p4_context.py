import re
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.rag.answering.context import (  # noqa: E402
    ContextPacker,
    ContextPackingPolicy,
)
from services.rag.document.splitting import approx_token_len  # noqa: E402


class RagP4ContextPackingTests(unittest.TestCase):
    @staticmethod
    def _result(index, content, **metadata):
        return {
            "node_id": f"node-{index}",
            "doc_id": "doc-1",
            "chunk_index": index,
            "filename": "服务器手册.md",
            "section_path": "GPU / 运行监控",
            "position": f"chars:{index * 100}-{(index + 1) * 100};lines:1-5",
            "content": content,
            "score": 1.0 - index / 10,
            **metadata,
        }

    @staticmethod
    def _packer(budget=240, minimum=16, maximum=80):
        return ContextPacker(ContextPackingPolicy(
            token_budget=budget,
            min_body_tokens=minimum,
            max_body_tokens=maximum,
        ))

    def test_context_never_exceeds_hard_budget(self):
        results = [
            self._result(index, f"第 {index} 条资料。" + "正文" * 300)
            for index in range(8)
        ]

        for budget in (80, 120, 240, 600):
            with self.subTest(budget=budget):
                packed = self._packer(
                    budget=budget,
                    minimum=8,
                    maximum=max(16, budget // 3),
                ).pack(results)
                self.assertLessEqual(packed.token_count, budget)
                self.assertEqual(packed.token_count, approx_token_len(packed.text))

    def test_citations_are_contiguous_and_map_one_to_one_to_nodes(self):
        packed = self._packer(500).pack([
            self._result(0, "第一条独立资料。"),
            self._result(1, "第二条独立资料。"),
            self._result(2, "第三条独立资料。"),
        ])

        citation_ids = [entry.citation_id for entry in packed.entries]
        node_ids = [entry.node_id for entry in packed.entries]
        rendered_ids = re.findall(r"\[(K\d+)]", packed.text)

        self.assertEqual(citation_ids, ["K1", "K2", "K3"])
        self.assertEqual(rendered_ids, citation_ids)
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertEqual(packed.citation_map, {
            "K1": "node-0",
            "K2": "node-1",
            "K3": "node-2",
        })

    def test_duplicate_node_or_content_only_enters_once(self):
        shared = "请先检查 GPU 状态。"
        packed = self._packer(500).pack([
            self._result(0, shared),
            self._result(0, shared),
            self._result(1, shared),
            self._result(2, shared + "\n\n然后提交训练任务。"),
        ])

        self.assertEqual(packed.text.count(shared), 1)
        self.assertEqual(len(packed.entries), 2)
        self.assertEqual(packed.duplicate_node_count, 2)
        self.assertIn("然后提交训练任务", packed.entries[1].text)

    def test_key_command_is_complete_when_budget_truncates_prose(self):
        command = "watch -n 2 nvidia-smi"
        content = (
            "背景说明。" + "普通文字" * 100 + "\n\n"
            + f"持续监控命令：`{command}`\n\n"
            + "后续说明。" + "附加内容" * 100
        )
        packed = self._packer(budget=180, minimum=8, maximum=30).pack([
            self._result(0, content)
        ])

        self.assertIn(command, packed.text)
        self.assertEqual(packed.text.count(command), 1)
        self.assertIn("内容已按上下文预算截断", packed.text)
        self.assertLessEqual(packed.token_count, 180)

    def test_query_aware_packing_keeps_relevant_tail_in_original_order(self):
        content = (
            "一般介绍。" + "背景" * 100 + "\n\n"
            "设备加入网络后，需要把设备 ID 发给管理员批准。\n\n"
            "最后补充。"
        )
        packed = self._packer(budget=170, minimum=8, maximum=28).pack(
            [self._result(0, content)],
            query="加入网络后为什么不能登录，需要管理员做什么？",
        )

        self.assertIn("设备 ID", packed.text)
        self.assertIn("管理员批准", packed.text)
        self.assertLessEqual(packed.token_count, 170)

    def test_fenced_command_block_is_atomic_and_balanced(self):
        block = "```bash\nssh -p 2222 user@server\n```"
        packed = self._packer(budget=180, minimum=8, maximum=30).pack([
            self._result(0, "说明。" + "文字" * 80 + "\n\n" + block)
        ])

        self.assertIn(block, packed.text)
        self.assertEqual(packed.text.count("```"), 2)
        self.assertNotIn("ssh -p 2222 user@serve\n", packed.text)

    def test_source_section_and_position_are_rendered(self):
        packed = self._packer(300).pack([
            self._result(0, "监控说明。")
        ])

        self.assertIn("来源：服务器手册.md / GPU / 运行监控", packed.text)
        self.assertIn("chars:0-100;lines:1-5", packed.text)
        self.assertIn("Node：node-0", packed.text)

    def test_protected_command_is_omitted_instead_of_cut_when_it_cannot_fit(self):
        command = "ssh " + "very-long-argument " * 100
        packed = self._packer(budget=90, minimum=8, maximum=20).pack([
            self._result(0, command)
        ])

        self.assertNotIn(command[:-1], packed.text)
        self.assertNotIn("very-long-argumen", packed.text)
        self.assertLessEqual(packed.token_count, 90)


if __name__ == "__main__":
    unittest.main()
