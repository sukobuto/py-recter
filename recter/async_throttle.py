from typing import *
import asyncio
import math
from asyncio.futures import TimeoutError
from datetime import datetime

from redis import StrictRedis
from uuid import uuid4

from recter.exceptions import WaitingTimeoutError, RunningTimeoutError


class AsyncThrottle:

    def __init__(self, redis: StrictRedis, name: str, max_parallels: int,
                 polling_interval=0.01, garbage_check_window=3, garbage_check_interval_count=10):
        self.redis = redis
        self.name = name
        self.max_parallels = max_parallels
        self.polling_interval = polling_interval
        self.garbage_check_window = garbage_check_window
        self.garbage_check_interval_count = garbage_check_interval_count

    @property
    def __key(self):
        return 'recter:' + self.name

    async def wait(self, timeout: float) -> bytes:
        token = str(uuid4()).encode('utf8')
        timestamp = datetime.now().timestamp()
        key = self.__key
        self.redis.zadd(key, timestamp, token)
        count = 0
        while True:
            cleared = self.redis.zrange(key, 0, self.max_parallels - 1)
            if token in cleared:
                return token
            await asyncio.sleep(self.polling_interval)
            count += 1
            if count % self.garbage_check_interval_count == 0:
                self.remove_garbage(cleared)
            if datetime.now().timestamp() - timestamp > timeout:
                self.exit(token)
                raise WaitingTimeoutError()

    def register_as_running(self, token: bytes, running_timeout):
        self.redis.setex(self.__key + ':' + token.decode('utf8'), math.ceil(running_timeout), token)

    def exit(self, token: bytes):
        self.redis.zrem(self.__key, token)
        self.redis.delete(self.__key + ':' + token.decode('utf8'))

    def remove_garbage(self, tokens: List[bytes]):
        for token in tokens[:self.garbage_check_window]:
            if not self.redis.exists(self.__key + ':' + token.decode('utf8')):
                self.exit(token)

    async def run(self, coroutine, waiting_timeout=10.0, running_timeout=10.0):
        token = await self.wait(waiting_timeout)
        try:
            self.register_as_running(token, running_timeout)
            result = await asyncio.wait_for(coroutine, running_timeout)
            return result
        except TimeoutError as te:
            raise RunningTimeoutError(te)
        finally:
            self.exit(token)
