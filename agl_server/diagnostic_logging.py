"""把 Python 控制台输出同时保存为有时间戳、可轮转的诊断日志。"""

from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading


class _LogHandler(RotatingFileHandler):
    """日志写入失败仅向原始控制台告警，避免递归记录错误。"""

    def handleError(self, record):
        """磁盘不可写时保留控制台运行，不把错误写回日志包装器。"""
        if not getattr(self, '_warned', False):
            self._warned = True
            sys.__stderr__.write('[日志] 无法写入运行日志，控制台输出不受影响。\n')


class _LogStream:
    """保持原始控制台行为，按完整行同步写入共享日志处理器。"""

    def __init__(self, console, handler):
        """保存原始流和共享处理器，并为当前流维护未结束的行。"""
        self.console, self.handler = console, handler
        self.pending = ''
        self.lock = threading.RLock()

    def write(self, text):
        """输出原文，完整行追加本机时间戳后落盘。"""
        with self.lock:
            count = self.console.write(text)
            self.pending += text
            while '\n' in self.pending:
                line, self.pending = self.pending.split('\n', 1)
                self.handler.handle(logging.LogRecord('hoshimi', logging.INFO, '', 0, line.rstrip('\r'), (), None))
            return count

    def flush(self):
        """刷新控制台和文件；完整行在 write 时已写入。"""
        self.console.flush()
        self.handler.flush()

    def __getattr__(self, name):
        """将编码、终端状态等未改动的流属性交还原始控制台。"""
        return getattr(self.console, name)


def install_log_capture(base):
    """创建独立启动日志，单文件 10 MiB、保留三份轮转文件。"""
    directory = Path(base) / 'logs'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime('%Y-%m-%dT%H-%M-%S-%f') + '.log'
        handler = _LogHandler(directory / filename, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    except OSError:
        print('[日志] 无法创建运行日志，控制台输出不受影响。', file=sys.stderr)
        return
    sys.stdout, sys.stderr = _LogStream(sys.stdout, handler), _LogStream(sys.stderr, handler)
