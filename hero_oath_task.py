"""誓约任务进度事实：按存档中的真实记录计算每个誓约任务的当前进度。

``WeddingAssignmentCfg`` 的一级与二级各四项，条件编号在客户端
``WeddingAssignmentCfg`` 与 ``ConditionCfg`` 之外没有第二份解释表，因此这里
按任务配置里的 ``condition`` 与 ``additional_parameter`` 展开：

- ``410000``：大厅触摸次数，来自 ``38014`` 上报的 ``poster_touch`` 累计；
- ``800``：指定心链剧情已读，取 ``read_stories``；
- ``110``：熟练度达到 ``additional_parameter[1]``；
- ``2022``：角色在游园街有宿舍；
- ``410003``：``hero.clear_times`` 达到 ``need``；
- ``410001``：宿舍赠送家具累计数量达到 ``need``；
- ``410002``：助理任期累计天数达到 ``need``，含当前未结束的任期；
- ``401``：指定关卡通关，取 ``battle_progress`` 的真实通关次数。

任务进度只读取既有存档，不写入任何字段，重复查询结果必须一致。
"""

from functools import lru_cache
import time

from gameplay_protocol import read_client_data
from poster_touch import poster_touch_count


@lru_cache(maxsize=1)
def mastery_level_max() -> int:
    """读取客户端熟练度上限，缺省按配置缺项处理为 100。"""
    value = read_client_data("require('GameSetting').mastery_level_max.value[1]")
    return int(value) if value else 100


def task_progress_value(user: dict, task: dict) -> int:
    """按任务条件与参数计算单个誓约任务的进度，供提交门禁与客户端展示共用。"""
    kind = int(task['condition'])
    params = task.get('additional_parameter') or []
    hero_id = int(task['hero_id'])
    if kind == 410000:
        return poster_touch_count(user, hero_id)
    if kind == 800:
        return 1 if params and params[0] in (user.get('read_stories') or []) else 0
    if kind == 110:
        # need 为 1，条件参数才是熟练度门槛，因此返回是否达标而不是熟练度本身。
        need_value = int(params[1]) if len(params) > 1 else 30
        hero = _owned_hero(user, hero_id)
        mastery = min(int(hero.get('clear_times', 0)), mastery_level_max())
        return int(mastery >= need_value)
    if kind == 2022:
        return 1 if _hero_has_dorm(user, hero_id) else 0
    if kind == 410003:
        hero = _owned_hero(user, hero_id)
        return int(hero.get('clear_times', 0))
    if kind == 410001:
        return dorm_gift_count(user, hero_id)
    if kind == 410002:
        return assistant_term_days(user, hero_id)
    if kind == 401:
        return 1 if params and _stage_cleared(user, params[0]) else 0
    return 0


def _owned_hero(user: dict, hero_id: int) -> dict:
    """返回已拥有的角色存档，未拥有时视为全部事实为零。"""
    return next((h for h in user.get('heroes', [])
                 if h.get('id') == hero_id and h.get('unlock', 1)), {})


def _stage_cleared(user: dict, stage: int) -> bool:
    """读取真实通关记录，普通账号的入口解锁不算完成。"""
    return user.get('battle_progress', {}).get(f'1:{int(stage)}', {}).get('clear_times', 0) > 0


def archive_of(hero_id: int) -> int:
    """不同形态角色共享同一个游园街档案编号。"""
    from backhome_catalog import archive_id
    return archive_id(hero_id)


def _hero_has_dorm(user: dict, hero_id: int) -> bool:
    """角色宿舍已存在即为拥有，不要求达到任何等级。"""
    return any(archive_of(hero_id) in (room.get('archives_id') or [])
               for room in _rooms(user))


def dorm_gift_count(user: dict, hero_id: int) -> int:
    """统计该角色宿舍累计赠送家具数量，跨房间合计。"""
    aid = archive_of(hero_id)
    return sum(int(row.get('num', 0)) for room in _rooms(user)
               if aid in (room.get('archives_id') or [])
               for row in room.get('give_furnitures') or [])


def _rooms(user: dict) -> list[dict]:
    """返回游园街房间存档，尚未进入游园街时为空。"""
    return (user.get('backhome') or {}).get('rooms') or []


def assistant_term_days(user: dict, hero_id: int, now: int | None = None) -> int:
    """累计助理任期天数，包含当前仍未结束的任期。

    任期在 ``32016`` 切换看板角色、``32132`` 用皮肤重选看板角色时记录；
    当前任期以 ``profile.poster_girl`` 为准，结束时间按当前时刻计算。
    """
    moment = int(time.time()) if now is None else int(now)
    from profile_data import poster_girl_terms
    terms = [row for row in poster_girl_terms(user) if int(row['hero_id']) == hero_id]
    if not terms:
        return 0
    current = int((user.get('profile') or {}).get('poster_girl', 0))
    total = 0
    for row in terms:
        start = int(row.get('start_time', 0))
        end = int(row.get('end_time', 0))
        if not start:
            continue
        if not end and current != hero_id:
            continue
        total += max(0, (end or moment) - start)
    return total // 86400
