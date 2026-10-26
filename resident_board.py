"""夏日棋盘进入、移动、骰子、机会、道具与真实任务结算。"""

from copy import deepcopy

from resident_catalog import resident_rows, resident_config, append_unique
from resident_board_data import (board_state, board_run, board_new_run, board_entity, board_attr,
    board_set_attr, board_random, board_grid, board_neighbors, board_change_grid, empty_chance)
from resident_board_events import board_pool, board_add_buff, board_remove_buff, board_tick_buffs, board_buff_effects

BOARD_COMMANDS = {85000, 85010, 85012, 85014, 85016, 85018, 85020, 85022, 85024, 85026, 85028, 85036, 85038}


def board_battle_complete(user: dict, kind: int, stage: int) -> None:
    """实际胜利记录仅关联当前格子开启的同一关卡，不能借用其他战斗的结果。"""
    if not board_state(user)['chapter_id']:
        return
    run = board_run(user)
    chance = board_entity(run)['chance']
    if not chance['chance_id']:
        return
    cfg = resident_config('ChessBoardChanceCfg', chance['chance_id'])
    actual_stage = resident_config('ActivityWhackMoleCfg', cfg['params'][0])['stage_id'] if cfg['type'] == 3 else cfg['params'][0] if cfg['params'] else 0
    if {3: 71, 8: 74, 10: 75, 11: 72, 13: 73}.get(cfg['type']) == kind and actual_stage == stage and chance['is_unlock']:
        run.setdefault('battle_wins', {})[str(cfg['id'])] = True


def board_history(user: dict) -> dict:
    """通关条件用于菜单下一章节解锁，未通关不能下发历史成功。"""
    cleared = board_state(user)['cleared']
    return {'data_list': [{'id': c['id'], 'data': int(c['params'][0] in cleared)}
        for c in resident_rows('ConditionCfg') if c['type'] == 11501]}


def board_spend_gold(hero: dict, amount: int) -> None:
    """棋盘金币独立于账号钱包，事件与购买使用配置金额。"""
    if amount < 0 or board_attr(hero, 4) < amount:
        raise ValueError('鸭鸭币不足')
    board_set_attr(hero, 4, board_attr(hero, 4)-amount)


def board_roll(run: dict, hero: dict) -> list:
    """固定骰子、骰子数量与范围都由保存的属性决定。"""
    count, limit, selected = board_attr(hero, 2), board_attr(hero, 1), board_attr(hero, 3)
    if not 1 <= count <= 2 or not 1 <= limit <= 6:
        raise ValueError('骰子属性无效')
    return [min(selected, limit) if selected else board_random(run, limit)+1 for _ in range(count)]


