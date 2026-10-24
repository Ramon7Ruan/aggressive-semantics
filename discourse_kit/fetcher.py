"""HTTP 抓取层：限速、重试、robots 自检、熔断。

设计要点
--------
1. **可注入时钟与 sleep**：测试时换掉 `clock` / `sleep` 就能在毫秒级验证限速逻辑，
   不必真的等 2 秒。
2. **fail-closed**：robots.txt 拿不到就拒绝访问，不是放行。
3. **硬失败**：重试耗尽抛 FetchError，不返回 None 让调用方猜。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from discourse_kit import config
from discourse_kit.errors import (
    FetchError,
    RobotsDisallow,
    TimeWindowBlocked,
    TooManyBlocks,
)


@dataclass
class RequestRecord:
    """一次真实发出的请求（重试记为多条）。用于审计与测试断言。"""

    index: int
    url: str
    status: int | None
    started_at: float
    note: str = ""


@dataclass
class Fetcher:
    min_gap: float = config.GAP_MIN
    max_gap: float = config.GAP_MAX
    timeout: int = config.TIMEOUT
    max_retry: int = config.MAX_RETRY
    allow_night: bool = False

    session: requests.Session | None = None
    sleep: object = time.sleep
    clock: object = time.monotonic
    now: object = datetime.now

    request_log: list[RequestRecord] = field(default_factory=list)
    _last_started: float | None = field(default=None, repr=False)
    _count: int = field(default=0, repr=False)
    _block_hits: int = field(default=0, repr=False)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "User-Agent": config.USER_AGENT,
                    "Accept-Language": config.ACCEPT_LANGUAGE,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
        if self.min_gap > self.max_gap:
            raise ValueError("min_gap 不能大于 max_gap")

    # ── 对外接口 ────────────────────────────────────────────────
    def fetch(self, url: str) -> str:
        """抓取一个 URL，返回 HTML 文本。"""
        self._guard_window()
        self._guard_robots(url)
        resp = self._request_with_retry(url)
        return resp.content.decode("utf-8", errors="replace")

    # ── 守卫 ────────────────────────────────────────────────────
    def _guard_window(self) -> None:
        if self.allow_night:
            return
        hour = self.now().hour
        start, end = config.NIGHT_BLOCK_START, config.NIGHT_BLOCK_END
        in_night = (start <= hour < end) if start <= end else (hour >= start or hour < end)
        if in_night:
            raise TimeWindowBlocked(
                f"当前 {hour} 点处于禁访时段 {start}:00–{end}:00。"
                "如需强制运行，传 allow_night=True（不建议）。"
            )

    def _guard_robots(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise RobotsDisallow(f"只允许 http/https：{url}")

        # 保守禁访前缀。这不是 robots 的义务，是本项目主动避开接口路径。
        for prefix in config.BLOCKED_PREFIXES:
            if parsed.path.startswith(prefix):
                raise RobotsDisallow(f"命中保守禁访前缀 {prefix!r}：{url}")

        rp = self._robots_for(parsed.netloc)
        if rp is not None and not rp.can_fetch(config.USER_AGENT, url):
            raise RobotsDisallow(f"robots.txt 禁止访问：{url}")

    def _robots_for(self, host: str) -> RobotFileParser | None:
        """按 host 缓存 robots 解析结果。拿不到时 fail-closed（返回 None 但抛错）。"""
        if host in self._robots:
            return self._robots[host]

        robots_url = f"https://{host}/robots.txt"
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code != 200:
                raise FetchError(f"HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001 —— 任何失败都按保守策略拒绝
            raise RobotsDisallow(
                f"无法获取 {robots_url}（{exc}），按 fail-closed 策略拒绝访问 {host}"
            ) from exc

        rp = RobotFileParser()
        rp.parse(resp.content.decode("utf-8", errors="replace").splitlines())
        self._robots[host] = rp
        return rp

    # ── 限速与请求 ──────────────────────────────────────────────
    def _wait_turn(self) -> None:
        if self._count and self._count % config.BATCH_SIZE == 0:
            self.sleep(config.BATCH_PAUSE)

        if self._last_started is None:
            return
        target = self._last_started + random.uniform(self.min_gap, self.max_gap)
        wait = target - self.clock()
        if wait > 0:
            self.sleep(wait)

    def _request_with_retry(self, url: str) -> requests.Response:
        last_error: str = "未知错误"

        for attempt in range(1, self.max_retry + 1):
            self._wait_turn()
            started = self.clock()
            self._last_started = started
            self._count += 1

            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"网络异常：{exc}"
                self._record(url, None, started, note=f"attempt={attempt}")
                self._backoff(attempt)
                continue

            self._record(url, resp.status_code, started, note=f"attempt={attempt}")

            if resp.status_code == 200:
                self._block_hits = 0
                return resp

            if resp.status_code in (429, 403):
                self._block_hits += 1
                if self._block_hits >= config.BLOCK_THRESHOLD:
                    raise TooManyBlocks(
                        f"连续 {self._block_hits} 次被限流（最后状态 {resp.status_code}）。"
                        "疑似触发风控，应立即停止全部采集。"
                    )
                last_error = f"HTTP {resp.status_code}（限流）"
                self._backoff(attempt, base=5.0)
                continue

            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}（服务端错误）"
                self._backoff(attempt)
                continue

            # 其余 4xx：重试无意义，直接失败
            raise FetchError(f"HTTP {resp.status_code}：{url}")

        raise FetchError(f"重试 {self.max_retry} 次仍失败（{last_error}）：{url}")

    def _backoff(self, attempt: int, base: float = 1.0) -> None:
        self.sleep(base * (2 ** (attempt - 1)))

    def _record(self, url: str, status: int | None, started: float, note: str = "") -> None:
        self.request_log.append(
            RequestRecord(
                index=len(self.request_log) + 1,
                url=url,
                status=status,
                started_at=started,
                note=note,
            )
        )
