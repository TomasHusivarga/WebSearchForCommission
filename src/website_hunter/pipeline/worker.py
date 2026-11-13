import asyncio

from website_hunter.pipeline.service import CandidatePipeline
from website_hunter.storage.repository import Repository


class JobWorker:
    def __init__(self, repository: Repository | None = None):
        self.repository = repository or Repository()
        self.pipeline = CandidatePipeline(self.repository)
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            await self._task

    async def _run(self) -> None:
        while not self._stopping:
            job = await asyncio.to_thread(self.repository.claim_job)
            if job:
                await asyncio.to_thread(self.pipeline.process, job)
            else:
                await asyncio.sleep(0.5)
