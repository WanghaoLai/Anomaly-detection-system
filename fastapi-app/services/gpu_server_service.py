"""远程 GPU 服务器的只读状态和文件目录服务。"""
import asyncio
import csv
import io
import json
import logging
import posixpath
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from settings import GPU_SERVER_CONFIG

try:
    import asyncssh
except ImportError:  # 依赖未安装时保持主应用可启动
    asyncssh = None


logger = logging.getLogger(__name__)
LINUX_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

GPU_QUERY = (
    "nvidia-smi --query-gpu="
    "index,uuid,name,driver_version,temperature.gpu,utilization.gpu,"
    "memory.total,memory.used,memory.free,power.draw,power.limit "
    "--format=csv,noheader,nounits"
)
PROCESS_QUERY = (
    "nvidia-smi --query-compute-apps="
    "gpu_uuid,pid,process_name,used_gpu_memory "
    "--format=csv,noheader,nounits"
)
PROCESS_OWNER_QUERY = "ps -eo pid=,user=,comm="
PASSWD_QUERY = "getent passwd"


class GpuServerError(RuntimeError):
    pass


class GpuServerService:
    def __init__(self) -> None:
        self.config = GPU_SERVER_CONFIG
        self._summary_cache: dict[str, Any] | None = None
        self._cache_time = 0.0
        self._summary_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.config["host"] and self.config["ssh_user"])

    def _connection_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "host": self.config["host"],
            "port": self.config["port"],
            "username": self.config["ssh_user"],
        }
        if self.config["ssh_password"]:
            options["password"] = self.config["ssh_password"]
        if self.config["private_key_path"]:
            options["client_keys"] = [self.config["private_key_path"]]
        if self.config["known_hosts_path"]:
            options["known_hosts"] = self.config["known_hosts_path"]
        return options

    async def _connect(self):
        if asyncssh is None:
            raise GpuServerError("后端缺少 asyncssh 依赖")
        if not self.configured:
            raise GpuServerError("尚未配置 GPU 服务器连接信息")
        try:
            return await asyncio.wait_for(
                asyncssh.connect(**self._connection_options()),
                timeout=self.config["connect_timeout"],
            )
        except Exception as exc:
            logger.warning("GPU server SSH connection failed: %s", exc)
            raise GpuServerError("无法连接 GPU 服务器") from exc

    async def _run(self, connection, command: str, check: bool = True):
        try:
            return await connection.run(
                command,
                check=check,
                timeout=self.config["command_timeout"],
            )
        except Exception as exc:
            logger.warning("GPU server command failed: %s", exc)
            raise GpuServerError("远程服务器信息采集失败") from exc

    @staticmethod
    def _number(value: str, integer: bool = False):
        value = value.strip()
        if not value or value.upper() == "N/A":
            return None
        try:
            return int(float(value)) if integer else round(float(value), 2)
        except ValueError:
            return None

    def _parse_gpus(self, output: str) -> list[dict[str, Any]]:
        gpus = []
        for row in csv.reader(io.StringIO(output.strip())):
            if len(row) < 11:
                continue
            total = self._number(row[6], integer=True) or 0
            used = self._number(row[7], integer=True) or 0
            gpus.append({
                "index": self._number(row[0], integer=True),
                "uuid": row[1].strip(),
                "name": row[2].strip(),
                "driverVersion": row[3].strip(),
                "temperature": self._number(row[4], integer=True),
                "utilization": self._number(row[5], integer=True),
                "memoryTotal": total,
                "memoryUsed": used,
                "memoryFree": self._number(row[8], integer=True) or 0,
                "memoryUtilization": round(used * 100 / total, 1) if total else 0,
                "powerDraw": self._number(row[9]),
                "powerLimit": self._number(row[10]),
            })
        return sorted(gpus, key=lambda item: item["index"] if item["index"] is not None else 999)

    @staticmethod
    def _parse_process_owners(output: str) -> dict[int, dict[str, str]]:
        owners = {}
        for line in output.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 2 and parts[0].isdigit():
                owners[int(parts[0])] = {
                    "username": parts[1],
                    "command": parts[2] if len(parts) == 3 else "-",
                }
        return owners

    def _parse_processes(
        self, output: str, owners: dict[int, dict[str, str]], gpus: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        gpu_indexes = {gpu["uuid"]: gpu["index"] for gpu in gpus}
        processes = []
        for row in csv.reader(io.StringIO(output.strip())):
            if len(row) < 4:
                continue
            pid = self._number(row[1], integer=True)
            if pid is None:
                continue
            owner = owners.get(pid, {})
            processes.append({
                "gpuIndex": gpu_indexes.get(row[0].strip()),
                "gpuUuid": row[0].strip(),
                "pid": pid,
                "processName": row[2].strip() or owner.get("command", "-"),
                "memoryUsed": self._number(row[3], integer=True) or 0,
                "username": owner.get("username", "未知"),
            })
        return sorted(processes, key=lambda item: (item["gpuIndex"] or 0, item["pid"]))

    def _base_summary(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "online": False,
            "host": self.config["host"] or "未配置",
            "expectedGpuCount": self.config["expected_gpu_count"],
            "gpuCount": 0,
            "gpus": [],
            "processes": [],
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

    async def _collect_summary(self) -> dict[str, Any]:
        summary = self._base_summary()
        if not self.configured:
            summary["error"] = "尚未配置 GPU 服务器连接信息"
            return summary

        connection = None
        try:
            connection = await self._connect()
            gpu_result, process_result, owner_result = await asyncio.gather(
                self._run(connection, GPU_QUERY),
                self._run(connection, PROCESS_QUERY, check=False),
                self._run(connection, PROCESS_OWNER_QUERY),
            )
            gpus = self._parse_gpus(gpu_result.stdout)
            owners = self._parse_process_owners(owner_result.stdout)
            processes = self._parse_processes(process_result.stdout, owners, gpus)
            summary.update({
                "online": True,
                "gpuCount": len(gpus),
                "gpus": gpus,
                "processes": processes,
                "error": None,
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            })
            return summary
        except GpuServerError as exc:
            summary["error"] = str(exc)
            return summary
        finally:
            if connection is not None:
                connection.close()
                await connection.wait_closed()

    async def get_summary(self, force: bool = False) -> dict[str, Any]:
        cache_age = time.monotonic() - self._cache_time
        if not force and self._summary_cache and cache_age < self.config["status_cache_seconds"]:
            return self._summary_cache

        async with self._summary_lock:
            cache_age = time.monotonic() - self._cache_time
            if not force and self._summary_cache and cache_age < self.config["status_cache_seconds"]:
                return self._summary_cache
            self._summary_cache = await self._collect_summary()
            self._cache_time = time.monotonic()
            return self._summary_cache

    def resolve_linux_account(self, app_username: str) -> str:
        try:
            account_map = json.loads(self.config["account_map_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise GpuServerError("GPU 账号映射配置格式错误") from exc
        if not isinstance(account_map, dict):
            raise GpuServerError("GPU 账号映射配置必须是 JSON 对象")
        # 精确映射优先；"*" 用于所有系统用户共用一个 Linux 账号。
        linux_username = str(
            account_map.get(app_username, account_map.get("*", app_username))
        )
        if not LINUX_USERNAME_PATTERN.fullmatch(linux_username):
            raise GpuServerError("当前用户未绑定有效的 GPU 服务器账号")
        return linux_username

    def _render_root_directory(self, linux_username: str, path_template: str) -> str:
        try:
            root = posixpath.normpath(
                path_template.format(username=linux_username)
            )
        except (KeyError, ValueError) as exc:
            raise GpuServerError("GPU 账号根目录配置无效") from exc
        if not posixpath.isabs(root):
            raise GpuServerError("GPU 账号根目录必须使用绝对路径")
        return root

    def resolve_allowed_directories(self, linux_username: str) -> list[dict[str, str]]:
        raw = self.config.get("allowed_directories_json", "")
        if raw:
            try:
                configured = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GpuServerError("GPU 账号展示目录配置格式错误") from exc
        else:
            configured = {"账号主目录": self.config["account_root_template"]}

        if isinstance(configured, dict):
            entries = list(configured.items())
        elif isinstance(configured, list):
            entries = [
                (posixpath.basename(posixpath.normpath(str(path))) or f"目录 {index + 1}", path)
                for index, path in enumerate(configured)
            ]
        else:
            raise GpuServerError("GPU 账号展示目录配置必须是 JSON 对象或数组")

        directories = []
        for index, (name, path_template) in enumerate(entries):
            if not isinstance(name, str) or not name.strip() or not isinstance(path_template, str):
                raise GpuServerError("GPU 账号展示目录配置无效")
            directories.append({
                "id": str(index),
                "name": name.strip(),
                "path": self._render_root_directory(linux_username, path_template),
            })
        if not directories:
            raise GpuServerError("尚未配置可展示的 GPU 账号目录")
        return directories

    def get_file_roots(self, app_username: str) -> dict[str, Any]:
        linux_username = self.resolve_linux_account(app_username)
        directories = self.resolve_allowed_directories(linux_username)
        return {
            "account": linux_username,
            "directories": [
                {"id": directory["id"], "name": directory["name"]}
                for directory in directories
            ],
        }

    def _requested_directory(
        self,
        linux_username: str,
        relative_path: str,
        root_template: str | None = None,
    ) -> tuple[str, str]:
        root = self._render_root_directory(
            linux_username,
            root_template or self.config["account_root_template"],
        )
        path = PurePosixPath(relative_path or ".")
        if path.is_absolute() or ".." in path.parts:
            raise GpuServerError("请求的目录超出授权范围")
        normalized_relative = "" if str(path) == "." else str(path)
        candidate = posixpath.normpath(posixpath.join(root, normalized_relative))
        if posixpath.commonpath([root, candidate]) != root:
            raise GpuServerError("请求的目录超出授权范围")
        return root, candidate

    async def _uid_map(self, connection) -> dict[int, str]:
        result = await self._run(connection, PASSWD_QUERY, check=False)
        users = {}
        for line in result.stdout.splitlines():
            fields = line.split(":")
            if len(fields) > 2 and fields[2].isdigit():
                users[int(fields[2])] = fields[0]
        return users

    async def get_files(
        self,
        app_username: str,
        root_id: str,
        relative_path: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        linux_username = self.resolve_linux_account(app_username)
        directories = self.resolve_allowed_directories(linux_username)
        selected = next(
            (directory for directory in directories if directory["id"] == root_id),
            directories[0] if not root_id else None,
        )
        if selected is None:
            raise GpuServerError("请求的展示目录未经授权")
        root, candidate = self._requested_directory(
            linux_username,
            relative_path,
            selected["path"],
        )
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            root_real = posixpath.normpath(str(await sftp.realpath(root)))
            candidate_real = posixpath.normpath(str(await sftp.realpath(candidate)))
            if posixpath.commonpath([root_real, candidate_real]) != root_real:
                raise GpuServerError("请求的目录超出授权范围")

            uid_map = await self._uid_map(connection)
            items = []
            truncated = False
            async for entry in sftp.scandir(candidate_real):
                if entry.filename in {".", ".."}:
                    continue
                attrs = entry.attrs
                permissions = attrs.permissions or 0
                is_directory = stat.S_ISDIR(permissions)
                is_symlink = stat.S_ISLNK(permissions)
                entry_type = "directory" if is_directory else "symlink" if is_symlink else "file"
                items.append({
                    "name": entry.filename,
                    "type": entry_type,
                    "size": attrs.size or 0,
                    "owner": uid_map.get(attrs.uid, str(attrs.uid) if attrs.uid is not None else "-"),
                    "permissions": stat.filemode(permissions) if permissions else "-",
                    "modifiedAt": (
                        datetime.fromtimestamp(attrs.mtime, timezone.utc).isoformat()
                        if attrs.mtime else None
                    ),
                })
                if len(items) >= self.config["file_max_entries"]:
                    truncated = True
                    break

            items.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
            total = len(items)
            start = (page - 1) * page_size
            current_relative = posixpath.relpath(candidate_real, root_real)
            current_relative = "" if current_relative == "." else current_relative
            parent = posixpath.dirname(current_relative) if current_relative else None
            return {
                "account": linux_username,
                "rootId": selected["id"],
                "rootName": selected["name"],
                "path": current_relative,
                "parent": parent,
                "page": page,
                "pageSize": page_size,
                "total": total,
                "truncated": truncated,
                "items": items[start:start + page_size],
            }
        except GpuServerError:
            raise
        except Exception as exc:
            logger.warning("GPU account directory listing failed: %s", exc)
            raise GpuServerError("无法读取当前账号的文件信息") from exc
        finally:
            connection.close()
            await connection.wait_closed()


gpu_server_service = GpuServerService()
