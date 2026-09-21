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

from settings import GPU_ADDITIONAL_SERVERS_JSON, GPU_SERVER_CONFIG

try:
    import asyncssh
except ImportError:  # 依赖未安装时保持主应用可启动
    asyncssh = None


logger = logging.getLogger(__name__)
LINUX_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SERVER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

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
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or GPU_SERVER_CONFIG
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
            "serverId": self.config.get("id", "primary"),
            "serverName": self.config.get("name", "GPU 服务器"),
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
        # GPU 文件系统身份必须来自管理员配置，不能由可编辑的应用账号名推导。
        # "*" 仅用于管理员明确配置所有系统用户共用一个低权限 Linux 账号。
        mapped_account = account_map.get(app_username, account_map.get("*"))
        if mapped_account is None:
            raise GpuServerError("当前用户未绑定 GPU 服务器账号")
        linux_username = str(mapped_account).strip()
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

    def resolve_conda_env_roots(self) -> list[dict[str, str]]:
        raw = self.config.get("conda_env_roots_json", "")
        try:
            configured = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise GpuServerError("Conda 环境总目录配置格式错误") from exc

        if isinstance(configured, dict):
            entries = list(configured.items())
        elif isinstance(configured, list):
            entries = [
                (
                    posixpath.basename(posixpath.normpath(str(path)))
                    or f"Conda 目录 {index + 1}",
                    path,
                )
                for index, path in enumerate(configured)
            ]
        else:
            raise GpuServerError("Conda 环境总目录配置必须是 JSON 对象或数组")

        roots = []
        for index, (name, path) in enumerate(entries):
            if not isinstance(name, str) or not name.strip() or not isinstance(path, str):
                raise GpuServerError("Conda 环境总目录配置无效")
            normalized = posixpath.normpath(path.strip())
            if not posixpath.isabs(normalized):
                raise GpuServerError("Conda 环境总目录必须使用绝对路径")
            roots.append({"id": str(index), "name": name.strip(), "path": normalized})
        if not roots:
            raise GpuServerError("尚未配置 Conda 环境总目录")
        return roots

    async def get_conda_environments(self) -> dict[str, Any]:
        roots = self.resolve_conda_env_roots()
        connection = await self._connect()
        environments: list[dict[str, Any]] = []
        root_results = []
        truncated = False
        max_entries = self.config["conda_env_max_entries"]
        try:
            sftp = await connection.start_sftp_client()
            for root in roots:
                root_result = {**root, "available": True, "error": None}
                try:
                    root_real = posixpath.normpath(str(await sftp.realpath(root["path"])))
                    root_result["path"] = root_real
                    async for entry in sftp.scandir(root_real):
                        if entry.filename in {".", ".."}:
                            continue
                        environment_path = posixpath.join(root_real, entry.filename)
                        try:
                            environment_real = posixpath.normpath(
                                str(await sftp.realpath(environment_path))
                            )
                            if posixpath.commonpath([root_real, environment_real]) != root_real:
                                continue
                            environment_attrs = await sftp.stat(environment_real)
                            if (
                                environment_attrs.permissions
                                and not stat.S_ISDIR(environment_attrs.permissions)
                            ):
                                continue
                            conda_meta = await sftp.stat(
                                posixpath.join(environment_real, "conda-meta")
                            )
                            if conda_meta.permissions and not stat.S_ISDIR(
                                conda_meta.permissions
                            ):
                                continue
                        except Exception:
                            continue

                        environments.append({
                            "name": entry.filename,
                            "path": environment_real,
                            "sourceId": root["id"],
                            "sourceName": root["name"],
                            "modifiedAt": (
                                datetime.fromtimestamp(
                                    environment_attrs.mtime, timezone.utc
                                ).isoformat()
                                if environment_attrs.mtime else None
                            ),
                        })
                        if len(environments) >= max_entries:
                            truncated = True
                            break
                except Exception as exc:
                    logger.warning(
                        "Conda environment root scan failed for %s: %s",
                        root["path"],
                        exc,
                    )
                    root_result["available"] = False
                    root_result["error"] = "目录不可访问"
                root_results.append(root_result)
                if truncated:
                    break

            environments.sort(
                key=lambda item: (item["sourceName"].lower(), item["name"].lower())
            )
            return {
                "source": "DIRECTORY",
                "roots": root_results,
                "environments": environments,
                "total": len(environments),
                "truncated": truncated,
                "scannedAt": datetime.now(timezone.utc).isoformat(),
            }
        except GpuServerError:
            raise
        except Exception as exc:
            logger.warning("Conda environment listing failed: %s", exc)
            raise GpuServerError("无法读取 Conda 环境列表") from exc
        finally:
            connection.close()
            await connection.wait_closed()

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
                "absolutePath": candidate_real,
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

