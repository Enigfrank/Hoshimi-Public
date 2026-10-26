import json
import hashlib
import re
import asyncio
from pathlib import Path
from urllib.request import Request, urlopen

from config import CLIENT_VERSION_NAME, HOTFIX_BASE_URL, STREAMING_ASSETS_DIR, RESOURCE_DOWNLOAD_URL, RESOURCE_DOWNLOAD_MODE

VOICE_HASH_PATTERN = re.compile(r'^voice_hash_([A-Za-z]{2,8})_(\d+)\.bytes$')
RESOURCE_HASH_PATTERN = re.compile(r'^[0-9a-f]{32}\.ys$')
VOICE_LIST_PATTERN = re.compile(r'^voice_package_list(_\d+_\d+)?\.bytes$')

_voice_state: dict = {'signature': None, 'packages': {}}
_resource_state: dict = {'signature': None, 'files': {}}


def voice_root() -> Path:
    """返回本机语音资源根目录，位于 StreamingAssets/Voice。"""
    return STREAMING_ASSETS_DIR.parent / 'Voice'


def _voice_signature() -> tuple:
    """计算语音散列文件签名，文件变动时用于刷新缓存。"""
    root = voice_root()
    if not root.is_dir():
        return ()
    signature = []
    for path in sorted(root.glob('*/voice_hash_*.bytes')):
        try:
            info = path.stat()
        except OSError:
            continue
        signature.append((str(path), info.st_mtime_ns, info.st_size))
    return tuple(signature)


def voice_packages() -> dict:
    """读取本机语音散列文件，返回按语言索引的版本、大小与内容散列表。"""
    signature = _voice_signature()
    if _voice_state['signature'] == signature:
        return _voice_state['packages']
    packages: dict = {}
    root = voice_root()
    if not root.is_dir():
        _voice_state.update(signature=signature, packages=packages)
        return packages
    for path in sorted(root.glob('*/voice_hash_*.bytes')):
        language = path.parent.name
        matched = VOICE_HASH_PATTERN.match(path.name)
        if not matched or matched.group(1) != language:
            print(f'[语音清单跳过] 文件名无效：{path.name}')
            continue
        try:
            document = json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            print(f'[语音清单跳过] 无法解析 {path.name}：{error}')
            continue
        version = int(document.get('version', -1))
        size = int(document.get('size', -1))
        rows = document.get('assetHashList')
        if (document.get('versionName') != language or version != int(matched.group(2))
                or version <= 0 or size <= 0 or not isinstance(rows, list)):
            print(f'[语音清单跳过] 名称与内容不一致：{path.name}')
            continue
        entries: dict = {}
        broken = False
        for row in rows:
            parts = row.split('|')
            if len(parts) != 3 or not re.fullmatch(r'[0-9a-f]{32}', parts[1]):
                broken = True
                break
            entries[parts[1]] = int(parts[2])
        if broken:
            print(f'[语音清单跳过] 条目格式无效：{path.name}')
            continue
        existing = packages.get(language)
        if existing is not None and existing['version'] >= version:
            # 同一语言存在多个版本时只保留最高版本，避免清单指向旧语音。
            continue
        packages[language] = {'version': version, 'size': size, 'entries': entries, 'path': path}
    _voice_state.update(signature=signature, packages=packages)
    return packages


def voice_package_list() -> bytes:
    """按本机语音散列合成语音包清单，版本与大小与官方一致。"""
    packages = voice_packages()
    if not packages:
        raise FileNotFoundError('本机没有语音散列文件')
    infos = [{'name': language, 'version': package['version'], 'size': package['size']}
             for language, package in sorted(packages.items())]
    return json.dumps({'infos': infos}, separators=(',', ':')).encode('utf-8')


def voice_hash_file(language: str, version: int) -> bytes:
    """返回指定语言与版本的语音散列正文，版本不符时拒绝下发。"""
    package = voice_packages().get(language)
    if package is None or package['version'] != version:
        raise FileNotFoundError('语音版本不存在')
    return package['path'].read_bytes()


def resource_files() -> dict:
    """索引主清单与语音散列中的内容散列和大小，只转发已知资源。"""
    manifest_path = STREAMING_ASSETS_DIR / 'AssetHash_Info.bytes'
    signature = (_voice_signature(), manifest_path.stat().st_mtime_ns, manifest_path.stat().st_size)
    if _resource_state['signature'] == signature:
        return _resource_state['files']
    manifest = json.loads(manifest_path.read_bytes())
    files: dict = {}
    conflicts: set = set()
    for row in manifest['assetHashList']:
        parts = row.split('|')
        if len(parts) != 3 or not parts[0].endswith('.ys'):
            continue
        files[parts[1] + '.ys'] = int(parts[2])
    for package in voice_packages().values():
        for digest, size in package['entries'].items():
            key = digest + '.ys'
            if key in conflicts:
                continue
            if key in files and files[key] != size:
                conflicts.add(key)
                files.pop(key, None)
                print(f'[资源散列冲突] {key} 大小不一致，已拒绝登记')
                continue
            files[key] = size
    _resource_state.update(signature=signature, files=files)
    return files


def clear_hotfix_cache() -> None:
    """清空语音与资源索引缓存，供测试或人工刷新后重新读取。"""
    _voice_state.update(signature=None, packages={})
    _resource_state.update(signature=None, files={})


