from challenge_data import (boss_activity_options, boss_area, boss_data,
                            challenge_catalog, challenge_pushes)
from polyhedron_game import normalize_data, polyhedron_catalog, polyhedron_data


def _core_stage_ids() -> list[int]:
    """返回迭代校验当前配置的全部关卡 ID。"""
    return [row['id'] for row in challenge_catalog()['core']]


def _remove_progress(user: dict, kind: int, stages) -> None:
    """删除指定类型下已列出的关卡进度，不触碰其他战斗记录。"""
    progress = user.get('battle_progress')
    if not isinstance(progress, dict):
        return
    for stage in stages:
        progress.pop(f'{kind}:{stage}', None)


def _reset_core(user: dict) -> None:
    """重置迭代校验当前配置内所有关卡的通关与用时记录。"""
    _remove_progress(user, 67, _core_stage_ids())


def _switch_dream_boss(user: dict, area_id=None, group_id=None) -> str:
    """切换梦境周期与 BOSS，只清理所选 BOSS 的关卡进度。"""
    if area_id is None or group_id is None:
        raise ValueError('请选择梦境周期和 BOSS')
    try:
        area_id, group_id = int(area_id), int(group_id)
    except (TypeError, ValueError) as error:
        raise ValueError('梦境周期或 BOSS 无效') from error
    catalog = challenge_catalog()
    area = boss_area(user, area_id)
    if group_id not in area['boss_list']:
        raise ValueError('所选 BOSS 不属于该梦境周期')
    stages = catalog['boss_groups'][str(group_id)]
    _remove_progress(user, 10, stages)
    user['boss_area_id'] = area_id
    user['boss_group_id'] = group_id
    user['boss_star_rewards'] = []
    return f'梦境再构（周期 {area_id} / BOSS {group_id}）'


def _reset_polyhedron(user: dict) -> None:
    """重置多维变量当前路线，保留局外成长、解锁与领取记录。"""
    data = polyhedron_data(user)
    data['game']['state'] = 1
    data['game']['progress'] = {
        'tier_id': polyhedron_catalog()['tiers'][0]['id'], 'event': {},
        'stage': {'reward': {}}, 'cooperate_unique_skill_id': 0
    }
    data['manual'] = {}
    user['polyhedron'] = normalize_data(data)
    user.pop('polyhedron_completed_nodes', None)
    user.pop('polyhedron_run_settled', None)
    user.pop('polyhedron_settlement', None)


def reset_activity(user: dict, activity: str, area_id=None, group_id=None) -> tuple[str, list[tuple]]:
    """按白名单重置周期活动，并返回活动名与需要下发的页面推送。"""
    if activity == 'core':
        _remove_progress(user, 67, (row['stage_id'] for row in challenge_catalog()['core']))
        _reset_core(user)
        return '迭代校验', activity_pushes(user, activity)
    if activity == 'dream':
        name = _switch_dream_boss(user, area_id, group_id)
        return name, activity_pushes(user, activity)
    if activity == 'polyhedron':
        _reset_polyhedron(user)
        return '多维变量', [(18001, polyhedron_data(user))]
    raise ValueError('活动类型无效')


def activity_pushes(user: dict, activity: str) -> list[tuple]:
    """生成指定周期活动的页面推送，供重置与在线同步复用。"""
    if activity == 'dream':
        return boss_data(user)
    if activity == 'polyhedron':
        return [(18001, polyhedron_data(user))]
    if activity == 'neural':
        from neural_matrix import neural_data, neural_state
        return [(89801, neural_data(user)), (89811, {'score': neural_state(user)['last_score']})]
    if activity == 'core':
        return [(command, payload) for command, payload in challenge_pushes(user) if command == 75009]
    raise ValueError('活动类型无效')


def activity_options(user: dict) -> dict:
    """返回周期活动可选周期和 BOSS，供 GM 页面构造精确切换请求。"""
    return {'dream': boss_activity_options(user)}
