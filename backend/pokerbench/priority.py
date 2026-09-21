"""Admission priority for formal matches, with capacity visitors cannot occupy."""
import asyncio
from contextlib import asynccontextmanager


class PriorityPool:
    def __init__(self, limit=128, reserved=2):
        if not 0 < reserved < limit:
            raise ValueError('Reserved capacity must be smaller than total capacity')
        self.limit, self.reserved = limit, reserved
        self.active = self.regular = self.waiting_priority = 0
        self.changed = asyncio.Condition()

    @asynccontextmanager
    async def slot(self, *, priority=False):
        async with self.changed:
            if priority:
                self.waiting_priority += 1
            try:
                await self.changed.wait_for(lambda: self.active < self.limit and (
                    priority or self.regular < self.limit-self.reserved and not self.waiting_priority))
                self.active += 1
                if not priority:
                    self.regular += 1
            finally:
                if priority:
                    self.waiting_priority -= 1
                    self.changed.notify_all()
        try:
            yield
        finally:
            async with self.changed:
                self.active -= 1
                if not priority:
                    self.regular -= 1
                self.changed.notify_all()
