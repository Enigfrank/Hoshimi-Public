"""服务端启动入口。核心模块位于 agl_server 包内。"""

import asyncio
import sys

from agl_server.server import *
from agl_server.server import main
from config import BASE_DIR


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    from agl_server.diagnostic_logging import install_log_capture
    install_log_capture(BASE_DIR)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[服务端] 收到关闭请求。")
