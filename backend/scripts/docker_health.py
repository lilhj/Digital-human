"""Docker 健康检查脚本：检查 Redis（Worker 依赖）连通性。

用法: python scripts/docker_health.py [redis_url]
退出码 0 = 健康；非 0 = 不健康（docker 会按 restart 策略重启）。
"""
import sys

import redis


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "redis://redis:6379/0"
    try:
        r = redis.Redis.from_url(url, socket_timeout=3)
        assert r.ping(), "Redis PING 失败"
        print("健康检查通过")
    except Exception as e:  # noqa: BLE001
        print(f"健康检查失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
