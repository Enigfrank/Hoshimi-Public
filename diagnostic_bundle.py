"""只读收集诊断资料，先预览日志快照，再下载同一份 ZIP。"""

from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from zipfile import ZipFile, ZIP_DEFLATED

import config
import protocol_coverage

FILE_LIMIT = 4 * 1024 * 1024
TOTAL_LIMIT = 16 * 1024 * 1024
FILE_COUNT = 64
PREVIEW_TTL = 600
_lock = threading.Lock()
_snapshot = None
_timestamp = re.compile(r'^\s*\[?\s*(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)')


def parse_time(value):
    """解析带时区的 ISO 时间，拒绝缺少时区或非字符串输入。"""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('开始和结束时间须为带时区的 ISO 时间')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('开始和结束时间须为带时区的 ISO 时间') from None
    if parsed.utcoffset() is None:
        raise ValueError('开始和结束时间必须包含时区')
    return parsed.timestamp()


def filter_log(text, start, end, modified):
    """按行时间及其续行筛选；整文件无时间时使用修改时间。"""
    rows, current, has_time = [], None, False
    for line in text.splitlines():
        match = _timestamp.match(line)
        if match:
            try:
                current = datetime.fromisoformat(match[1].replace('/', '-').replace(',', '.').replace('Z', '+00:00')).timestamp()
                has_time = True
            except ValueError:
                pass
        if current is not None and start <= current <= end:
            rows.append(line)
    if not has_time:
        return (text if start <= modified <= end else ''), 'mtime'
    return '\n'.join(rows) + ('\n' if rows else ''), 'line_time'


def collect_logs(directory, group, start, end, warnings, budget):
    """从固定目录读取普通日志，保留文件名和所选内容，拒绝链接并限制读取量。"""
    files = []
    try:
        for parent in (directory, *directory.parents):
            info = parent.lstat()
            if parent.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                warnings.append(f'{group}：日志目录包含链接，已跳过')
                return files, budget
        candidates = []
        for path in directory.iterdir():
            if not re.fullmatch(r'.+\.(?:log(?:\.[1-3])?|txt)', path.name, re.I):
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                continue
            candidates.append((info.st_mtime, path.name, path, info))
    except OSError:
        warnings.append(f'{group}：日志目录不存在或无法读取')
        return files, budget
    candidates.sort(reverse=True)
    if len(candidates) > FILE_COUNT:
        warnings.append(f'{group}：仅检查最近 {FILE_COUNT} 个日志文件')
    for modified, _, path, original in candidates[:FILE_COUNT]:
        if modified < start:
            continue
        if budget <= 0:
            warnings.append(f'{group}：已达到此来源读取上限，剩余日志未收集')
            break
        name = f'{group}/{path.name}'
        limit = min(FILE_LIMIT, budget)
        try:
            with path.open('rb') as handle:
                # 打开后重新确认句柄仍是普通文件，避免把设备或目录当日志读取。
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(original, opened):
                    continue
                size = opened.st_size
                prefix = handle.read(min(2, limit))
                truncated = size > limit
                if truncated:
                    offset = size - (limit - len(prefix))
                    if prefix in (b'\xff\xfe', b'\xfe\xff') and offset % 2:
                        offset += 1
                    handle.seek(offset)
                    raw = handle.read(limit - len(prefix))
                else:
                    raw = prefix + handle.read(limit - len(prefix))
        except OSError:
            warnings.append(f'{group}：有日志无法读取，已跳过')
            continue
        budget -= len(raw) + (len(prefix) if truncated else 0)
        if truncated:
            warnings.append(f'{name}：仅读取文件末尾，较早日志可能缺失')
            if prefix in (b'\xff\xfe', b'\xfe\xff'):
                raw = prefix + raw
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = raw.decode('utf-16', errors='replace')
        else:
            text = raw.decode('utf-8-sig', errors='replace')
        if '\ufffd' in text:
            warnings.append(f'{name}：无法解码的字符已替换')
        if truncated:
            text = text.partition('\n')[2]
        text, selection = filter_log(text, start, end, modified)
        if not text:
            continue
        if selection == 'mtime':
            warnings.append(f'{name}：无可识别行时间，按文件修改时间筛选')
        files.append({'name': name, 'selection': selection, 'content': text})
    if not files:
        warnings.append(f'{group}：未收集到所选时间范围的日志')
    return files, budget


