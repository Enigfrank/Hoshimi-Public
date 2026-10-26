"""按当前客户端清单读取本机资源包的文本，产物只保存在内存中。"""

from functools import lru_cache
import hashlib
import json
import re

from config import GAME_CLIENT_DIR, STREAMING_ASSETS_DIR


@lru_cache(maxsize=8)
def client_texts(resource_name: str) -> dict[str, bytes]:
    """核对散列和大小后读取 TextAsset，不把客户端地图或脚本编进服务端。"""
    import UnityPy
    rows = json.loads((STREAMING_ASSETS_DIR/'AssetHash_Info.bytes').read_bytes())['assetHashList']
    matches = [r.split('|') for r in rows if r.split('|')[0] == resource_name]
    if len(matches) != 1:
        raise ValueError('客户端资源清单没有唯一的地图资源包')
    _, digest, size = matches[0]
    if not re.fullmatch('[0-9a-f]{32}', digest):
        raise ValueError('客户端资源散列无效')
    path = STREAMING_ASSETS_DIR/digest[0]/digest[1]/(digest+'.ys')
    if not path.is_file():
        path = STREAMING_ASSETS_DIR/(digest+'.ys')
    raw = path.read_bytes()
    if len(raw) != int(size) or hashlib.md5(raw).hexdigest() != digest:
        raise ValueError('客户端地图资源大小或散列不匹配')
    versions = re.findall(rb'20\d\d\.\d+\.\d+[fp]\d+', (GAME_CLIENT_DIR/'AetherGazer_Data/globalgamemanagers').read_bytes())
    if not versions:
        raise ValueError('客户端引擎版本无法读取')
    UnityPy.config.FALLBACK_UNITY_VERSION = versions[0].decode('ascii')
    index = raw.find(b'UnityFS')
    if index < 0:
        raise ValueError('客户端资源包格式无效')
    assets = {}
    for obj in UnityPy.load(raw[index:]).objects:
        if obj.type.name != 'TextAsset':
            continue
        asset = obj.read()
        value = asset.m_Script
        data = value.encode('utf-8', 'surrogateescape') if isinstance(value, str) else bytes(value)
        if asset.m_Name in assets and assets[asset.m_Name] != data:
            raise ValueError('地图资源中存在内容不同的重名文本')
        assets[asset.m_Name] = data
    return assets