def board_step(run: dict, entity_id: int, x: int, z: int, direction: int, pushes: list, remaining: int) -> None:
    """按照客户端时机顺序处理每个移动格子，事件与位置在同一事务中保存。"""
    entity = board_entity(run, entity_id)
    old = board_grid(run, entity['pos']['x'], entity['pos']['z'])
    new = board_grid(run, x, z)
    if resident_config('ChessBoardHexCfg', new['typeID'])['updatable'] == 1:
        entity.update(pos={'x': x, 'z': z}, direction=direction)
        board_set_attr(entity, 8, 2)
        return
    for field, timing in (('thingStartMoveParam', 26), ('startMoveParam', 4)):
        if board_attr(entity, 8) == 1:
            board_pool(run, old[field], timing, [old['x'], old['z']], pushes, entity_id, grid=old)
    for field, timing in (('thingExitParam', 25), ('exitParam', 3)):
        board_pool(run, old[field], timing, [old['x'], old['z']], pushes, entity_id, grid=old)
    entity.update(pos={'x': x, 'z': z}, direction=direction)
    board_set_attr(entity, 5, remaining)
    board_set_attr(entity, 8, 2 if remaining else 3)
    for other in list(run['map']['hero']):
        if other['id'] == entity_id or other['pos'] != entity['pos']:
            continue
        if entity_id == 0:
            cfg = resident_config('ChessBoardNPCPoolCfg', other['type'])
            board_buff_effects(run, 0, 3 if not remaining else 4, pushes, other['id'])
            if other in run['map']['hero']:
                board_pool(run, cfg['player_stay_npc_event' if not remaining else 'player_cross_npc_event'],
                    10 if not remaining else 6, [other['id']], pushes, 0, other['id'], new)
        elif other['id'] == 0:
            cfg = resident_config('ChessBoardNPCPoolCfg', entity['type'])
            board_buff_effects(run, 0, 1 if not remaining else 2, pushes, entity_id)
            if entity not in run['map']['hero']:
                return
            board_pool(run, cfg['npc_stay_player_event' if not remaining else 'npc_cross_player_event'],
                9 if not remaining else 7, [entity_id], pushes, entity_id, 0, new)
        elif not remaining:
            cfg = resident_config('ChessBoardNPCPoolCfg', entity['type'])
            board_pool(run, cfg['npc_stay_npc_event'], 8, [entity_id], pushes, entity_id, other['id'], new)
        if entity not in run['map']['hero']:
            return
    for field, timing in (('thingEnterParam', 19), ('enterParam', 2)):
        board_pool(run, new[field], timing, [x, z], pushes, entity_id, grid=new)
        if entity not in run['map']['hero']:
            return
    if board_attr(entity, 5) <= 0:
        board_set_attr(entity, 8, 3)
        for field, timing in (('thingEndMoveParam', 27), ('endMoveParam', 5)):
            board_pool(run, new[field], timing, [x, z], pushes, entity_id, grid=new)
            if entity not in run['map']['hero']:
                return
    board_buff_effects(run, entity_id, 5, pushes)
    board_set_attr(entity, 8, 2 if board_attr(entity, 5) > 0 else 3)


def board_npc_turn(run: dict, pushes: list) -> list:
    """为每个存活 NPC 生成真实可行走路径及碰撞事件，随后开始下一回合。"""
    hero = board_entity(run)
    board_tick_buffs(run, 0, pushes)
    if hero['use_item']:
        cfg = resident_config('ChessBoardPropCfg', hero['use_item'])
        if cfg['type'] == 10006:
            board_set_attr(hero, cfg['params'][0], board_attr(hero, cfg['params'][0])-cfg['params'][1])
    hero['use_item'] = 0
    paths = []
    ids = sorted((r['id'] for r in run['map']['hero'] if r['id']), reverse=True)
    for entity_id in ids:
        entity = next((r for r in run['map']['hero'] if r['id'] == entity_id), None)
        if entity is None:
            continue
        cfg = resident_config('ChessBoardNPCPoolCfg', entity['type'])
        board_pool(run, cfg['npc_start_event'], 11, [entity_id], pushes, entity_id)
        amount = 0 if run.get('npc_stopped', 0) or board_attr(entity, 12) else sum(board_roll(run, entity))
        board_set_attr(entity, 5, amount)
        board_set_attr(entity, 8, 1)
        path = []
        while board_attr(entity, 5) > 0:
            choices = board_neighbors(run, entity['pos'])
            if not choices:
                break
            # 按当前朝向继续前进，路口按保存的随机状态选择合法出口。
            forward = next((p for p in choices if p[2] == entity['direction']), None)
            if forward is None:
                alternatives = [p for p in choices if p[2] != (entity['direction']+3)%6] or choices
                forward = alternatives[board_random(run, len(alternatives))]
            x, z, direction = forward
            path.append({'x': x, 'z': z})
            board_step(run, entity_id, x, z, direction, pushes, board_attr(entity, 5)-1)
            if entity not in run['map']['hero']:
                break
        paths.append({'npc_id': entity_id, 'path': path, 'move_num': amount})
        if entity in run['map']['hero']:
            board_pool(run, cfg['npc_stop_event'], 12, [entity_id], pushes, entity_id)
            board_tick_buffs(run, entity_id, pushes)
    if run.get('npc_stopped', 0):
        run['npc_stopped'] -= 1
    run['map']['turn'] += 1
    board_set_attr(hero, 8, 0)
    for turn, pool in resident_config('ChessBoardLevelCfg', run['chapter_id'], 'id_level')['level_event']:
        if turn == run['map']['turn']:
            board_pool(run, pool, 20, [turn], pushes)
    return paths


