"""刺桐箱庭主支线任务、脚本参数与战斗条件的持久化。"""

from copy import deepcopy
from functools import lru_cache

from battle_rewards import grant_rewards, drop_config, roll_drops
from resident_catalog import resident_rows, resident_config, append_unique

WORLD_COMMANDS = {28820, 28822, 28830, 28832}
WORLD_ACTIVITY = 323641


@lru_cache(maxsize=1)
def world_catalog() -> tuple[dict, dict]:
    """只选取当前常驻地图所属任务，不把客户端测试任务下发给玩家。"""
    mains = {c['main_task_id']: c for c in resident_rows('SandplayTaskMainCfg')
             if c['activity_id'] == WORLD_ACTIVITY}
    tasks = {c['task_id']: c for c in resident_rows('SandplayTaskCfg') if c['main_task_id'] in mains}
    return mains, tasks


def world_state(user: dict) -> dict:
    """建立箱庭独立任务记录；任务前置由实际完成记录决定。"""
    state = user.setdefault('resident_world', {})
    state.setdefault('quests', {})
    state.setdefault('parameters', {})
    state.setdefault('sea_stages', [])
    return state


def world_condition(state: dict, condition_id: int) -> bool:
    """读取箱庭配置中的主任务完成条件，不以入口解锁代替任务完成。"""
    cfg = resident_config('ConditionCfg', condition_id)
    return cfg['type'] == 100 and state['quests'].get(str(cfg['params'][0]), {}).get('status') == 9


def world_refresh(user: dict) -> dict:
    """根据主支线前置开放任务和子任务，保留已完成与领奖事实。"""
    state = world_state(user)
    mains, tasks = world_catalog()
    for main_id, cfg in mains.items():
        conditions = [world_condition(state, i) for i in cfg['unlock_param']]
        if conditions and not (all(conditions) if cfg['unlock_operation'] == 2 else any(conditions)):
            continue
        main = state['quests'].setdefault(str(main_id), {'id': main_id,
            'status': 2 if cfg['receive_npc'] else 3, 'task_list': [], 'expired_timestamp': 0})
        if main['status'] != 3:
            continue
        finished = {r['id'] for r in main['task_list'] if r['status'] == 1}
        for task_id, task in tasks.items():
            if task['main_task_id'] != main_id:
                continue
            previous = task['pre_task_id']
            unlocked = not previous or (all(i in finished for i in previous) if task['pre_operation'] == 2
                                         else any(i in finished for i in previous))
            if unlocked and not any(r['id'] == task_id for r in main['task_list']):
                main['task_list'].append({'id': task_id, 'progress': 0, 'status': 0})
    return state


def world_data(user: dict) -> dict:
    """首次和重新登录用 28801 恢复任务；操作使用 28803 增量更新。"""
    state = world_refresh(user)
    mains, _ = world_catalog()
    return {'quest_list': deepcopy([state['quests'][str(i)] for i in mains if str(i) in state['quests']])}


def world_task(user: dict, task_id: int) -> tuple[dict, dict]:
    """校验子任务属于当前活动且已开始，不允许跳过前置。"""
    _, tasks = world_catalog()
    cfg = tasks.get(task_id)
    if cfg is None:
        raise ValueError('箱庭子任务不存在')
    main = world_refresh(user)['quests'].get(str(cfg['main_task_id']))
    row = next((r for r in main['task_list'] if r['id'] == task_id), None) if main else None
    if main is None or main['status'] != 3 or row is None:
        raise ValueError('箱庭子任务尚未开放')
    return cfg, row


def world_sea_data(user: dict) -> dict:
    """船战关卡完成与船员技能根据已提交的箱庭子任务恢复。"""
    state = world_refresh(user)
    done = {r['id'] for q in state['quests'].values() for r in q['task_list'] if r['status'] == 1}
    return {'data': {'stage_id_list': list(state['sea_stages']),
        'affix': [c['id'] for c in resident_rows('SeaWarfareSkillCfg') if not c['task_id'] or c['task_id'] in done]}}


def world_battle_complete(user: dict, kind: int, stage: int) -> list:
    """实际船战或箱庭战斗获胜后更新对应条件，失败不增加进度。"""
    condition = {84: 330001, 85: 330002}.get(kind)
    if condition is None:
        return []
    _, tasks = world_catalog()
    state = world_refresh(user)
    rewards = []
    if kind == 84:
        for cfg in resident_rows('SeaWarfareStageCfg'):
            if cfg['stage_id'] == stage and append_unique(state['sea_stages'], cfg['id']):
                rewards.extend(sorted(roll_drops(drop_config(cfg['reward']), 1), key=lambda r: r['id']))
    for main in state['quests'].values():
        if main['status'] != 3:
            continue
        for row in main['task_list']:
            cfg = tasks[row['id']]
            if row['status'] == 0 and cfg['condition'] == condition and stage in cfg['additional_parameter']:
                row['progress'] = min(cfg['need'], row['progress'] + 1)
    return rewards


def world_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """实现箱庭参数同步、子任务提交、主任务领奖及接取的原子操作。"""
    state = world_refresh(user)
    mains, _ = world_catalog()
    response = {'result': 0}
    if command == 28832:
        task_id, value = request['param'], request['value']
        if task_id == 0:
            if value != 1:
                raise ValueError('箱庭刷新参数无效')
        else:
            cfg, row = world_task(user, task_id)
            if cfg['condition'] != 60 or value > cfg['need']:
                raise ValueError('箱庭任务不能用脚本参数完成或进度溢出')
            row['progress'] = max(row['progress'], value)
            state['parameters'][str(task_id)] = row['progress']
    elif command == 28822:
        ids = request['quest_task_id']
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('箱庭子任务列表为空或重复')
        rows = []
        for task_id in ids:
            cfg, row = world_task(user, task_id)
            if row['progress'] < cfg['need']:
                raise ValueError('箱庭子任务尚未完成')
            rows.append(row)
        for row in rows:
            row['status'] = 1
        response['reward_list'] = []
    elif command == 28820:
        ids = request['quest_id_list']
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('箱庭主任务列表为空或重复')
        rewards = []
        for main_id in ids:
            cfg, main = mains.get(main_id), state['quests'].get(str(main_id))
            if cfg is None or main is None:
                raise ValueError('箱庭主任务尚未开放')
            if main['status'] == 9:
                continue
            end = next((r for r in main['task_list'] if r['id'] == cfg['end_task_id']), {})
            if main['status'] != 3 or end.get('status') != 1:
                raise ValueError('箱庭主任务尚未完成')
            rewards.extend({'id': item, 'num': amount} for item, amount in cfg['reward'])
        for main_id in ids:
            state['quests'][str(main_id)]['status'] = 9
        grant_rewards(user, rewards)
        response['reward_list'] = rewards
    elif command == 28830:
        main = state['quests'].get(str(request['quest_id']))
        if main is None or main['status'] not in (2, 3):
            raise ValueError('箱庭主任务不能接取')
        main['status'] = 3
    else:
        raise ValueError('未知箱庭任务请求')
    return response, [(28803, world_data(user)), (86035, world_sea_data(user))]
