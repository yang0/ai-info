import asyncio
import uuid
from datetime import timedelta
from .models import utcnow
from .sources import demo_posts, reach_search, opencli_search, SourceError
from .analysis import prepare_clusters, trend_topics, verify, assess

AGENTS = [
    {'id': 'scout', 'name': '侦察员', 'role': '并行扫描 X 查询', 'kind': 'tool'},
    {'id': 'cluster', 'name': '聚类员', 'role': '去重与话题聚合', 'kind': 'rules'},
    {'id': 'trend', 'name': '趋势员', 'role': '增长与加速度计算', 'kind': 'rules'},
    {'id': 'verify', 'name': '核验员', 'role': '校验来源与样本边界', 'kind': 'rules'},
    {'id': 'analyst', 'name': '研判组', 'role': '机会 / 质疑双视角', 'kind': 'rules_or_llm'},
    {'id': 'editor', 'name': '主编', 'role': '汇总并归档情报', 'kind': 'rules'},
]


class Runner:
    def __init__(self, store):
        self.store = store
        self.task = None
        self.active = None
        self.last_started = 0
        self.cooldown_until = 0

    def start(self):
        if self.task and not self.task.done():
            raise ValueError('已有扫描正在执行')
        if utcnow().timestamp() < self.cooldown_until:
            raise ValueError(f'采集限流冷却中，请在 {int(self.cooldown_until-utcnow().timestamp())+1} 秒后重试')
        settings = self.store.settings()
        run = {'id': uuid.uuid4().hex[:12], 'started': utcnow().isoformat(),
               'state': 'running', 'mode': settings.mode, 'scope': settings.dataset_key(), 'finished': None, 'error': None}
        self.active = run
        self.last_started = utcnow().timestamp()
        self.store.run(run)
        self.task = asyncio.create_task(self.execute(run, settings))
        return run.copy()

    def emit(self, run, agent, state, message):
        self.store.event(run['id'], agent, state, message)

    async def stage(self, run, agent, fn):
        self.emit(run, agent, 'running', '开始执行')
        result = fn()
        if asyncio.iscoroutine(result):
            result = await result
        # Yield so cancellation and SSE remain responsive between rule stages.
        await asyncio.sleep(0)
        self.emit(run, agent, 'done', '执行完成')
        return result

    async def execute(self, run, settings):
        stage = 'scout'
        try:
            now = utcnow()
            self.emit(run, 'scout', 'running', f'扫描 {len(settings.queries)} 条查询' if settings.mode != 'demo' else '载入隔离的模拟样本')
            if settings.mode == 'demo':
                posts, coverage, rejected = demo_posts(now), 'synthetic', 0
            else:
                search = opencli_search if settings.mode == 'opencli' else reach_search
                sem = asyncio.Semaphore(2)
                async def collect(q):
                    async with sem:
                        return await search(q, settings.result_limit)
                batches = await asyncio.gather(*(collect(q) for q in settings.queries), return_exceptions=True)
                # No silent partial success: preserve the prior published snapshot on any query failure.
                errors = [b for b in batches if isinstance(b, BaseException)]
                if errors:
                    raise errors[0]
                posts = list({p.id: p for b in batches for p in b.posts}.values())
                coverage = 'ranked_sampled' if settings.mode == 'agent_reach' else 'sampled'
                rejected = sum(b.rejected for b in batches)
                self.store.put_posts(settings.dataset_key(), posts)
                posts = self.store.posts(settings.dataset_key(), (now-timedelta(minutes=120)).timestamp())
            self.emit(run, 'scout', 'done', f'{len(posts)} 条样本；丢弃 {rejected} 条无效记录；覆盖 {coverage}')
            stage = 'cluster'
            groups = await self.stage(run, 'cluster', lambda: prepare_clusters(posts, now))
            stage = 'trend'
            topics = await self.stage(run, 'trend', lambda: trend_topics(posts, now, settings.min_authors, groups))
            stage = 'verify'
            async def verify_all():
                async def one(t):
                    await asyncio.sleep(0)
                    return verify(t, settings.mode == 'demo')
                return await asyncio.gather(*(one(t) for t in topics))
            topics = await self.stage(run, 'verify', verify_all)
            stage = 'analyst'
            async def assess_all():
                sem = asyncio.Semaphore(2)
                async def one(i, t):
                    async with sem:
                        return await assess(t, settings.llm_enabled and i < 5)
                return await asyncio.gather(*(one(i, t) for i, t in enumerate(topics)))
            topics = await self.stage(run, 'analyst', assess_all)
            self.emit(run, 'analyst', 'info', f"{sum(t['assessment']['engine']=='llm' for t in topics)} 个话题使用本地双角色研判，其余使用规则")
            stage = 'editor'
            self.emit(run, 'editor', 'running', '归档带证据的观测快照')
            snapshot = {'run_id': run['id'], 'mode': settings.mode, 'coverage': coverage,
                        'generated_at': now.isoformat(), 'sample_count': sum(t['total'] for t in topics),
                        'raw_count': len(posts), 'rejected': rejected, 'topics': topics,
                        'window_minutes': 15, 'queries': settings.queries}
            await asyncio.sleep(0)
            # Publish only after every stage succeeded. Cancellation retains previous snapshot.
            self.store.publish(settings.dataset_key(), snapshot)
            self.emit(run, 'editor', 'done', f'归档 {len(topics)} 个话题')
            run['state'] = 'done'
        except asyncio.CancelledError:
            self.emit(run, stage, 'cancelled', '扫描已取消，保留上一份完整快照')
            run['state'] = 'cancelled'
        except Exception as exc:
            if isinstance(exc, SourceError) and exc.retry_after:
                self.cooldown_until = utcnow().timestamp() + exc.retry_after
            if isinstance(exc, SourceError) and exc.pause:
                self.store.save_settings(settings.model_copy(update={'auto_scan': False}))
            message = str(exc) if isinstance(exc, SourceError) else f'执行失败 ({type(exc).__name__})；请检查配置与采集格式'
            run.update(state='failed', error=message)
            self.emit(run, stage, 'failed', message)
        finally:
            run['finished'] = utcnow().isoformat()
            self.store.run(run)
            self.active = None

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                # A task cancelled before entering execute cannot run its finally block.
                if self.active:
                    run = self.active
                    run.update(state='cancelled', finished=utcnow().isoformat())
                    self.emit(run, 'scout', 'cancelled', '扫描已取消')
                    self.store.run(run)
                    self.active = None

    async def scheduler(self):
        while True:
            await asyncio.sleep(5)
            settings = self.store.settings()
            if settings.auto_scan and utcnow().timestamp()-self.last_started >= settings.interval_seconds:
                if utcnow().timestamp() >= self.cooldown_until and (not self.task or self.task.done()):
                    self.start()
