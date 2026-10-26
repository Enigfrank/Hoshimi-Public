"""饭堂委托、上岗、招牌菜、手动经营与离线收益。"""

from copy import deepcopy
import time

from backhome_catalog import archive_id, backhome_catalog, home_condition, home_config, home_hero
from backhome_data import backhome_state, new_entrust
from battle_rewards import grant_rewards, spend_items
from hero_training_data import item_cost

CANTEEN_COMMANDS = {58012, 58018, 58024, 58100, 58102, 58104, 58106, 58108, 58114, 58116, 58120}


def food_info(user: dict, food_id: int) -> dict:
    """菜谱必须已解锁，熟练度和累计制作数独立保存。"""
    cfg = home_config('BackHomeCanteenFoodCfg', food_id)
    if not home_condition(user, cfg['unlock']):
        raise ValueError('菜谱尚未解锁')
    return backhome_state(user)['food'].setdefault(str(food_id), {'id': food_id, 'proficiency': 0, 'num': 0})


def spend_ingredients(user: dict, food_id: int, count: int) -> None:
    """实际制作时按菜谱消耗食材，不能直接使用客户端声明的成本。"""
    cfg = home_config('BackHomeCanteenFoodCfg', food_id)
    spend_items(user, [{'id': i, 'num': n * count} for i, n in cfg['ingredient_list']])


def available_worker(user: dict, hero_id: int, ignored_position: int = 0) -> dict:
    """委托和工作按共享档案互斥，禁止同一角色的不同形态重复上岗。"""
    hero = home_hero(user, hero_id)
    canteen = user['backhome']['canteen']
    if hero['is_lock']:
        raise ValueError('角色已被游园街功能锁定')
    if any(c['hero_id'] and archive_id(c['hero_id']) == hero['archives_id'] for c in canteen['careers']):
        raise ValueError('角色正在饭堂工作')
    if any(e['pos'] != ignored_position and e['start_time'] and any(archive_id(i) == hero['archives_id'] for i in e['hero_list']) for e in canteen['entrust']):
        raise ValueError('角色已经出发委托')
    return hero


def dispatch_entrust(user: dict, request: dict) -> None:
    """委托时间以配置分钟数为准，空角色列表用于取消未完成的委托。"""
    state = backhome_state(user)
    row = next((r for r in state['canteen']['entrust'] if r['pos'] == request['pos']), None)
    if row is None:
        raise ValueError('委托槽位尚未解锁')
    ids = request['hero_list']
    if not ids:
        if not row['start_time']:
            raise ValueError('委托尚未派遣')
        row['hero_list'], row['start_time'], row['duration'] = [], 0, 0
        return
    cfg = home_config('BackHomeCanteenTaskCfg', row['id'])
    if row['start_time'] or not cfg['need'][0] <= len(ids) <= row['num_max'] or len(set(archive_id(i) for i in ids)) != len(ids):
        raise ValueError('委托已派遣或角色数量无效')
    if request['duration'] not in [r[0] for r in cfg['time']]:
        raise ValueError('委托时长不符合配置')
    cost = request['duration'] * backhome_catalog()['settings']['dorm_canteen_fatigue_cost'][0] // 60
    for hero_id in ids:
        if available_worker(user, hero_id, row['pos'])['fatigue'] < cost:
            raise ValueError('委托角色心情不足')
    row['hero_list'], row['start_time'], row['duration'] = list(ids), int(time.time()), request['duration']


