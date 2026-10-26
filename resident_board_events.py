"""按客户端棋盘事件配置执行状态变化，并生成相同的事件队列。"""

from copy import deepcopy

from resident_board_data import (board_attr, board_set_attr, board_entity, board_random, board_new_entity,
    board_grid, board_change_grid, empty_chance)
from resident_catalog import resident_config


def board_event_value(value) -> dict:
    """编码整数、数组和二维数组，匹配客户端事件参数解包格式。"""
    if isinstance(value, int):
        return {'type': 1, 'integer': value}
    if value and isinstance(value[0], (list, tuple)):
        return {'type': 3, 'array2': [{'array': list(row)} for row in value]}
    return {'type': 2, 'array': list(value)}


def board_spawn(run: dict, kind: int, x: int, z: int, direction: int) -> int:
    """创建配置中的 NPC；棋盘允许的 NPC 数量达到上限时返回零。"""
    board_grid(run, x, z)
    if len(run['map']['hero']) >= 4:
        return 0
    entity_id = run['next_entity_id']
    run['next_entity_id'] += 1
    run['map']['hero'].append(board_new_entity(entity_id, kind, x, z, direction))
    return entity_id


def board_remove_buff(run: dict, entity_id: int, buff_id: int, pushes: list) -> None:
    """移除增益前下发销毁事件，保留客户端需要的结束效果。"""
    entity = board_entity(run, entity_id)
    row = next((r for r in entity['buff'] if r['id'] == buff_id), None)
    if row is None:
        return
    entity['buff'].remove(row)
    cfg = resident_config('ChessBoardBuffPoolCfg', buff_id)
    board_pool(run, cfg['destory_event'], 15, [buff_id], pushes, entity_id)


def board_add_buff(run: dict, entity_id: int, buff_id: int, pushes: list) -> None:
    """按客户端互斥和免疫条件加入增益，初始化次数及持续回合。"""
    entity = board_entity(run, entity_id)
    cfg = resident_config('ChessBoardBuffPoolCfg', buff_id)
    if cfg['buff_type'] == 2 and board_attr(entity, 9) >= 1:
        return
    if cfg['clash'] == 1:
        for old in list(entity['buff']):
            if resident_config('ChessBoardBuffPoolCfg', old['id'])['clash'] == 1:
                board_remove_buff(run, entity_id, old['id'], pushes)
    entity['buff'] = [r for r in entity['buff'] if r['id'] != buff_id]
    entity['buff'].append({'id': buff_id, 'duration': 0,
        'buff_effect': [{'id': i, 'num': 0} for i in cfg['buff_efffect']]})
    board_pool(run, cfg['add_event'], 16, [buff_id], pushes, entity_id)


def board_open_chance(run: dict, chance_id: int, pushes: list) -> None:
    """只在触发真实格子机会时生成选项，重连读取同一批商品与奖励。"""
    hero = board_entity(run)
    if hero['chance']['chance_id']:
        return
    cfg = resident_config('ChessBoardChanceCfg', chance_id)
    chance = empty_chance()
    chance.update(chance_id=chance_id, is_unlock=int(cfg['cost'] == 0))
    if cfg['type'] in (1, 2):
        count, pool = cfg['params']
        chance['item_list'] = [pool[board_random(run, len(pool))] for _ in range(count)]
    elif cfg['type'] == 5:
        index = board_random(run, len(cfg['params']))
        option = cfg['params'][index]
        chance['item_list'] = [index+1, option[0]]
        if option[0] == 2:
            chance['item_list'].extend(option[2][board_random(run, len(option[2]))] for _ in range(option[1]))
        elif option[0] in (1, 4, 5):
            chance['item_list'].append(option[1])
    elif cfg['type'] == 9 and cfg['params'][0] == 2:
        count = max(row[1] for row in cfg['params'][1])
        pool = cfg['params'][2]
        chance['item_list'] = [pool[board_random(run, len(pool))] for _ in range(count)]
    elif cfg['type'] == 14:
        chance['choose_list'] = [board_attr(hero, 13), 0, 0]
    hero['chance'] = chance
    pushes.append((85033, {'chance': deepcopy(chance)}))


