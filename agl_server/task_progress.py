"""任务进度、周期活跃度与奖励领取的持久化。"""

import time

from .assignment_facts import (DAILY_TASK_DONE, TASK_REWARD_POINT,
                              bump, facts_value, reconcile_tasks)
from .battle_rewards import grant_rewards
from .gameplay_protocol import read_client_data
from .server_clock import business_day


def refresh_task_periods(user: dict, now: int | None = None) -> None:
    """以北京时间重置每日和每周任务，保留剧情与长期任务。"""
    from .gameplay_data import client_catalog
    day = business_day(int(time.time()) if now is None else now)
    periods = {6: day, 5: (day-4) // 7}
    saved = user.setdefault('task_periods', {})
    tasks = user.setdefault('tasks', {})
    for kind, current in periods.items():
        key = str(kind)
        if key in saved and saved[key] != current:
            for cfg in client_catalog()['tasks']:
                if cfg['type'] == kind:
                    tasks.pop(str(cfg['id']), None)
            user.setdefault('task_points', {}).pop('1' if kind == 6 else '3', None)
            if kind == 5:
                user['passport_weekly_exp'] = 0
        saved[key] = current


def advance_periodic_progress(user: dict, condition: int, amount: int, parameter: int = 0) -> None:
    """按本次事件推进每日/每周任务的累加进度。

    每日、每周任务在周期切换时会删除任务条目，不能像长期任务那样读跨天累计的
    计数器重算，只能按每次真实事件累加；消耗类事件在 ``battle_rewards.spend_items``
    写计数器后调用本函数，避免每日任务进度停在旧值。
    """
    from .gameplay_data import client_catalog
    for cfg in client_catalog()['tasks']:
        if cfg['type'] not in (5, 6) or int(cfg['condition']) != int(condition):
            continue
        params = cfg.get('additional_parameter') or []
        if params and params[0] not in (0, parameter):
            continue
        entry = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        if not entry.get('complete_flag'):
            entry['progress'] = min(int(cfg['need']), int(entry.get('progress', 0)) + int(amount))


def update_task_progress(user: dict, condition: int, amount: int = 1, parameter: int = 0) -> None:
    """由已验证的登录、通关、消耗和交互事件更新当前可见任务。

    累加类条件先把次数写入独立计数器，再由事实重算决定任务进度；这样任务
    在后续阶段才出现时也能继承此前的累计值，不再受“当时是否在任务列表里”限制。
    """
    from .gameplay_data import client_catalog, task_data
    bump(user, condition, parameter, amount)
    active = {row['id'] for row in task_data(user)['assignment_list']}
    for cfg in client_catalog()['tasks']:
        if cfg['id'] not in active or cfg['condition'] != condition:
            continue
        params = cfg.get('additional_parameter') or []
        if params and params[0] not in (0, parameter):
            continue
        if cfg['type'] not in (5, 6) and facts_value(user, cfg) is not None:
            # 可由存档事实算出的条件交给 reconcile_tasks，避免事件与事实重复累加。
            # 每日/每周任务按周期删除条目，不能读跨天累计的计数器，只能累加本次事件。
            continue
        entry = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        if entry['complete_flag'] == 0:
            entry['progress'] = min(cfg['need'], entry['progress'] + amount)
    reconcile_tasks(user)


def claim_available_task_points(user: dict, point_id: int) -> list[dict]:
    """领取指定活跃度类型中已达成且未领取的全部奖励。"""
    cfg = read_client_data(f"require('ActivityPtCfg')[{int(point_id)}] or {{}}")
    point = user.setdefault('task_points', {}).setdefault(str(point_id), {'point': 0, 'claimed': []})
    rewards = []
    for target, reward_cfg in zip(cfg.get('target', []), cfg.get('reward', [])):
        if target in point['claimed'] or point['point'] < target:
            continue
        current = [{'id': item, 'num': amount} for item, amount in reward_cfg]
        grant_rewards(user, current)
        point['claimed'].append(target)
        rewards.extend(current)
    return rewards


def claim_tasks(user: dict, ids: list[int], task_type: int = 0) -> dict:
    """整批校验任务；每日、每周空列表分别领取活跃度 1、3 的达成奖励。"""
    from .gameplay_data import client_catalog, task_data
    # 领取前按存档事实补齐进度，避免任务已达成但事件钩子缺失时被判为未完成。
    reconcile_tasks(user)
    active = {row['id'] for row in task_data(user)['assignment_list']}
    catalog = {cfg['id']: cfg for cfg in client_catalog()['tasks']}
    if (not ids and task_type not in (6, 5)) or len(set(ids)) != len(ids):
        raise ValueError('任务列表为空或重复')
    rewards = []
    for task_id in ids:
        cfg = catalog.get(task_id)
        progress = user.get('tasks', {}).get(str(task_id), {})
        if task_id not in active or not cfg or progress.get('complete_flag') or progress.get('progress', 0) < cfg['need']:
            raise ValueError('任务尚未完成或奖励已领取')
        rewards.extend({'id': item, 'num': amount} for item, amount in cfg['reward'])
    for task_id in ids:
        user['tasks'][str(task_id)]['complete_flag'] = 1
    grant_rewards(user, rewards)
    update_task_progress(user, TASK_REWARD_POINT, sum(r['num'] for r in rewards if r['id'] == 22), 22)
    # “完成N个每日任务”按实际领取的每日任务个数累计。
    daily = [task_id for task_id in ids if (catalog.get(task_id) or {}).get('type') == 6]
    if daily:
        bump(user, DAILY_TASK_DONE, 6, len(daily))
    point_id = {6: 1, 5: 3}.get(task_type)
    if point_id:
        rewards.extend(claim_available_task_points(user, point_id))
    return {'result': 0, 'reward_list': rewards}


def claim_task_points(user: dict, request: dict) -> dict:
    """领取日、周或新手活跃度节点，防止重复领取。"""
    point_id = request.get('activity_pt_id') or 1
    cfg = read_client_data(f"require('ActivityPtCfg')[{int(point_id)}] or {{}}")
    target = request['need_active_point']
    point = user.setdefault('task_points', {}).setdefault(str(point_id), {'point': 0, 'claimed': []})
    if not cfg or target not in cfg['target'] or point['point'] < target or target in point['claimed']:
        raise ValueError('活跃度未达到或奖励已领取')
    rewards = [{'id': item, 'num': amount} for item, amount in cfg['reward'][cfg['target'].index(target)]]
    grant_rewards(user, rewards)
    point['claimed'].append(target)
    return {'result': 0, 'reward_list': rewards}
