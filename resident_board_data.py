"""夏日棋盘账号、网格、角色属性和协议快照。"""

from copy import deepcopy
from functools import lru_cache

from gameplay_protocol import read_client_data
from resident_board_map import board_map
from resident_catalog import resident_config

DIRECTIONS = ((1, 1), (0, 1), (-1, 0), (-1, -1), (0, -1), (1, 0))


@lru_cache(maxsize=1)
def board_attributes() -> list:
    """属性表以名称作为主键，不能用空的 all 列表漏掉默认值。"""
    return read_client_data("(function()local r=setmetatable({},{json_array=true});"
        "for _,v in pairs(require('ChessBoardAttributeCfg'))do if type(v)=='table'and v.id then r[#r+1]=v end end;"
        "table.sort(r,function(a,b)return a.id<b.id end);return r end)()")


def board_state(user: dict) -> dict:
    """单账号保存当前棋盘、每张地图和真实通关，离开场景不丢失进行中的对局。"""
    state = user.setdefault('resident_board', {})
    state.setdefault('chapter_id', 0)
    state.setdefault('runs', {})
    state.setdefault('cleared', [])
    state.setdefault('run_counter', 0)
    return state


def board_run(user: dict) -> dict:
    """只允许操作当前已进入的棋盘，拒绝未开局的掷骰或道具操作。"""
    state = board_state(user)
    run = state['runs'].get(str(state['chapter_id']))
    if not run:
        raise ValueError('请先进入棋盘')
    return run


def board_entity(run: dict, entity_id: int = 0) -> dict:
    """按账号内实体编号查找玩家或 NPC。"""
    row = next((r for r in run['map']['hero'] if r['id'] == entity_id), None)
    if row is None:
        raise ValueError('棋盘角色不存在')
    return row


def board_attr(entity: dict, attribute: int) -> int:
    """读取实体的实际属性，NPC 缺省属性同样使用客户端默认值。"""
    row = next((r for r in entity['attribute'] if r['id'] == attribute), None)
    return row['value'] if row else next(c['default'] for c in board_attributes() if c['id'] == attribute)


def board_set_attr(entity: dict, attribute: int, value: int) -> None:
    """匹配客户端 SubAttribute 行为，属性下限为零，保留临时增益。"""
    row = next((r for r in entity['attribute'] if r['id'] == attribute), None)
    if row is None:
        row = {'id': attribute, 'value': 0}
        entity['attribute'].append(row)
    row['value'] = max(0, value)


def board_random(run: dict, limit: int) -> int:
    """对局内随机状态随存档保存，重连不会重新生成商店、机会或骰子。"""
    if limit <= 0:
        raise ValueError('棋盘随机配置为空')
    run['rng'] = (1664525 * run['rng'] + 1013904223) & 0xffffffff
    return run['rng'] % limit


def empty_chance() -> dict:
    """返回原始协议要求的完整空机会结构。"""
    return {'chance_id': 0, 'is_unlock': 0, 'item_list': [], 'choose_list': []}


def board_new_entity(entity_id: int, kind: int, x: int, z: int, direction: int) -> dict:
    """创建完整玩家或 NPC 描述，所有 required 子消息均初始化。"""
    attributes = [{'id': c['id'], 'value': c['default']} for c in board_attributes()]
    if entity_id:
        cfg = resident_config('ChessBoardNPCPoolCfg', kind)
        attributes = [{'id': i, 'value': v} for i, v in cfg['init_attribute_list']]
    return {'id': entity_id, 'type': kind, 'pos': {'x': x, 'z': z}, 'direction': direction,
            'attribute': attributes, 'item': [], 'tmp_item': [], 'buff': [], 'use_item': 0, 'chance': empty_chance()}


def board_new_run(user: dict, chapter: int) -> dict:
    """按真实出生点建立空棋盘，首次事件随后执行，不提前完成任何任务。"""
    cfg = resident_config('ChessBoardLevelCfg', chapter, 'id_level')
    board_map(chapter)
    state = board_state(user)
    state['run_counter'] += 1
    return {'chapter_id': chapter, 'rng': (chapter + user.get('uid', 0) + state['run_counter']) & 0xffffffff,
        'next_entity_id': 1, 'finished_tasks': [], 'finished_chances': [], 'dice': [], 'next_npc_phase': 1,
        'map': {'turn': 1, 'hero': [board_new_entity(0, 0, *cfg['init_pos'], cfg['init_dir'])],
                'floor': [], 'object': [], 'ban_dir': [], 'task': []}}


