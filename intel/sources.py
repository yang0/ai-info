import asyncio
import json
import re
import shutil
from pathlib import Path
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from .models import Post, utcnow


class SourceError(Exception):
    def __init__(self, message, retry_after=0, pause=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.pause = pause


@dataclass
class Batch:
    posts: list[Post]
    coverage: str
    rejected: int = 0


def safe_x_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme == 'https' and p.hostname in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') and bool(
            re.fullmatch(r'/[A-Za-z0-9_]+/status/\d+/?', p.path)) and not p.username
    except ValueError:
        return False


def valid_post(p: Post, now: datetime) -> bool:
    return (safe_x_url(p.url) and now - timedelta(days=7) <= p.created_at <= now
            and not p.text.startswith('RT @'))


def parse_reach(payload: dict, query: str) -> Batch:
    if payload.get('ok') is not True or payload.get('schema_version') != 1:
        code = (payload.get('error') or {}).get('code', 'SCHEMA_MISMATCH')
        if code not in {'NEED_SETUP', 'NEED_LOGIN', 'UPSTREAM_BROKEN', 'RATE_LIMITED', 'TIMEOUT', 'PLATFORM_BLOCKED', 'NOT_FOUND', 'BAD_INPUT', 'BUSY'}:
            code = 'SCHEMA_MISMATCH'
        # Never expose upstream stderr or cookies to the browser.
        raise SourceError(f'Agent Reach 未就绪 ({code})；请在终端运行 agent-reach doctor',
                          retry_after=3600 if code == 'RATE_LIMITED' else 0,
                          pause=code in {'NEED_LOGIN', 'NEED_SETUP', 'PLATFORM_BLOCKED'})
    posts, rejected, now = [], 0, utcnow()
    for item in payload.get('items', []):
        try:
            url = item.get('url') or ''
            match = re.search(r'/([A-Za-z0-9_]+)/status/(\d+)', url)
            if not match or not safe_x_url(url):
                raise ValueError('invalid url')
            raw = item.get('raw') if isinstance(item.get('raw'), dict) else {}
            author = item.get('author') or raw.get('author') or match[1]
            author = str(author).lstrip('@')
            if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', author) or author == 'i':
                raise ValueError('missing author')
            stamp = item.get('published_at')
            try:
                created = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                created = parsedate_to_datetime(stamp)
            p = Post(id=match[2], text=item.get('text') or item.get('title') or '',
                     author=author, created_at=created, url=f'https://x.com/{author}/status/{match[2]}', query=query)
            if not valid_post(p, now):
                raise ValueError('invalid date')
            posts.append(p)
        except (KeyError, ValueError, TypeError, AttributeError):
            rejected += 1
    if rejected and not posts:
        raise SourceError('采集结果缺少有效的原帖时间、作者或链接；未更新情报，请检查上游版本')
    return Batch(posts, 'sampled', rejected)


def executable_argv(name):
    exe = shutil.which(name)
    if not exe:
        raise SourceError(f'未安装 {name}；请按免费接入说明完成安装', pause=True)
    path = Path(exe)
    if name == 'opencli' and path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        # Run the npm JS entry directly on Windows. Never pass queries to cmd.exe.
        root = path.parent / 'node_modules' / '@jackwener' / 'opencli'
        try:
            meta = json.loads((root/'package.json').read_text(encoding='utf-8'))
            binary = meta['bin'] if isinstance(meta['bin'], str) else meta['bin']['opencli']
            entry = (root/binary).resolve()
            if not entry.is_relative_to(root.resolve()) or not entry.is_file() or not shutil.which('node'):
                raise ValueError('invalid npm entry')
            return [shutil.which('node'), str(entry)]
        except (OSError, KeyError, TypeError, ValueError):
            raise SourceError('找不到 OpenCLI 的 Node 入口；请运行 npm install -g @jackwener/opencli 修复', pause=True) from None
    return [exe]