def build_preview(values):
    """收集白名单配置、版本及未脱敏日志，缓存十分钟的单份 ZIP 快照。"""
    global _snapshot
    start, end = parse_time(values.get('start')), parse_time(values.get('end'))
    if start >= end:
        raise ValueError('结束时间必须晚于开始时间')
    home, client = Path(config.BASE_DIR).resolve(), Path(config.GAME_CLIENT_DIR).absolute()
    warnings = []
    summary = {
        'implementation': 'Python', 'server_version': '源码运行（无编译版本号）',
        'client_version': config.CLIENT_VERSION, 'client_version_name': config.CLIENT_VERSION_NAME,
        'http_port': config.HTTP_PROXY_PORT, 'game_port': config.TCP_GATEWAY_PORT,
        'https_port': config.HTTPS_DIRECT_PORT, 'enable_https': config.ENABLE_HTTPS,
        'resource_mode': config.RESOURCE_DOWNLOAD_MODE,
    }
    files = [{'name': 'summary.json', 'selection': 'snapshot', 'content': json.dumps(summary, ensure_ascii=False, indent=2)}]
    # 兜底统计为当前累计快照，不混称为所选时段内的命中数。
    fallback = protocol_coverage.fallback_summary()
    files.append({'name': 'protocol-fallback.json', 'selection': 'cumulative_snapshot',
                  'content': json.dumps({'scope': '累计快照，不按所选日志时段过滤', 'fallback': fallback}, ensure_ascii=False, indent=2)})
    for directory, group in ((home / 'logs', 'server-logs'), (client / 'CrashSightLog', 'crashsight-logs')):
        # 两类日志各保留一半预算，避免服务端日志占满后无法收集闪退日志。
        collected, _ = collect_logs(directory, group, start, end, warnings, TOTAL_LIMIT // 2)
        files.extend(collected)
    manifest = {'start': values['start'], 'end': values['end'], 'warnings': warnings,
                'limits': {'file_bytes': FILE_LIMIT, 'total_read_bytes': TOTAL_LIMIT,
                           'source_read_bytes': TOTAL_LIMIT // 2, 'files_per_source': FILE_COUNT},
                'files': [{'name': row['name'], 'selection': row['selection']} for row in files]}
    files.insert(0, {'name': 'manifest.json', 'selection': 'snapshot', 'content': json.dumps(manifest, ensure_ascii=False, indent=2)})
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for row in files:
            row['size'] = len(row['content'].encode('utf-8'))
            archive.writestr(row['name'], row['content'])
    token = secrets.token_hex(16)
    with _lock:
        _snapshot = (token, time.monotonic() + PREVIEW_TTL, buffer.getvalue())
    return {'preview_id': token, 'expires_in': PREVIEW_TTL, 'files': files, 'warnings': warnings,
            'zip_bytes': len(buffer.getvalue())}


def download_preview(token):
    """只返回已预览的有效内存快照，拒绝文件路径及任意文件下载。"""
    global _snapshot
    with _lock:
        if _snapshot is None:
            raise ValueError('预览已过期或被替换，请重新生成预览')
        if time.monotonic() > _snapshot[1]:
            _snapshot = None
            raise ValueError('预览已过期或被替换，请重新生成预览')
        if not isinstance(token, str) or not secrets.compare_digest(token, _snapshot[0]):
            raise ValueError('预览已过期或被替换，请重新生成预览')
        return _snapshot[2]


def http_response(path, method, body, content_type):
    """处理已通过 GM 本机和同源校验的预览与下载 JSON 请求。"""
    if method != 'POST':
        return b'{"error":"POST required"}', 'application/json; charset=utf-8', 405
    if content_type.split(';')[0].strip() != 'application/json':
        return b'{"error":"JSON required"}', 'application/json; charset=utf-8', 415
    values = json.loads(body)
    if not isinstance(values, dict):
        raise ValueError('请求体须为对象')
    if path == '/gm/api/diagnostics/export':
        return download_preview(values.get('preview_id')), 'application/zip', 200
    return json.dumps(build_preview(values), ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', 200
