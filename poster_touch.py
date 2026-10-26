"""大厅看板触摸上报：把客户端上报的触摸次数累计到账号，供誓约任务判定使用。

``38014`` 是单向上传协议，没有对应的 SC，只能由服务端解析 ``log_list``
并把 ``poster_touch`` 事件的累计次数写进 ``poster_touches``。

事件结构固定为::

    {"event_name": "poster_touch",
     "attribute_json": "{"scene_id":"6000","hero_id":1084,"position":0,"touch_times":1}"}

``hero_id`` 传的是皮肤编号，必须经 ``SkinCfg`` 映射回角色；角色或皮肤未拥有
时拒绝该条记录，不做累计。``touch_times`` 客户端只用 1 和 3 两种值，
分别是单次点击与三连击，其他值一律拒绝，避免刷取誓约任务的触摸要求。
``38014`` 同时承载其他埋点事件，非 ``poster_touch`` 的条目直接跳过，
只有目标事件的非法内容才报错，保证上报通道不被无关事件拖垮。
"""

from functools import lru_cache
import json

from gameplay_protocol import read_client_data
from skin_scene_data import skin_available

# 上报事件名，只有该事件与誓约任务的触摸次数相关。
POSTER_TOUCH_EVENT = 'poster_touch'
# 客户端实际使用的两种单次上报次数：三连击与单次点击。
ALLOWED_TOUCH_TIMES = (1, 3)
# 单条事件的属性文本长度上限，避免异常报文占用解析时间。
ATTRIBUTE_LIMIT = 512


@lru_cache(maxsize=4096)
def skin_hero(skin_id: int) -> int:
    """按 ``SkinCfg`` 把皮肤编号映射回角色编号，非皮肤返回 0。"""
    return int(read_client_data(
        f"(function() local c=require('SkinCfg')[{int(skin_id)}]; "
        f"return c and c.hero or 0 end)()"))


def save_poster_touch(user: dict, request: dict) -> None:
    """累计大厅触摸次数，上报内容无效时抛出 ``ValueError``。

    整批先校验再落盘，任何一条目标事件非法都不改变已累计的次数。
    全部条目都不是触摸事件时视为无操作，不报错也不落盘。
    """
    rows = list(request.get('log_list') or [])
    if not rows:
        raise ValueError('触摸上报列表为空')
    planned: list[tuple[int, int]] = []
    for row in rows:
        if not _is_touch_event(row):
            continue
        hero_id, times = _parse_event(user, row)
        if times not in ALLOWED_TOUCH_TIMES:
            raise ValueError('触摸次数无效')
        planned.append((hero_id, times))
    if not planned:
        return
    saved = user.setdefault('poster_touches', {})
    for hero_id, times in planned:
        key = str(hero_id)
        saved[key] = int(saved.get(key, 0)) + times


def poster_touch_count(user: dict, hero_id: int) -> int:
    """读取该角色累计的大厅触摸次数，未触摸过时为零。"""
    return int((user.get('poster_touches') or {}).get(str(hero_id), 0))


def _is_touch_event(row) -> bool:
    """判断单条上报是否为目标触摸事件，非目标事件用于跳过。"""
    return isinstance(row, dict) and row.get('event_name') == POSTER_TOUCH_EVENT


def _parse_event(user: dict, row: dict) -> tuple[int, int]:
    """解析单条触摸事件，返回角色编号与本次累计次数。"""
    raw = row.get('attribute_json') or ''
    if not isinstance(raw, str) or len(raw) > ATTRIBUTE_LIMIT:
        raise ValueError('触摸事件属性无效')
    try:
        attribute = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError('触摸事件属性不是合法 JSON') from error
    if not isinstance(attribute, dict):
        raise ValueError('触摸事件属性不是对象')
    skin_id = int(attribute.get('hero_id') or 0)
    hero_id = skin_hero(skin_id)
    if not hero_id:
        raise ValueError('触摸上报的角色或皮肤不存在')
    hero = next((h for h in user.get('heroes', [])
                 if h.get('id') == hero_id and h.get('unlock', 1)), None)
    # 上报的是皮肤编号，必须确实持有该形态；默认形态与皮肤共用这套校验。
    if not hero or not skin_available(user, hero, skin_id):
        raise ValueError('触摸上报的角色或皮肤未拥有')
    return int(hero_id), int(attribute.get('touch_times') or 0)