def board_apply_event(run: dict, event: list, pushes: list, a: int, b: int, grid: dict) -> list:
    """执行客户端定义的状态事件，动画、镜头和剧情仅通过原事件通知渲染。"""
    kind, args = event[0], event[1:]
    computed = []
    if kind in (10001, 10009, 10010, 10014, 10015, 10018, 10021):
        return computed
    if kind == 10002:
        computed = [board_spawn(run, *args)]
    elif kind in (10003, 10004):
        board_change_grid(run, *args, thing=kind == 10004)
    elif kind in (10005, 10006):
        board_change_grid(run, grid['x'], grid['z'], args[0], thing=kind == 10006)
    elif kind in (10007, 10017):
        x, z = (grid['x'], grid['z']) if kind == 10007 else args
        run['map']['hero'] = [r for r in run['map']['hero'] if not r['id'] or r['pos'] != {'x': x, 'z': z}]
    elif kind in (10019, 10020):
        x, z = args if kind == 10019 else (grid['x'], grid['z'])
        board_change_grid(run, x, z, -1, thing=True)
    elif kind in (10008, 10016):
        if kind == 10008:
            left, right = board_entity(run, a), board_entity(run, b)
            x, z, new_type, direction = grid['x'], grid['z'], args[0], left['direction']
        else:
            x, z, new_type, direction = args
            occupants = [r for r in run['map']['hero'] if r['id'] and r['pos'] == {'x': x, 'z': z}]
            if len(occupants) != 2:
                return [0]
            left, right = occupants
        if left['type'] == new_type:
            new_id = left['id']
            run['map']['hero'].remove(right)
        elif right['type'] == new_type:
            new_id = right['id']
            run['map']['hero'].remove(left)
        else:
            run['map']['hero'].remove(left)
            run['map']['hero'].remove(right)
            new_id = board_spawn(run, new_type, x, z, direction)
        computed = [new_id, direction] if kind == 10008 else [new_id]
    elif kind == 10012:
        resident_config('ChessBoardTaskPoolCfg', args[0])
        if not any(r['id'] == args[0] for r in run['map']['task']):
            run['map']['task'].append({'id': args[0], 'progress': 0})
    elif kind == 10013:
        for row in run['map']['task']:
            cfg = resident_config('ChessBoardTaskPoolCfg', row['id'])
            if cfg['condition_type'] == args[0]:
                row['progress'] = min(cfg['need'], row['progress'] + args[1])
    elif 20001 <= kind <= 20008:
        entity_id = a if args[0] == 1 else b
        entity = board_entity(run, entity_id)
        if kind == 20001:
            _, x, z, direction = args
            board_grid(run, x, z)
            entity.update(pos={'x': x, 'z': z}, direction=direction)
        elif kind == 20002:
            board_set_attr(entity, args[1], board_attr(entity, args[1]) + args[2])
        elif kind == 20003:
            entity['item'].append(args[1])
        elif kind == 20004:
            for row in list(entity['buff']):
                if resident_config('ChessBoardBuffPoolCfg', row['id'])['buff_type'] == 2:
                    board_remove_buff(run, entity_id, row['id'], pushes)
        elif kind == 20005:
            board_add_buff(run, entity_id, args[1], pushes)
        elif kind == 20006:
            board_remove_buff(run, entity_id, args[1], pushes)
        else:
            computed = [args[-1]]
            board_set_attr(entity, args[1], args[-1])
    else:
        raise ValueError(f'棋盘事件尚未实现：{kind}')
    return computed


def board_pool(run: dict, pool_id: int, timing: int, timing_params: list, pushes: list,
               a: int = 0, b: int = -1, grid: dict | None = None) -> None:
    """展开配置池并发送 85031；机会池通过 85033 下发当前选项。"""
    if not pool_id:
        return
    cfg = resident_config('ChessBoardEventPoolCfg', pool_id)
    events = cfg['player_params' if a == 0 else 'npc_params']
    if not events:
        return
    if cfg['type'] == 2:
        board_open_chance(run, events[0], pushes)
        return
    if grid is None:
        entity = board_entity(run, a)
        grid = board_grid(run, entity['pos']['x'], entity['pos']['z'])
    event_rows = []
    for source in events:
        computed = board_apply_event(run, source, pushes, a, b, grid)
        event_rows.append({'event': [board_event_value(v) for v in source], 'params': computed})
    pushes.append((85031, {'event': {'trigger_type': timing, 'trigger_type_param': list(timing_params),
        'event_pool_id': pool_id, 'trigger_player_a': a, 'trigger_player_b': b, 'event_list': event_rows}}))


def board_buff_effects(run: dict, entity_id: int, trigger: int, pushes: list, other: int = -1) -> None:
    """保存每项增益效果的实际触发次数，再生成与客户端匹配的时机通知。"""
    entity = board_entity(run, entity_id)
    for buff in list(entity['buff']):
        for effect in buff['buff_effect']:
            cfg = resident_config('ChessBoardBuffEffectCfg', effect['id'])
            if cfg['trigger_time'] != trigger or cfg['trigger_count'] >= 0 and effect['num'] >= cfg['trigger_count']:
                continue
            effect['num'] += 1
            timing = {1: 21, 2: 22, 3: 23, 4: 24, 5: 14}[trigger]
            a, b = (other, entity_id) if trigger in (1, 2) else (entity_id, other)
            board_pool(run, cfg['event'], timing, [effect['id']], pushes, a, b)


def board_tick_buffs(run: dict, entity_id: int, pushes: list) -> None:
    """结束回合时递增持续时间，按真实周期触发及到期移除。"""
    entity = board_entity(run, entity_id)
    for buff in list(entity['buff']):
        buff['duration'] += 1
        cfg = resident_config('ChessBoardBuffPoolCfg', buff['id'])
        if cfg['updateround'] > 0 and buff['duration'] % cfg['updateround'] == 0:
            board_pool(run, cfg['turn_event'], 17, [buff['id']], pushes, entity_id)
        if cfg['remainround'] > 0 and buff['duration'] >= cfg['remainround']:
            board_remove_buff(run, entity_id, buff['id'], pushes)