def board_use_prop(run: dict, request: dict, pushes: list) -> None:
    """处理配置定义的十二种道具，校验库存、目标与当前回合。"""
    hero = board_entity(run)
    item = request['prop_id']
    cfg = resident_config('ChessBoardPropCfg', item)
    arena = hero['chance']['chance_id'] and resident_config('ChessBoardChanceCfg', hero['chance']['chance_id'])['type'] == 14
    used = hero['chance']['choose_list'][2] if arena else hero['use_item']
    if used or item not in hero['item'] or not arena and board_attr(hero, 8) != 0:
        raise ValueError('道具未拥有或本回合已使用道具')
    params, kind = request['params'], cfg['type']
    target = None
    if kind in (10002, 10003):
        if len(params) != 2:
            raise ValueError('道具目标无效')
        target = next((r for r in run['map']['hero'] if r['id'] and r['pos'] == {'x': params[0], 'z': params[1]}), None)
        if target is None:
            raise ValueError('目标位置没有 NPC')
    if kind == 10001:
        board_add_buff(run, 0, cfg['params'][0], pushes)
    elif kind == 10002:
        run['map']['hero'].remove(target)
    elif kind == 10003:
        hero['pos'], target['pos'] = target['pos'], hero['pos']
        hero['direction'], target['direction'] = target['direction'], hero['direction']
    elif kind == 10004:
        if len(params) != 1 or not 1 <= params[0] <= board_attr(hero, 1):
            raise ValueError('指定骰子点数无效')
        board_set_attr(hero, 3, params[0])
    elif kind in (10005, 10006):
        board_set_attr(hero, cfg['params'][0], board_attr(hero, cfg['params'][0])+cfg['params'][1])
    elif kind == 10007:
        if len(params) != 2:
            raise ValueError('放置物件目标无效')
        if board_grid(run, *params)['thingTypeID']:
            raise ValueError('目标格子已有物件')
        board_change_grid(run, *params, cfg['params'][0], thing=True)
    elif kind == 10008:
        if params and params[0] == 1:
            for buff in list(hero['buff']):
                if resident_config('ChessBoardBuffPoolCfg', buff['id'])['clash'] == 1:
                    board_remove_buff(run, 0, buff['id'], pushes)
        elif len(params) == 3 and params[0] == 2:
            board_change_grid(run, params[1], params[2], -1, thing=True)
        else:
            raise ValueError('拆除道具参数无效')
    elif kind == 10009:
        run['npc_stopped'] = cfg['params'][0]
    elif kind == 10010:
        if params and params[0] == 2:
            board_add_buff(run, 0, cfg['params'][0], pushes)
        elif len(params) == 3 and params[0] == 1:
            target = next((r for r in run['map']['hero'] if r['id'] and r['pos'] == {'x': params[1], 'z': params[2]}), None)
            if target is None:
                raise ValueError('目标位置没有 NPC')
            board_add_buff(run, target['id'], cfg['params'][0], pushes)
        else:
            raise ValueError('增益道具目标无效')
    else:
        raise ValueError('棋盘道具类型未实现')
    hero['item'].remove(item)
    if arena:
        hero['chance']['choose_list'][2] = item
    else:
        hero['use_item'] = item


