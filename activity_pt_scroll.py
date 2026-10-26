"""轮盘活动状态、配置校验与客户端推送。"""

from copy import deepcopy
from functools import lru_cache

from gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def activity_pt_scroll_catalog() -> dict:
    """读取轮盘关卡与随机池，按活动 ID 归组用于精确校验。"""
    return read_client_data('''(function()
        local result={stages={},random={}}
        local stages=require('ActivityPtRouletteStageCfg')
        for activity_id,stage_ids in pairs(stages.get_id_list_by_activity_id) do
            for _,stage_id in ipairs(stage_ids) do
                local cfg=stages[stage_id]
                local rows=result.stages[activity_id] or {}
                rows[#rows+1]=cfg
                result.stages[activity_id]=rows
            end
        end
        local random=require('ActivityPtRouletteRandomCfg')
        for _,pool_id in ipairs(random.all) do result.random[pool_id]=random[pool_id] end
        return result
    end)()''')


def _initial_activity_state(activity_id: int) -> dict:
    """按实际轮盘配置构造有效初始状态，供首次登录和无存档账号使用。"""
    stages = activity_pt_scroll_catalog()['stages'].get(str(activity_id), [])
    if not stages:
        raise ValueError('轮盘活动配置不存在')
    roulette = next((row for row in stages if row['mode'] == 2 and row.get('roulette_id')), None)
    pools = activity_pt_scroll_catalog()['random']
    pool_id = roulette['roulette_id'][0] if roulette else next(iter(pools), 0)
    pool = pools.get(str(pool_id), {})
    buff_id = next((row[1][0] for row in pool.get('random_affix_list', [])
                    if row[0] == 0 and row[1]), 0)
    if not pool_id or not buff_id:
        raise ValueError('轮盘活动随机池配置无效')
    return {'clear_stage_list': [], 'buff_id': buff_id, 'up_select': 0,
            'index': pool_id, 'clear_times': 0}


def activity_pt_scroll_state(user: dict, activity_id: int) -> dict:
    """读取或初始化单个轮盘活动状态，不向客户端暴露内部存档键。"""
    states = user.get('activity_pt_scroll')
    if not isinstance(states, dict):
        states = {}
        user['activity_pt_scroll'] = states
    state = states.get(str(activity_id))
    defaults = _initial_activity_state(activity_id)
    if state is None:
        state = defaults
        states[str(activity_id)] = state
    else:
        state = deepcopy(state)
    for key, value in defaults.items():
        state.setdefault(key, deepcopy(value))
    return state


def save_activity_pt_scroll_state(user: dict, activity_id: int, state: dict) -> None:
    """保存已经完成校验的轮盘活动状态。"""
    states = user.get('activity_pt_scroll')
    if not isinstance(states, dict):
        states = {}
        user['activity_pt_scroll'] = states
    states[str(activity_id)] = deepcopy(state)


def validate_activity_pt_scroll_stage(user: dict, common: dict) -> dict:
    """校验活动、入口和实际关卡三者匹配，并返回真实轮盘关卡配置。"""
    activity_id = common.get('activity_id', 0)
    stages = activity_pt_scroll_catalog()['stages'].get(str(activity_id), [])
    stage = next((row for row in stages
                  if row['id'] == common.get('index', 0)
                  and row['stage_id'] == common.get('dest', 0)), None)
    if stage is None:
        raise ValueError('轮盘活动关卡不匹配')
    activity_pt_scroll_state(user, activity_id)
    return stage


def activity_pt_scroll_pushes(user: dict) -> list[tuple]:
    """返回当前账号已有轮盘活动的完整页面状态。"""
    states = user.get('activity_pt_scroll', {})
    if not isinstance(states, dict):
        return []
    result = []
    for activity_id in sorted(states, key=int):
        state = activity_pt_scroll_state(user, int(activity_id))
        result.append((76151, {'activity_id': int(activity_id), **state}))
    return result


def activity_pt_scroll_push(user: dict, activity_id: int) -> tuple:
    """生成单个轮盘活动的协议推送，确保响应携带最新清关记录。"""
    state = activity_pt_scroll_state(user, activity_id)
    return 76151, {'activity_id': activity_id, **state}
