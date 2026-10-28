"""Bounded network reads. POSTs are never blindly retried."""
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
import requests


class NetworkError(RuntimeError):
    pass


def validate_url(url: str, policy: dict, *, resolve: bool = True) -> None:
    from .config import ConfigError
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ConfigError("URL 无效") from None
    schemes = ("https", "http") if policy["allow_http"] else ("https",)
    if parts.scheme not in schemes or not parts.hostname or parts.username or parts.password:
        raise ConfigError("URL 必须为 HTTPS 且不能嵌入账号密码；HTTP 需显式启用")
    if any(ord(c) < 33 for c in url):
        raise ConfigError("URL 不能含空白或控制字符")
    if policy["allow_private_hosts"]:
        return
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ConfigError("默认禁止访问本机或内网地址")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if not resolve:
            return
        try:
            addresses = [
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                              type=socket.SOCK_STREAM)
            ]
        except OSError:
            raise NetworkError("DNS 解析失败") from None
    if not addresses or any(not address.is_global for address in addresses):
        raise ConfigError("默认禁止访问本机、内网或保留地址")


class HttpClient:
    def __init__(self, policy: dict, session=None, sleeper=time.sleep):
        self.policy = policy
        self._external_session = session is not None
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.session.headers.update({"User-Agent": "SmartDailyNews/0.2 (+source-linked RSS reader)"})

    def close(self):
        self.session.close()

    def fork(self):
        """Create an isolated client for one fetch worker.

        Custom-injected sessions cannot safely be reproduced; callers can fall
        back to serial requests when this returns None.
        """
        if self._external_session:
            return None
        return HttpClient(dict(self.policy), sleeper=self.sleeper)

    def get(self, url: str, headers: dict | None = None) -> tuple[bytes, dict, int]:
        attempts = self.policy["get_retries"] + 1
        for attempt in range(attempts):
            try:
                return self._get(url, headers or {})
            except (requests.RequestException, NetworkError) as exc:
                if attempt + 1 == attempts:
                    if isinstance(exc, NetworkError):
                        raise
                    raise NetworkError(f"网络读取失败: {type(exc).__name__}") from None
                self.sleeper(min(2 ** attempt, 8))
        raise AssertionError("unreachable")

    def _get(self, url: str, headers: dict) -> tuple[bytes, dict, int]:
        for redirect in range(4):
            validate_url(url, self.policy)
            with self.session.get(url, headers=headers, timeout=self.policy["timeout_seconds"],
                                  allow_redirects=False, stream=True) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect == 3 or not response.headers.get("Location"):
                        raise NetworkError("新闻源重定向无效或次数过多")
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code == 304:
                    return b"", dict(response.headers), 304
                if response.status_code != 200:
                    raise NetworkError(f"新闻源返回 HTTP {response.status_code}")
                size = 0
                chunks = []
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > self.policy["max_feed_bytes"]:
                        raise NetworkError("新闻源响应超过大小上限")
                    chunks.append(chunk)
                return b"".join(chunks), dict(response.headers), response.status_code
        raise NetworkError("新闻源重定向失败")

    def post(self, url: str, *, headers: dict, body: bytes, timeout: int, max_bytes: int = 1000000):
        validate_url(url, self.policy)
        with self.session.post(url, data=body, headers=headers, timeout=timeout,
                               allow_redirects=False, stream=True) as response:
            size, chunks = 0, []
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise NetworkError("远端响应超过大小上限")
                chunks.append(chunk)
            return response.status_code, dict(response.headers), b"".join(chunks)
