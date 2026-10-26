"""夏日棋盘地图查询，直接从玩家客户端读取，不附带官方地图内容。"""

from functools import lru_cache
import json

from client_text_assets import client_texts
from resident_catalog import resident_config


@lru_cache(maxsize=16)
def board_map(chapter: int) -> dict:
    """校验章节配置、地图编号、网格范围与出生点，返回实际客户端地图。"""
    cfg = resident_config('ChessBoardLevelCfg', chapter, 'id_level')
    raw = client_texts('combattle/map/chessboard.ys').get(str(chapter))
    if raw is None:
        raise ValueError('本机客户端没有该棋盘地图')
    data = json.loads(raw.decode('utf-8-sig'))
    if str(data['id']) != str(chapter):
        raise ValueError('棋盘地图编号与章节不匹配')
    tiles = {(r['x'], r['z']): r for r in data['mapInfo']}
    if len(tiles) != len(data['mapInfo']) or len(tiles) != data['width'] * data['height']:
        raise ValueError('棋盘地图网格重复或缺失')
    if any(not 0 <= x < data['width'] or not 0 <= z < data['height'] for x, z in tiles):
        raise ValueError('棋盘地图网格超出范围')
    if tuple(cfg['init_pos']) not in tiles:
        raise ValueError('棋盘出生点不在实际地图中')
    return data