def local_resource_path(filename: str) -> Path | None:
    """在本机散列目录与语音目录中定位资源文件，找不到时返回空。"""
    candidates = [STREAMING_ASSETS_DIR / filename]
    if RESOURCE_HASH_PATTERN.match(filename):
        candidates.append(STREAMING_ASSETS_DIR / filename[0] / filename[1] / filename)
        for language in voice_packages():
            candidates.append(voice_root() / language / filename[0] / filename[1] / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def resource_size_matches(filename: str, path: Path) -> bool:
    """校验本机资源大小与清单一致，损坏文件不直接下发。"""
    expected = resource_files().get(filename)
    if expected is None:
        return True
    try:
        return path.stat().st_size == expected
    except OSError:
        return False


def remote_resource_url(filename: str) -> str | None:
    """本机缺少的有效 bundle 使用官服同版本内容散列地址按需下载。"""
    if filename not in resource_files():
        return None
    local = local_resource_path(filename)
    if local is None or not resource_size_matches(filename, local):
        return RESOURCE_DOWNLOAD_URL.rstrip('/') + '/' + filename
    return None


def _content_range_total(value: str) -> int | None:
    """解析 Content-Range 中的资源总长度。"""
    matched = re.fullmatch(r'bytes\s+\d+-\d+/(\d+)', value.strip())
    return int(matched.group(1)) if matched else None


async def stream_remote_resource(filename: str, writer, range_header: str = '') -> None:
    """分块转发官方资源，避免客户端不支持重定向或大文件占满内存。"""
    url = remote_resource_url(filename)
    if not url:
        raise ValueError('资源不在当前清单中')
    headers = {}
    if range_header:
        if not re.fullmatch(r'bytes=\d+-\d*', range_header):
            writer.write(b'HTTP/1.1 416 Range Not Satisfiable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await writer.drain()
            return
        headers['Range'] = range_header
    response = None
    headers_sent = False
    try:
        response = await asyncio.to_thread(urlopen, Request(url, headers=headers), timeout=30)
        length = int(response.headers['Content-Length'])
        status = response.status
        expected = resource_files()[filename]
        if status == 200 and length != expected:
            raise ValueError('资源大小与当前清单不符')
        if status == 206:
            total = _content_range_total(response.headers.get('Content-Range', ''))
            if total is None or total != expected:
                raise ValueError('续传总长度与当前清单不符')
        elif status != 200:
            raise ValueError('资源大小或状态码与当前清单不符')
        header = f'HTTP/1.1 {status} {"Partial Content" if status == 206 else "OK"}\r\nContent-Type: application/octet-stream\r\nContent-Length: {length}\r\nConnection: close\r\n'
        if status == 206:
            header += f'Content-Range: {response.headers["Content-Range"]}\r\n'
        writer.write((header + '\r\n').encode('ascii'))
        headers_sent = True
        remaining = length
        while remaining:
            chunk = await asyncio.to_thread(response.read, min(256 * 1024, remaining))
            if not chunk:
                raise OSError('上游资源提前结束')
            writer.write(chunk)
            await writer.drain()
            remaining -= len(chunk)
        print(f'[资源下载] 已转发 {filename}，{length} 字节')
    except Exception as error:
        print(f'[资源下载失败] {filename}：{error}')
        if not headers_sent:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await writer.drain()
    finally:
        if response is not None:
            response.close()


def build_update_response() -> dict:
    """根据本机资源清单生成安装版本与资源版本，避免下发过期的固定版本。"""
    manifest_bytes = (STREAMING_ASSETS_DIR / "AssetHash_Info.bytes").read_bytes()
    manifest = json.loads(manifest_bytes)
    app_version = str(manifest["appVersion"])
    build_code = str(manifest["buildCode"])
    direct = RESOURCE_DOWNLOAD_MODE == 'direct'
    digest = hashlib.md5(manifest_bytes).hexdigest()
    assethash = f"assethash_{app_version}_{build_code}_{digest}.bytes" if direct else f"assethash_{app_version}_{build_code}.bytes"
    versions = []
    for update_type, version, version_name in (
        ("install", app_version, CLIENT_VERSION_NAME),
        ("noInstall", build_code, manifest["versionName"]),
    ):
        versions.append({
            "createDate": 1700000000000,
            "modifyDate": 1700000000000,
            "sortWeight": 0,
            "type": update_type,
            "version": version,
            "versionName": version_name,
            "matchedAppVersion": app_version,
            "forceUpdate": False,
            "fileSize": len(manifest_bytes),
            "disDate": 1700000000000,
            "downloadUrl": RESOURCE_DOWNLOAD_URL.rstrip('/') + '/' if direct and update_type == 'noInstall' else HOTFIX_BASE_URL,
            "platformType": "pc",
            "extraData": "",
            "assethash": assethash,
        })
    return {
        "code": 0,
        "errorCode": "0",
        "message": "success",
        "errorMsg": "",
        "data": versions,
    }


def _manifest_voice_package() -> bytes:
    """返回按本机语音散列合成的语音包清单。"""
    return voice_package_list()


def read_hotfix_file(filename: str) -> bytes:
    """读取热更新文件，将版本化的资源清单请求映射到客户端现有清单。"""
    if '/' in filename or '\\' in filename or filename in ('.', '..'):
        raise ValueError('资源文件名无效')
    if VOICE_LIST_PATTERN.match(filename):
        return _manifest_voice_package()
    matched = VOICE_HASH_PATTERN.match(filename)
    if matched:
        return voice_hash_file(matched.group(1), int(matched.group(2)))
    target = STREAMING_ASSETS_DIR / filename
    if target.is_file():
        return target.read_bytes()
    if RESOURCE_HASH_PATTERN.match(filename):
        local = local_resource_path(filename)
        if local is not None:
            return local.read_bytes()
    if filename.startswith("assethash_") and filename.endswith(".bytes"):
        return (STREAMING_ASSETS_DIR / "AssetHash_Info.bytes").read_bytes()
    raise FileNotFoundError('热更新资源不存在')
