"""M1 单元测试：限速、robots 守卫、重试、熔断、时间窗。

全部用假时钟 + 脚本化 session，不发真实网络请求，毫秒级完成。
"""

from datetime import datetime

import pytest

from discourse_kit import config
from discourse_kit.errors import FetchError, RobotsDisallow, TimeWindowBlocked, TooManyBlocks
from discourse_kit.fetcher import Fetcher

OPEN_ROBOTS = "User-agent: *\nDisallow: /blocked/\n"


class FakeClock:
    """假时钟：sleep 推进时间，clock 读取时间。"""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"<html></html>") -> None:
        self.status_code = status
        self.content = body


class ScriptedSession:
    """按顺序返回预设响应；robots.txt 单独处理。"""

    def __init__(self, robots: str = OPEN_ROBOTS, robots_status: int = 200, pages=None) -> None:
        self.robots = robots
        self.robots_status = robots_status
        self.pages = list(pages or [])
        self.calls: list[str] = []

    def get(self, url: str, timeout=None):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(self.robots_status, self.robots.encode("utf-8"))
        if not self.pages:
            raise AssertionError(f"没有更多预设响应，但仍请求了 {url}")
        status, body = self.pages.pop(0)
        return FakeResponse(status, body)


def make_fetcher(session, clock=None, **kw) -> Fetcher:
    clock = clock or FakeClock()
    return Fetcher(session=session, clock=clock, sleep=clock.sleep, allow_night=True, **kw)


# ── robots 与禁访前缀 ────────────────────────────────────────────
def test_api_prefix_is_blocked_before_any_request():
    session = ScriptedSession()
    f = make_fetcher(session)
    with pytest.raises(RobotsDisallow, match="保守禁访前缀"):
        f.fetch("https://bbs.hupu.com/api/v4/thread")
    assert session.calls == [], "守卫必须在发请求之前生效"


@pytest.mark.parametrize("path", ["/ajax/x", "/search?q=1"])
def test_other_blocked_prefixes(path):
    session = ScriptedSession()
    f = make_fetcher(session)
    with pytest.raises(RobotsDisallow):
        f.fetch(f"https://bbs.hupu.com{path}")


def test_robots_disallow_is_respected():
    session = ScriptedSession()
    f = make_fetcher(session)
    with pytest.raises(RobotsDisallow, match="robots.txt 禁止访问"):
        f.fetch("https://bbs.hupu.com/blocked/page")


def test_robots_fetch_failure_is_fail_closed():
    session = ScriptedSession(robots_status=500)
    f = make_fetcher(session)
    with pytest.raises(RobotsDisallow, match="fail-closed"):
        f.fetch("https://bbs.hupu.com/topic-daily")


def test_robots_loaded_once_per_host():
    session = ScriptedSession(pages=[(200, b"a"), (200, b"b")])
    f = make_fetcher(session)
    f.fetch("https://bbs.hupu.com/1.html")
    f.fetch("https://bbs.hupu.com/2.html")
    robots_calls = [u for u in session.calls if u.endswith("/robots.txt")]
    assert len(robots_calls) == 1


# ── 限速 ────────────────────────────────────────────────────────
def test_gap_between_requests_within_bounds():
    session = ScriptedSession(pages=[(200, b"a")] * 5)
    clock = FakeClock()
    f = make_fetcher(session, clock, min_gap=1.5, max_gap=2.5)
    for i in range(5):
        f.fetch(f"https://bbs.hupu.com/{i}.html")

    starts = [r.started_at for r in f.request_log]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 1.5 - 1e-9 for g in gaps), f"间隔过短：{gaps}"
    assert all(g <= 2.5 + 1e-9 for g in gaps), f"间隔过长：{gaps}"


def test_gap_is_randomised_not_constant():
    session = ScriptedSession(pages=[(200, b"a")] * 8)
    clock = FakeClock()
    f = make_fetcher(session, clock)
    for i in range(8):
        f.fetch(f"https://bbs.hupu.com/{i}.html")
    starts = [r.started_at for r in f.request_log]
    gaps = [round(b - a, 6) for a, b in zip(starts, starts[1:])]
    assert len(set(gaps)) > 1, f"间隔是固定值，不符合随机化要求：{gaps}"


# ── 重试与熔断 ──────────────────────────────────────────────────
def test_retry_on_500_then_success():
    session = ScriptedSession(pages=[(500, b""), (500, b""), (200, b"<html>ok</html>")])
    f = make_fetcher(session, max_retry=3)
    html = f.fetch("https://bbs.hupu.com/1.html")
    assert "ok" in html
    assert len([r for r in f.request_log if r.status == 500]) == 2


def test_404_fails_immediately_without_retry():
    session = ScriptedSession(pages=[(404, b"")])
    f = make_fetcher(session, max_retry=3)
    with pytest.raises(FetchError, match="HTTP 404"):
        f.fetch("https://bbs.hupu.com/1.html")
    assert len(f.request_log) == 1, "404 不应重试"


def test_consecutive_429_triggers_circuit_breaker():
    session = ScriptedSession(pages=[(429, b"")] * 10)
    f = make_fetcher(session, max_retry=5)
    with pytest.raises(TooManyBlocks, match="疑似触发风控"):
        f.fetch("https://bbs.hupu.com/1.html")


def test_retry_exhaustion_raises():
    session = ScriptedSession(pages=[(500, b"")] * 5)
    f = make_fetcher(session, max_retry=2)
    with pytest.raises(FetchError, match="重试 2 次仍失败"):
        f.fetch("https://bbs.hupu.com/1.html")


# ── 时间窗 ──────────────────────────────────────────────────────
def test_night_window_blocks_by_default():
    session = ScriptedSession()
    clock = FakeClock()
    f = Fetcher(
        session=session,
        clock=clock,
        sleep=clock.sleep,
        now=lambda: datetime(2026, 10, 21, 3, 0, 0),
    )
    with pytest.raises(TimeWindowBlocked, match="禁访时段"):
        f.fetch("https://bbs.hupu.com/topic-daily")


def test_daytime_is_allowed():
    session = ScriptedSession(pages=[(200, b"ok")])
    clock = FakeClock()
    f = Fetcher(
        session=session,
        clock=clock,
        sleep=clock.sleep,
        now=lambda: datetime(2026, 10, 21, 14, 0, 0),
    )
    assert "ok" in f.fetch("https://bbs.hupu.com/topic-daily")


# ── 编码与配置 ──────────────────────────────────────────────────
def test_utf8_chinese_decoded_correctly():
    body = "<title>步行街主干道</title>".encode("utf-8")
    session = ScriptedSession(pages=[(200, body)])
    f = make_fetcher(session)
    assert "步行街主干道" in f.fetch("https://bbs.hupu.com/topic-daily")


def test_invalid_gap_range_rejected():
    with pytest.raises(ValueError, match="min_gap"):
        Fetcher(session=ScriptedSession(), min_gap=3.0, max_gap=1.0)


def test_config_gap_defaults_are_sane():
    assert 1.0 <= config.GAP_MIN < config.GAP_MAX <= 5.0
    assert config.BLOCK_THRESHOLD >= 2