def board_finish_chance(user: dict, run: dict, request: dict, pushes: list) -> None:
    """按当前机会结算，原生战斗需要已保存的实际胜利；失败不推进主线。"""
    hero = board_entity(run)
    chance = hero['chance']
    if not chance['chance_id'] and run.get('last_settle') == request:
        return
    if chance['chance_id'] != request['chance_id'] or request['result'] not in (0, 1):
        raise ValueError('棋盘机会或结果不匹配')
    cfg = resident_config('ChessBoardChanceCfg', chance['chance_id'])
    if request['result'] == 0 and not chance['is_unlock']:
        raise ValueError('机会尚未解锁')
    won = request['result'] == 0
    if won and cfg['type'] in (3, 8, 10, 11, 13) and not run.get('battle_wins', {}).get(str(cfg['id'])):
        raise ValueError('尚未收到机会战斗的胜利结算')
    if cfg['type'] == 14:
        left, total, _ = chance['choose_list']
        if request['score'] != total or won and total < cfg['params'][0] or not won and left > 0:
            raise ValueError('竞技骰子结算与已保存点数不匹配')
        if not won:
            board_set_attr(hero, 13, board_attr(hero, 13)+1)
    if won:
        append_unique(run['finished_chances'], cfg['id'])
        if cfg['type'] == 2:
            hero['tmp_item'].extend(chance['item_list'])
        elif cfg['type'] == 5:
            option = cfg['params'][chance['item_list'][0]-1]
            if option[0] == 1:
                board_set_attr(hero, 4, board_attr(hero, 4)+option[1])
            elif option[0] == 2:
                hero['tmp_item'].extend(chance['item_list'][2:])
            elif option[0] == 4:
                board_add_buff(run, 0, option[1], pushes)
            elif option[0] == 5:
                run['npc_stopped'] = option[1]
        elif cfg['type'] == 9:
            earned = next((amount for grade, amount in cfg['params'][1] if grade == request['score']), None)
            if earned is None:
                raise ValueError('QTE 评级无效')
            if cfg['params'][0] == 1:
                board_set_attr(hero, 4, board_attr(hero, 4)+earned)
            else:
                hero['tmp_item'].extend(chance['item_list'][:earned])
        board_pool(run, cfg['chance_event'], 13, [cfg['id']], pushes)
    hero['chance'] = empty_chance()
    run['last_settle'] = deepcopy(request)
    pushes.append((85033, {'chance': empty_chance()}))


