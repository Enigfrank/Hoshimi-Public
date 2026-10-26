"""为原始 SDK 提供本地婚纱照上传配置、签名校验与图片读取。"""

import base64
from datetime import datetime, timezone
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

import config
import user_storage
from account_auth import request_values, sdk_result

PHOTO_BODY_LIMIT = 8 * 1024 * 1024
PHOTO_GRANTS = {}
PHOTO_PATH = re.compile(r'^/photos/([0-9a-f]{24})/([0-9]+)/oath/picture_([0-9]+)_([0-9]+)\.(jpg|png)$')
CONFIG_PATHS = {'/mix-sdk-api/pass/oss/getAppUploadCfg', '/mix-sdk-api/pass/oss/getStsUploadToken'}


def photo_credentials(line, body, headers, manager):
    """只为已认证账号签发十五分钟凭据，禁止空配置冒充成功。"""
    if not config.ENABLE_HTTPS:
        return sdk_result(error='婚纱照上传需要本地 HTTPS，请先配置证书并启用 AGL_ENABLE_HTTPS')
    values = request_values(line, body)
    token = values.get('token') or headers.get('token') or headers.get('clienttoken')
    user = manager.authenticate_token(token) if isinstance(token, str) and token else None
    if not user:
        return sdk_result(error='上传需要有效登录令牌')
    now = time.time()
    for key in list(PHOTO_GRANTS):
        if PHOTO_GRANTS[key]['expires'] <= now:
            del PHOTO_GRANTS[key]
    key = secrets.token_hex(12)
    grant = {'uid': user['uid'], 'token': secrets.token_hex(16),
             'secret': secrets.token_hex(32), 'expires': now + 900}
    PHOTO_GRANTS[key] = grant
    return {'code': 0, 'errorCode': '0', 'errorMsg': '', 'message': 'success', 'data': {
        'accessKeyId': key, 'accessKeySecret': grant['secret'], 'securityToken': grant['token'],
        'expiration': datetime.fromtimestamp(grant['expires'], timezone.utc).isoformat(),
        'bucket': '127', 'endpoint': f'https://0.0.1:{config.HTTPS_DIRECT_PORT}', 'prefix': f'photos/{key}/'}}


def photo_http_response(line, body, headers, manager):
    """处理原始 SDK 的配置、PUT 与 GET；只写入受限照片路径。"""
    method, target, *_ = line.split(' ')
    path = urlsplit(target).path
    if path in CONFIG_PATHS:
        try:
            result = photo_credentials(line, body, headers, manager)
        except (ValueError, TypeError):
            result = sdk_result(error='上传配置请求格式错误')
        return json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8', 200
    if not path.startswith('/photos/'):
        return None
    match = PHOTO_PATH.fullmatch(path)
    if not match:
        return b'Invalid photo path', 'text/plain', 400
    key, owner, uid_text, _, extension = match.groups()
    file = user_storage.DATA_DIR / path.lstrip('/')
    if method in ('GET', 'HEAD'):
        if not file.is_file():
            return b'Not found', 'text/plain', 404
        return file.read_bytes(), 'image/jpeg' if extension == 'jpg' else 'image/png', 200
    if method != 'PUT':
        return b'Method not allowed', 'text/plain', 405
    grant = PHOTO_GRANTS.get(key, {})
    uid = int(uid_text)
    canonical = (f"PUT\n\n{headers.get('content-type', '')}\n{headers.get('date', '')}"
                 f"\nx-oss-security-token:{headers.get('x-oss-security-token', '')}"
                 f"\nx-oss-storage-class:Standard\n/127{path}")
    signature = base64.b64encode(hmac.digest(grant.get('secret', '').encode(), canonical.encode(), 'sha1')).decode()
    if grant.get('uid') != uid or owner not in (str(uid), str(uid + 2**32)) or grant.get('expires', 0) <= time.time() \
            or not hmac.compare_digest(headers.get('x-oss-security-token', ''), grant.get('token', '')) \
            or not hmac.compare_digest(headers.get('authorization', ''), f'OSS {key}:{signature}'):
        return b'Invalid upload credentials', 'text/plain', 403
    if len(body) > PHOTO_BODY_LIMIT:
        return b'Photo exceeds 8 MiB', 'text/plain', 413
    if not ((len(body) >= 100 and body.startswith(b'\xff\xd8\xff') and body.endswith(b'\xff\xd9'))
            or (len(body) >= 45 and body.startswith(b'\x89PNG\r\n\x1a\n') and body[12:16] == b'IHDR'
                and body.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'))):
        return b'Invalid image', 'text/plain', 400
    temporary = file.with_suffix('.' + secrets.token_hex(8) + '.tmp')
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        os.replace(temporary, file)
    except OSError:
        return b'Cannot save photo', 'text/plain', 500
    finally:
        temporary.unlink(missing_ok=True)
    return b'', 'text/plain', 200
