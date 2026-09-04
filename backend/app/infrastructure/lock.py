"""Redis 分布式锁（裁决 D-014：SETNX 排他锁防并发审批）。

- 获取：SET key token NX PX ttl（原子）
- 释放：Lua 脚本校验 token 后删除（防止误删他人锁）
- TTL 兜底：持锁进程崩溃后自动过期，不死锁
"""
import uuid

from app.infrastructure.streams import get_redis

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class DistributedLock:
    def __init__(self, key: str, ttl_ms: int):
        self.key = key
        self.ttl_ms = ttl_ms
        self.token = uuid.uuid4().hex
        self._held = False

    def acquire(self) -> bool:
        """非阻塞获取锁。成功返回 True。"""
        ok = get_redis().set(self.key, self.token, nx=True, px=self.ttl_ms)
        self._held = bool(ok)
        return self._held

    def release(self) -> None:
        """释放锁（Lua 校验 token，仅释放自己持有的锁）。"""
        if self._held:
            get_redis().eval(_RELEASE_LUA, 1, self.key, self.token)
            self._held = False

    def __enter__(self) -> "DistributedLock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()