def board_grid(run: dict, x: int, z: int) -> dict:
    """将存档中的扩展地块和物件覆盖到实际地图，保持原坐标。"""
    data = board_map(run['chapter_id'])
    source = next((r for r in data['mapInfo'] if r['x'] == x and r['z'] == z), None)
    if source is None:
        raise ValueError('棋盘坐标超出地图')
    grid = deepcopy(source)
    for field, extension in (('floor', 'extendInfo'), ('object', 'extendThingInfo')):
        row = next((r for r in run['map'][field] if r['pos'] == {'x': x, 'z': z}), None)
        if row is None:
            continue
        if field == 'object' and row['tag'] < 0:
            grid.update(thingTypeID=0, thingEnterParam=0, thingExitParam=0, thingStartMoveParam=0, thingEndMoveParam=0)
            continue
        if field == 'object' and row['tag'] == 0:
            continue
        entries = data[extension] or []
        if not 1 <= row['tag'] <= len(entries):
            raise ValueError('棋盘扩展格子配置不存在')
        child = entries[row['tag']-1]
        if field == 'floor':
            grid.update(child)
        else:
            grid.update({k: v for k, v in child.items() if k.startswith('thing')})
    grid.update(x=x, z=z)
    return grid


def board_change_grid(run: dict, x: int, z: int, tag: int, thing: bool = False) -> None:
    """保存扩展格子索引，索引不是 HexCfg 的类型编号。"""
    data = board_map(run['chapter_id'])
    entries = data['extendThingInfo' if thing else 'extendInfo'] or []
    if not 1 <= tag <= len(entries) and not (thing and tag in (-1, 0)):
        raise ValueError('棋盘扩展编号无效')
    board_grid(run, x, z)
    rows = run['map']['object' if thing else 'floor']
    row = next((r for r in rows if r['pos'] == {'x': x, 'z': z}), None)
    if row is None:
        row = {'pos': {'x': x, 'z': z}, 'tag': 0, 'rotation': 0}
        rows.append(row)
    row['tag'] = tag
    if not thing:
        row['state'] = entries[tag-1]['status']
        if not resident_config('ChessBoardHexCfg', entries[tag-1]['typeID'])['walkable']:
            hero = board_entity(run)
            if hero['pos'] == {'x': x, 'z': z}:
                cfg = resident_config('ChessBoardLevelCfg', run['chapter_id'], 'id_level')
                hero.update(pos={'x': cfg['init_pos'][0], 'z': cfg['init_pos'][1]}, direction=cfg['init_dir'])
    elif tag > 0 and entries[tag-1]['thingTypeID'] and resident_config('ChessBoardThingCfg', entries[tag-1]['thingTypeID'])['fix_walkable'] == 1:
        hero = board_entity(run)
        if hero['pos'] == {'x': x, 'z': z}:
            cfg = resident_config('ChessBoardLevelCfg', run['chapter_id'], 'id_level')
            hero.update(pos={'x': cfg['init_pos'][0], 'z': cfg['init_pos'][1]}, direction=cfg['init_dir'])


def board_neighbors(run: dict, pos: dict) -> list[tuple]:
    """只返回实际可行走且未封闭的六向相邻格子。"""
    result = []
    current = board_grid(run, pos['x'], pos['z'])
    for direction, (dx, dz) in enumerate(DIRECTIONS):
        x, z = pos['x']+dx, pos['z']+dz
        data = board_map(run['chapter_id'])
        if not 0 <= x < data['width'] or not 0 <= z < data['height']:
            continue
        grid = board_grid(run, x, z)
        if not resident_config('ChessBoardHexCfg', grid['typeID'])['walkable'] or current['banDir'][direction]:
            continue
        if grid['thingTypeID'] and resident_config('ChessBoardThingCfg', grid['thingTypeID'])['fix_walkable'] == 1:
            continue
        result.append((x, z, direction))
    return result
