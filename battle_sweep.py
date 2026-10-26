"""常规素材与轮盘活动扫荡，复用战斗队伍校验、消耗和奖励结算。"""

from battle_data import battle_cost, start_battle, settle_battle
from activity_pt_scroll import (activity_pt_scroll_push, activity_pt_scroll_state,
                                save_activity_pt_scroll_state,
                                validate_activity_pt_scroll_stage)


def sweep_battle(user: dict, request: dict) -> tuple[dict, list[tuple]]:
    """执行允许扫荡的关卡，并返回结算数据与必须先到达的状态推送。"""
    common = request['common_info']
    stage_type = common['type']
    if stage_type not in (8, 9, 61):
        raise ValueError('该玩法不支持扫荡')
    scroll_state = None
    activity_id = common.get('activity_id', 0)
    if stage_type == 61:
        stage = validate_activity_pt_scroll_stage(user, common)
        scroll_state = activity_pt_scroll_state(user, activity_id)
    battle, player = start_battle(user, request)
    battle['reported'] = {'result': 1, 'info': {}}
    values = settle_battle(user, battle, battle['id'])
    pushes = []
    if stage_type == 61:
        times = battle['common']['battle_times']
        if battle_cost(stage)['id'] == 4:
            scroll_state['clear_times'] += times
        if common['dest'] not in scroll_state['clear_stage_list']:
            scroll_state['clear_stage_list'].append(common['dest'])
        save_activity_pt_scroll_state(user, activity_id, scroll_state)
        pushes.append(activity_pt_scroll_push(user, activity_id))
    return {**values, **player}, pushes