def finish_entrust(user: dict, request: dict) -> dict:
    """只领取真正到期的委托，奖励、心情扣除和新委托同一次提交。"""
    state = backhome_state(user)
    positions = request['pos']
    if not positions or len(set(positions)) != len(positions):
        raise ValueError('委托领奖槽位为空或重复')
    rewards, extra, renewed, fatigue = [], [], [], []
    for position in positions:
        row = next((r for r in state['canteen']['entrust'] if r['pos'] == position), None)
        if row is None or not row['start_time'] or int(time.time()) < row['start_time'] + row['duration'] * 60:
            raise ValueError('委托尚未完成')
        cfg = home_config('BackHomeCanteenTaskCfg', row['id'])
        rate = next(r[1] for r in cfg['time'] if r[0] == row['duration'])
        gain = [{'id': i, 'num': n * rate // 100} for i, n in cfg['reward_list'] if n * rate // 100]
        rewards.extend(gain)
        extra.append({'pos': position, 'extra_reward': 0, 'reward_list': gain})
        cost = row['duration'] * backhome_catalog()['settings']['dorm_canteen_fatigue_cost'][0] // 60
        for hero_id in row['hero_list']:
            hero = home_hero(user, hero_id)
            hero['fatigue'] = max(0, hero['fatigue'] - cost)
            fatigue.append({'archives_id': hero['archives_id'], 'fatigue': hero['fatigue']})
        ids = sorted(int(i) for i in backhome_catalog()['BackHomeCanteenTaskCfg'])
        replacement = new_entrust(position, ids[(ids.index(row['id']) + 1) % len(ids)])
        row.update(replacement)
        renewed.append(deepcopy(row))
    grant_rewards(user, rewards)
    return {'result': 0, 'extra_reward': extra, 'entrust': renewed, 'fatigue_list': fatigue}


def tick_canteen(user: dict) -> dict:
    """按招牌菜完成时间和真实食材推进离线经营，余额领取前不会重复结算。"""
    state = backhome_state(user)
    canteen = state['canteen']
    now = int(time.time())
    previous = canteen['business']['last_earnings_update_time']
    attractive = canteen['attractive']
    reduce, interval = backhome_catalog()['settings']['canteen_hot_reduce']
    cycles = max(0, now - attractive['dynamic_update_time']) // interval
    if attractive['dynamic'] and cycles:
        attractive['dynamic'] = max(0, attractive['dynamic'] - cycles * reduce)
        attractive['dynamic_update_time'] += cycles * interval
    consumed = {}
    if state['canteen_mode'] == 0:
        for dish in canteen['signature_dish']:
            cfg = home_config('BackHomeCanteenFoodCfg', dish['food_id'])
            elapsed = max(0, now - state.get('dish_updated', {}).get(str(cfg['id']), previous))
            count = min(elapsed // cfg['cost_time'], dish['sell_num'] - dish['sold_num'])
            for item, amount in cfg['ingredient_list']:
                count = min(count, user.get('materials', {}).get(str(item), 0) // amount)
            maximum = backhome_catalog()['settings']['dorm_canteen_storage_default'][0] + sum(
                home_config('BackHomeCanteenFurnitureCfg', home_config('BackHomeCanteenFurnitureIDCfg', r['uid'])['type_id'])['canteen_storage_max'][r['level'] - 1] for r in state['canteen_furnitures'])
            count = min(count, max(0, maximum - canteen['business']['earnings']) // cfg['sell'])
            if count <= 0:
                if elapsed >= cfg['cost_time']:
                    state.setdefault('dish_updated', {})[str(cfg['id'])] = now
                continue
            spend_ingredients(user, cfg['id'], count)
            gain = cfg['sell'] * count
            dish['sold_num'] += count
            dish['sell_earnings'] += gain
            canteen['business']['earnings'] += gain
            state.setdefault('dish_updated', {})[str(cfg['id'])] = now - elapsed % cfg['cost_time']
            food = food_info(user, cfg['id'])
            food['num'] += count
            for item, amount in cfg['ingredient_list']:
                consumed[item] = consumed.get(item, 0) + amount * count
    if canteen['signature_dish']:
        canteen['business']['last_earnings_update_time'] = now
    return {'architecture_id': 4, 'earnings': canteen['business']['earnings'],
            'last_earnings_update_time': now,
            'del_ingredients': [{'id': i, 'num': n} for i, n in consumed.items()], 'fatigue_list': [],
            'signature_dish': deepcopy(canteen['signature_dish']), 'food': deepcopy(list(state['food'].values())), 'npc_change': []}


def manual_settlement(user: dict, request: dict) -> dict:
    """手动营业以客人实例去重，按品质、菜谱和食材核算实际收入。"""
    state = backhome_state(user)
    if state['canteen_mode'] != 1 or not request['oper_list']:
        raise ValueError('饭堂未手动营业或订单为空')
    now = int(time.time())
    if now - state.get('manual_started', now) > backhome_catalog()['settings']['dorm_operate_time'][0] + 60:
        raise ValueError('本次手动营业已结束')
    earned, popularity = 0, 0
    for row in request['oper_list']:
        key = str(row['guest_uid'])
        if key in state['manual_orders'] or row['guest_uid'] <= 0 or not 1 <= row['quality'] <= 3 or not 0 <= row['evaluate'] <= 6:
            raise ValueError('订单重复、客人编号或菜品品质无效')
        food = food_info(user, row['food_id'])
        home_config('BackHomeCustomerCfg', row['guest_id'])
        if row['special_event'] != state['canteen']['special_event']['event_id']:
            raise ValueError('订单特殊事件与本轮营业不匹配')
        cfg = home_config('BackHomeCanteenFoodCfg', row['food_id'])
        spend_ingredients(user, row['food_id'], 1)
        rate = backhome_catalog()['settings']['canteen_customer_evaluation'][row['quality'] - 1][1]
        if row['evaluate']:
            popularity += backhome_catalog()['settings']['canteen_hot_increase'][row['evaluate'] - 1][1]
        earned += cfg['sell'] * rate // 100
        food['num'] += 1
        proficiency = dict(backhome_catalog()['settings']['canteen_cook_mastery']).get(row['quality'], 0)
        food['proficiency'] = min(backhome_catalog()['settings']['canteen_cook_mastery_max'][0], food['proficiency'] + proficiency)
        state['manual_orders'].append(key)
        dish = next((d for d in state['canteen']['signature_dish'] if d['food_id'] == row['food_id']), None)
        if dish:
            if dish['sold_num'] >= dish['sell_num']:
                raise ValueError('招牌菜订单已经售完')
            dish['sold_num'] += 1
            dish['sell_earnings'] += cfg['sell'] * rate // 100
    grant_rewards(user, [{'id': 39, 'num': earned}] if earned else [])
    state['canteen']['accruing_earnings'] += earned
    attractive = state['canteen']['attractive']
    attractive['dynamic'] = max(0, attractive['dynamic'] + popularity * backhome_catalog()['settings']['canteen_hot_hold'][0] // 100)
    attractive['dynamic_update_time'] = now
    return {'result': 0}


def canteen_request(user: dict, command: int, request: dict) -> dict:
    """处理实际饭堂协议，成功不能取代未满足的解锁、时间和库存条件。"""
    state = backhome_state(user)
    if 'architecture_id' in request and request['architecture_id'] != 4:
        raise ValueError('饭堂建筑编号无效')
    if command == 58018:
        return finish_entrust(user, request)
    if command == 58120:
        return manual_settlement(user, request)
    if command in (58100, 58102):
        rows = request['entrust_list'] if command == 58100 else [request]
        if not rows or len({r['pos'] for r in rows}) != len(rows):
            raise ValueError('批量委托为空或槽位重复')
        for row in rows:
            dispatch_entrust(user, row)
    elif command == 58024:
        position = request['pos']
        unlocks = dict(backhome_catalog()['settings']['dorm_canteen_task_unlock'])
        if position not in unlocks or not home_condition(user, unlocks[position]) or any(r['pos'] == position for r in state['canteen']['entrust']):
            raise ValueError('委托槽位不存在、已解锁或前置条件未满足')
        row = new_entrust(position, position)
        state['canteen']['entrust'].append(row)
        return {'result': 0, 'entrust': deepcopy(row)}
    elif command == 58012:
        row = next((r for r in state['canteen']['entrust'] if r['pos'] == request['pos']), None)
        if row is None or row['start_time']:
            raise ValueError('委托槽位无效或正在执行')
        cfg = home_config('BackHomeCanteenTaskCfg', row['id'])
        spend_items(user, item_cost([[39, cfg['cost']]]))
        ids = sorted(int(i) for i in backhome_catalog()['BackHomeCanteenTaskCfg'])
        refreshed = new_entrust(row['pos'], ids[(ids.index(row['id']) + 1) % len(ids)])
        refreshed['refresh_times'] = row['refresh_times'] + 1
        row.update(refreshed)
        return {'result': 0, 'entrust': deepcopy(row)}
    elif command == 58104:
        job, hero_id = request['type'], request['hero_id']
        row = next((r for r in state['canteen']['careers'] if r['type'] == job), None)
        if row is None:
            raise ValueError('饭堂职业无效')
        npc = backhome_catalog()['BackHomeNpcCfg'].get(str(hero_id))
        if npc:
            if npc['npc_work'] != job:
                raise ValueError('NPC职业不匹配')
        elif hero_id:
            if row['hero_id'] == hero_id:
                return {'result': 0}
            if available_worker(user, hero_id)['fatigue'] <= 0:
                raise ValueError('角色心情不足')
        row['hero_id'] = hero_id
    elif command == 58106:
        earnings = state['canteen']['business']['earnings']
        grant_rewards(user, [{'id': 39, 'num': earnings}] if earnings else [])
        state['canteen']['accruing_earnings'] += earnings
        state['canteen']['business']['earnings'] = 0
        state['canteen']['business']['last_receive_earnings_time'] = int(time.time())
        return {'result': 0, 'earnings': earnings}
    elif command == 58108:
        if request['cmd'] not in (0, 1):
            raise ValueError('营业模式无效')
        if request['cmd'] != state['canteen_mode']:
            state['canteen_mode'] = request['cmd']
            state['manual_orders'] = []
            state['manual_started'] = int(time.time())
    elif command == 58114:
        cfg = home_config('BackHomeCanteenFoodCfg', request['food_id'])
        food_info(user, cfg['id'])
        rows = state['canteen']['signature_dish']
        existing = next((r for r in rows if r['food_id'] == cfg['id']), None)
        number = request['sell_num']
        if number == 0:
            if existing:
                rows.remove(existing)
        else:
            limit = backhome_catalog()['settings']['dorm_canteen_sell_default'][0] + sum(
                home_config('BackHomeCanteenFurnitureCfg', home_config('BackHomeCanteenFurnitureIDCfg', r['uid'])['type_id'])['canteen_sell_max'][r['level'] - 1] for r in state['canteen_furnitures'])
            if (existing is None and len(rows) >= limit) or number > 10000:
                raise ValueError('招牌菜数量或售价档位超限')
            if existing:
                rows.remove(existing)
            rows.append({'food_id': cfg['id'], 'sell_num': number, 'sold_num': 0, 'sell_earnings': 0})
            state.setdefault('dish_updated', {})[str(cfg['id'])] = int(time.time())
    elif command == 58116:
        furniture = home_config('BackHomeCanteenFurnitureIDCfg', request['uid'])
        cfg = home_config('BackHomeCanteenFurnitureCfg', furniture['type_id'])
        row = next((r for r in state['canteen_furnitures'] if r['uid'] == request['uid']), None)
        current = row['level'] if row else 0
        if current >= len(cfg['cost_material']) or not all(home_condition(user, i) for i in cfg['condition'][current]):
            raise ValueError('厨具已满级或升级条件未满足')
        spend_items(user, item_cost(cfg['cost_material'][current]))
        if row:
            row['level'] += 1
        else:
            state['canteen_furnitures'].append({'uid': request['uid'], 'level': 1})
    else:
        raise ValueError('饭堂指令未支持')
    return {'result': 0}