class GpuServerRegistry:
    """只读 GPU 服务器白名单。

    客户端只能选择已在环境变量中注册的稳定 ID，不能传入主机、
    端口或凭据，避免将 SSH 监控接口变成任意网络探测器。
    """

    _OVERRIDABLE_FIELDS = {
        "host",
        "port",
        "ssh_user",
        "ssh_password",
        "private_key_path",
        "known_hosts_path",
        "connect_timeout",
        "command_timeout",
        "status_cache_seconds",
        "expected_gpu_count",
        "account_root_template",
        "file_max_entries",
        "conda_env_max_entries",
    }
    _JSON_FIELDS = {
        "account_map": "account_map_json",
        "allowed_directories": "allowed_directories_json",
        "conda_env_roots": "conda_env_roots_json",
    }

    def __init__(self, configs: list[dict[str, Any]]) -> None:
        if not configs:
            raise GpuServerError("至少需要一个 GPU 服务器配置")
        self._services: dict[str, GpuServerService] = {}
        self._default_id = str(configs[0].get("id") or "primary")
        for config in configs:
            server_id = str(config.get("id") or "").strip()
            if not SERVER_ID_PATTERN.fullmatch(server_id):
                raise GpuServerError(f"GPU 服务器 ID 无效: {server_id or '<empty>'}")
            if server_id in self._services:
                raise GpuServerError(f"GPU 服务器 ID 重复: {server_id}")
            self._services[server_id] = GpuServerService(dict(config))

    @classmethod
    def from_environment(
        cls,
        primary_config: dict[str, Any],
        additional_json: str,
    ) -> "GpuServerRegistry":
        try:
            entries = json.loads(additional_json or "[]")
        except json.JSONDecodeError as exc:
            raise GpuServerError("GPU 追加服务器配置不是有效 JSON") from exc
        if not isinstance(entries, list):
            raise GpuServerError("GPU 追加服务器配置必须是 JSON 数组")

        configs = [dict(primary_config)]
        allowed_keys = {"id", "name"} | cls._OVERRIDABLE_FIELDS | set(cls._JSON_FIELDS)
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise GpuServerError(f"第 {index + 1} 个 GPU 追加服务器必须是 JSON 对象")
            unknown = set(entry) - allowed_keys
            if unknown:
                raise GpuServerError(
                    f"第 {index + 1} 个 GPU 服务器存在未支持字段: "
                    + ", ".join(sorted(unknown))
                )
            config = dict(primary_config)
            # 追加服务器不得隐式复用主服务器的凭据、账号映射或目录授权。
            # 共享超时、数量上限等不会扩大访问范围的默认值。
            config.update({
                "host": "",
                "ssh_user": "",
                "ssh_password": "",
                "private_key_path": "",
                "known_hosts_path": "",
                "account_map_json": "{}",
                "allowed_directories_json": "{}",
                "conda_env_roots_json": "{}",
            })
            config.update({key: entry[key] for key in cls._OVERRIDABLE_FIELDS if key in entry})
            config["id"] = str(entry.get("id") or "").strip()
            config["name"] = str(entry.get("name") or "").strip()
            if not config["name"]:
                raise GpuServerError(f"第 {index + 1} 个 GPU 服务器缺少显示名称")
            if not str(config["host"]).strip():
                raise GpuServerError(f"第 {index + 1} 个 GPU 服务器缺少主机地址")
            if not str(config["ssh_user"]).strip():
                raise GpuServerError(f"第 {index + 1} 个 GPU 服务器缺少 SSH 账号")
            if not (
                str(config["private_key_path"]).strip()
                or str(config["ssh_password"]).strip()
            ):
                raise GpuServerError(f"第 {index + 1} 个 GPU 服务器缺少 SSH 凭据")
            if not str(config["known_hosts_path"]).strip():
                raise GpuServerError(
                    f"第 {index + 1} 个 GPU 服务器缺少 known_hosts 指纹文件"
                )
            for key, minimum, maximum in (
                ("port", 1, 65535),
                ("expected_gpu_count", 0, 1024),
                ("file_max_entries", 1, 1_000_000),
                ("conda_env_max_entries", 1, 1_000_000),
            ):
                value = config[key]
                if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                    raise GpuServerError(
                        f"第 {index + 1} 个 GPU 服务器的 {key} 无效"
                    )
            for key in ("connect_timeout", "command_timeout", "status_cache_seconds"):
                value = config[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    raise GpuServerError(
                        f"第 {index + 1} 个 GPU 服务器的 {key} 无效"
                    )
            for public_name, internal_name in cls._JSON_FIELDS.items():
                if public_name in entry:
                    value = entry[public_name]
                    if not isinstance(value, (dict, list)):
                        raise GpuServerError(
                            f"第 {index + 1} 个 GPU 服务器的 {public_name} "
                            "必须是 JSON 对象或数组"
                        )
                    config[internal_name] = json.dumps(value, ensure_ascii=False)
            configs.append(config)
        return cls(configs)

    @property
    def default_service(self) -> GpuServerService:
        return self._services[self._default_id]

    @property
    def default_id(self) -> str:
        return self._default_id

    def get(self, server_id: str = "") -> GpuServerService:
        selected_id = (server_id or self._default_id).strip()
        service = self._services.get(selected_id)
        if service is None:
            raise GpuServerError("所选 GPU 服务器不存在或未经管理员配置")
        return service

    def public_identity(self, server_id: str = "") -> dict[str, str]:
        """返回可写入业务响应的服务器身份，不暴露任何 SSH 凭据。"""
        selected_id = (server_id or self._default_id).strip()
        service = self.get(selected_id)
        return {
            "server_id": selected_id,
            "server_name": str(service.config.get("name") or "GPU 服务器"),
            "server_host": str(service.config.get("host") or "未配置"),
        }

    def public_options(self) -> list[dict[str, Any]]:
        def has_json_entries(raw: Any) -> bool:
            try:
                value = json.loads(raw or "{}")
            except (TypeError, json.JSONDecodeError):
                return False
            return isinstance(value, (dict, list)) and bool(value)

        return [
            {
                "id": server_id,
                "name": service.config.get("name") or "GPU 服务器",
                "host": service.config.get("host") or "未配置",
                "configured": service.configured,
                "expectedGpuCount": service.config.get("expected_gpu_count", 0),
                "supportsFiles": has_json_entries(
                    service.config.get("account_map_json")
                ),
                "supportsConda": has_json_entries(
                    service.config.get("conda_env_roots_json")
                ),
                "isDefault": server_id == self._default_id,
            }
            for server_id, service in self._services.items()
        ]


def build_gpu_server_registry(
    primary_config: dict[str, Any],
    additional_json: str,
) -> tuple[GpuServerRegistry, dict[str, Any]]:
    """构建注册表并返回可安全展示给管理员的配置健康状态。"""
    try:
        registry = GpuServerRegistry.from_environment(
            primary_config,
            additional_json,
        )
        return registry, {
            "healthy": True,
            "fallbackToPrimary": False,
            "activeServerCount": len(registry.public_options()),
            "error": None,
        }
    except GpuServerError as exc:
        # 监控页配置错误不应阻止训练、推理等核心模块启动；同时必须
        # 保存结构化降级状态，不能只依赖容易被忽略的启动日志。
        registry = GpuServerRegistry([primary_config])
        return registry, {
            "healthy": False,
            "fallbackToPrimary": True,
            "activeServerCount": 1,
            "error": str(exc),
        }


gpu_server_registry, gpu_server_configuration_status = build_gpu_server_registry(
    GPU_SERVER_CONFIG,
    GPU_ADDITIONAL_SERVERS_JSON,
)
if not gpu_server_configuration_status["healthy"]:
    logger.error(
        "GPU 追加服务器配置无效，已回退到主服务器: %s",
        gpu_server_configuration_status["error"],
    )

# 保留旧单例导出，避免历史测试与内部调用失效。
gpu_server_service = gpu_server_registry.default_service
