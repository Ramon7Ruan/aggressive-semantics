"""真实网络下的合规回归测试（默认不运行）。

背景：2026-10 的完整审计结论是——虎扑 `robots.txt` 第 31 行有 `User-agent: *` +
`Disallow: /`，即**全站禁止**。此前把它判成「允许」是一次调研失误（用 `grep | head -25`
看一个 31 行的文件，通配段被截掉），是被 `Fetcher._guard_robots` 自己发现的。

所以这个文件的作用变了：不再验收「能抓到 10 个 URL」，而是验收
**「守卫确实会拒绝，且拒绝发生在发请求之前」**。守住这一条，比抓到多少数据重要。

运行：pytest -m live -q -s
"""

import pytest

from discourse_kit.errors import RobotsDisallow
from discourse_kit.fetcher import Fetcher


@pytest.mark.live
def test_list_page_is_refused_before_request():
    f = Fetcher()
    with pytest.raises(RobotsDisallow):
        f.fetch("https://bbs.hupu.com/topic-daily")
    assert f.request_log == [], "被 robots 拒绝的 URL 不应产生任何网络请求"


@pytest.mark.live
def test_thread_page_is_refused_before_request():
    f = Fetcher()
    with pytest.raises(RobotsDisallow):
        f.fetch("https://bbs.hupu.com/643079380.html")
    assert f.request_log == []


@pytest.mark.live
def test_api_path_blocked_even_by_conservative_prefix():
    f = Fetcher()
    with pytest.raises(RobotsDisallow):
        f.fetch("https://bbs.hupu.com/api/v4/thread")
    assert f.request_log == []


@pytest.mark.live
def test_robots_is_parsed_not_eyeballed():
    """直接验证判定依据：robotparser 对通配段的结论必须是 False。

    这条断言是对那次失误的固化：只要平台仍全站禁止，任何「我们绕一下」的改动
    都会让这个测试变红。
    """
    import urllib.request
    import urllib.robotparser

    from discourse_kit import config

    req = urllib.request.Request("https://bbs.hupu.com/robots.txt",
                                 headers={"User-Agent": config.USER_AGENT})
    txt = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(txt.splitlines())
    assert rp.can_fetch(config.USER_AGENT, "https://bbs.hupu.com/topic-daily") is False