async def run_json(argv):
    proc = await asyncio.create_subprocess_exec(*argv,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=75)
        if len(stdout) > 5_000_000:
            raise SourceError('采集输出过大；请缩小查询范围')
        try:
            return json.loads(stdout)
        except (ValueError, TypeError):
            # Legacy upstreams sometimes print YAML errors. Never return raw output.
            if b'RATE_LIMITED' in stdout:
                raise SourceError('X 限流，已进入冷却', retry_after=3600) from None
            raise SourceError('采集器输出格式不兼容；请更新上游工具并检查登录状态') from None
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    except TimeoutError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise SourceError('采集超时；请检查浏览器连接状态') from None


def parse_opencli(payload, query):
    if isinstance(payload, dict):
        if payload.get('ok') is False:
            raw_code = (payload.get('error') or {}).get('code', '')
            code = str(raw_code).upper()
            if code in {'RATE_LIMITED', 'RATE_LIMIT'}:
                raise SourceError('X 限流，已进入冷却', retry_after=3600)
            raise SourceError('OpenCLI 无法读取 X；请运行 opencli doctor，并在 Chrome 中检查 X 登录',
                              pause=True)
        payload = payload.get('data', payload.get('items', []))
    if not isinstance(payload, list):
        raise SourceError('OpenCLI 结果格式不兼容')
    items = [dict(url=r.get('url'), author=r.get('author'), text=r.get('text'),
                  published_at=r.get('created_at'), raw=r) for r in payload if isinstance(r, dict)]
    return parse_reach({'ok': True, 'schema_version': 1, 'items': items}, query)


async def opencli_search(query: str, limit: int) -> Batch:
    # Web search operators and Latest tab, using an existing browser session.
    since = (utcnow()-timedelta(minutes=120)).strftime('%Y-%m-%d')
    scoped = query + f' since:{since}' if 'since:' not in query else query
    argv = executable_argv('opencli') + ['twitter', 'search', scoped, '--product', 'live',
            '--exclude', 'retweets', '--limit', str(limit), '-f', 'json']
    return parse_opencli(await run_json(argv), query)


async def reach_search(query: str, limit: int) -> Batch:
    # Shell is never used; only the read-only, normalized v1 interface is supported.
    argv = executable_argv('agent-reach') + ['twitter', 'search', query,
                                           '--limit', str(limit), '--backend', 'opencli']
    batch = parse_reach(await run_json(argv), query)
    batch.coverage = 'ranked_sampled'
    return batch


def demo_posts(now=None) -> list[Post]:
    """Synthetic per-minute fixture. Never creates real-looking X status links."""
    now = now or utcnow()
    anchor = now.replace(second=0, microsecond=0)
    themes = [
        ('AgentMemory', '持久记忆让 coding agent 跨任务保留项目上下文', [2, 3, 4, 5, 8, 13, 20, 34]),
        ('OpenWeights', '开放权重模型与推理工具链更新', [4, 5, 6, 7, 8, 9, 11, 13]),
        ('BrowserAgent', '浏览器智能体正在引发开发者复现实验', [1, 2, 3, 4, 5, 7, 12, 19]),
        ('LocalInference', '本地推理运行时的新一轮性能讨论', [10, 13, 16, 17, 14, 12, 10, 7]),
        ('AgentEval', '从基准分数走向真实任务评估', [1, 2, 2, 3, 4, 5, 8, 12]),
        ('MultimodalTools', '多模态工具调用工作流的工程实践', [3, 3, 4, 4, 5, 5, 6, 7]),
    ]
    # Analyze this fixture alone; do not accumulate demo history across scans.
    boundary = anchor
    result = []
    for k, (tag, text, counts) in enumerate(themes):
        for b, count in enumerate(counts):
            for n in range(count):
                at = boundary - timedelta(minutes=(7-b)*15 + (n*13 % 14)+1)
                result.append(Post(id=f'demo-{tag}-{int(at.timestamp())}-{n}',
                    text=f'#{tag} {text}。模拟观测 {at:%H:%M} / 样本 {n+1}，用于观察趋势与来源追踪。',
                    author=f'模拟研究员_{k+1}_{n+1}', created_at=at,
                    url='', likes=n*3, reposts=n//3, query='demo'))
    return result
