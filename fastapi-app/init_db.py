"""兼容旧入口：不再根据 ORM 静默补表，而是执行可审计的版本化迁移。"""

from common.migrations import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli(["apply"]))