def board_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """全部 850xx 操作通过服务器事务路由，状态在协议编码成功后才落盘。"""
    response, pushes = {'result': 0}, []
    state = board_state(user)
    if command == 85028 and not state['chapter_id'] and state.get('last_closed'):
        return response, [(85035, {'chapter_id': 0}), (51001, board_history(user))]
    if command == 85000:
        chapter = request['chapter_id']
        cfg = resident_config('ChessBoardLevelCfg', chapter, 'id_level')
        if cfg['unlock_condition']:
            condition = resident_config('ConditionCfg', cfg['unlock_condition'])
            if condition['type'] != 11501 or condition['params'][0] not in state['cleared']:
                raise ValueError('请先完成前一棋盘章节')
        run = state['runs'].get(str(chapter))
        if not run or run.get('closed'):
            run = board_new_run(user, chapter)
            state['runs'][str(chapter)] = run
            response['map'] = deepcopy(run['map'])
            board_pool(run, cfg['eventpool_id'], 1, [], pushes)
            board_set_attr(board_entity(run), 6, 1)
        else:
            response['map'] = deepcopy(run['map'])
        state['chapter_id'] = chapter
        pushes.append((85035, {'chapter_id': chapter}))
        return response, pushes
    run, hero = board_run(user), board_entity(board_run(user))
    if command == 85012:
        if hero['chance']['chance_id'] or board_attr(hero, 7):
            raise ValueError('当前机会未结束或棋盘已完成')
        if board_attr(hero, 8) != 0:
            response['dice_list'] = run['dice']
        else:
            run['dice'] = board_roll(run, hero)
            board_set_attr(hero, 5, sum(run['dice']))
            board_set_attr(hero, 8, 1)
            run.pop('npc_response', None)
            response['dice_list'] = run['dice']
    elif command == 85038:
        chance = hero['chance']
        if not chance['chance_id'] or resident_config('ChessBoardChanceCfg', chance['chance_id'])['type'] != 14 or not chance['is_unlock']:
            raise ValueError('当前不是已解锁的竞技骰子机会')
        if chance['choose_list'][0] <= 0:
            raise ValueError('竞技骰子次数已用完')
        response['dice_list'] = board_roll(run, hero)
        chance['choose_list'][0] -= 1
        chance['choose_list'][1] += sum(response['dice_list'])
        response['total_cnt'] = chance['choose_list'][0]
        chance['choose_list'][2] = 0
        hero['use_item'] = 0
    elif command == 85010:
        target = request['next_pos']
        if target == hero['pos'] and run.get('last_move', {}).get('to') == target:
            return response, deepcopy(run['last_move']['pushes'])
        if hero['chance']['chance_id'] or board_attr(hero, 5) <= 0 or board_attr(hero, 8) not in (1, 2):
            raise ValueError('当前不能移动')
        candidate = next((p for p in board_neighbors(run, hero['pos']) if (p[0], p[1]) == (target['x'], target['z'])), None)
        if candidate is None:
            raise ValueError('目标格子不可通行或不是相邻格子')
        board_step(run, 0, *candidate, pushes, board_attr(hero, 5)-1)
        run['last_move'] = {'to': deepcopy(target), 'pushes': deepcopy(pushes)}
    elif command == 85014:
        if 'npc_response' in run:
            response['npc_path_list'] = deepcopy(run['npc_response'])
        else:
            if board_attr(hero, 8) != 3 or hero['chance']['chance_id']:
                raise ValueError('玩家移动或机会尚未结束')
            response['npc_path_list'] = board_npc_turn(run, pushes)
            run['npc_response'] = deepcopy(response['npc_path_list'])
    elif command == 85016:
        board_use_prop(run, request, pushes)
    elif command in (85020, 85018):
        chance = hero['chance']
        if chance['chance_id'] != request['chance_id']:
            raise ValueError('机会不属于当前格子')
        cfg = resident_config('ChessBoardChanceCfg', chance['chance_id'])
        if command == 85020:
            if not chance['is_unlock']:
                board_spend_gold(hero, cfg['cost'])
                chance['is_unlock'] = 1
        else:
            item = request['prop_id']
            if cfg['type'] != 1 or item not in chance['item_list'] or item in chance['choose_list']:
                raise ValueError('商品不在当前商店或已经购买')
            board_spend_gold(hero, resident_config('ChessBoardPropCfg', item)['cost'])
            chance['choose_list'].append(item)
            hero['tmp_item'].append(item)
    elif command == 85022:
        board_finish_chance(user, run, request, pushes)
    elif command in (85024, 85036):
        item = request['prop_id']
        if item not in hero['tmp_item']:
            raise ValueError('待领取道具不存在')
        if command == 85024:
            replace = request['replace_prop_id']
            if replace:
                if replace not in hero['item']:
                    raise ValueError('被替换道具未拥有')
                hero['item'].remove(replace)
            if len(hero['item']) >= 6:
                raise ValueError('棋盘道具栏已满')
            hero['item'].append(item)
        hero['tmp_item'].remove(item)
    elif command == 85026:
        task = request['task_id']
        if task not in run['finished_tasks']:
            cfg = resident_config('ChessBoardTaskPoolCfg', task)
            row = next((r for r in run['map']['task'] if r['id'] == task), None)
            if row is None or row['progress'] < cfg['need']:
                raise ValueError('棋盘任务尚未完成')
            append_unique(run['finished_tasks'], task)
            run['map']['task'].remove(row)
            board_pool(run, cfg['finishevent'], 18, [task], pushes)
    elif command == 85028:
        if not board_attr(hero, 7):
            raise ValueError('棋盘章节尚未完成')
        append_unique(state['cleared'], run['chapter_id'])
        state['chapter_id'] = 0
        state['last_closed'] = run['chapter_id']
        run['closed'] = True
        pushes += [(85035, {'chapter_id': 0}), (51001, board_history(user))]
    else:
        raise ValueError('未知棋盘请求')
    return response, pushes
