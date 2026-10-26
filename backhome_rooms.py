"""心居解锁、入住、家具布局、赠礼与互动。"""

from copy import deepcopy
import time

from backhome_catalog import archive_id, backhome_catalog, home_condition, home_config, home_hero, room_level
from backhome_data import backhome_state, empty_layout, new_room
from battle_rewards import grant_rewards, spend_items
from hero_training_data import item_cost
from skin_scene_data import skin_available

ROOM_COMMANDS = {58010, 58040, 58126, 58128, 58130, 58132, 58134, 58136, 58138,
                 58142, 58144, 58146, 58152, 58154, 58158, 58196, 58218}


def owned_room(user: dict, room_id: int) -> dict:
    """房间必须已解锁，不能用参观房间编号操作自己的存档。"""
    room = next((r for r in backhome_state(user)['rooms'] if r['id'] == room_id), None)
    if room is None:
        raise ValueError('心居尚未解锁')
    return room


def valid_position(position: int) -> bool:
    """楼层位置按客户端101起编号，首层七间、其余每层十一间。"""
    floor, slot = divmod(position, 100)
    return 1 <= floor <= backhome_catalog()['settings']['floor_max'] and 1 <= slot <= (7 if floor == 1 else 11)


def layout_counts(rows: list) -> dict:
    """校验协议墙面、坐标、家具所属场景及套装后统计占用数量。"""
    result, sides = {}, set()
    if len(rows) > 5:
        raise ValueError('家具布局墙面数量无效')
    for side in rows:
        if side['type'] not in (1, 2, 3, 4, 5) or side['type'] in sides:
            raise ValueError('家具布局墙面无效或重复')
        sides.add(side['type'])
        if side['default_suit_id']:
            suit = home_config('BackHomeSuitCfg', side['default_suit_id'])
            for furniture, count in suit['suit_num']:
                result[str(furniture)] = result.get(str(furniture), 0) + count
        for row in side['furniture_pos']:
            home_config('BackHomeFurniture', row['furniture_id'])
            if not -1000000 <= row['x'] <= 1000000 or not -1000000 <= row['y'] <= 1000000 or not -360000 <= row['rotation'] <= 360000:
                raise ValueError('家具布局坐标无效')
            key = str(row['furniture_id'])
            result[key] = result.get(key, 0) + 1
    return result


def validate_layout(user: dict, room: dict, rows: list) -> None:
    """保留赠送家具占用，禁止跨房间复制同一库存；私人房家具须先赠送。"""
    counts = layout_counts(rows)
    room_type = home_config('BackHomeCfg', room['id'])['type']
    given = {str(r['id']): r['num'] for r in room['give_furnitures']}
    occupied_count = sum(n for i, n in counts.items() if home_config('BackHomeFurniture', int(i))['is_occupy'] == 1)
    maximum = backhome_catalog()['settings']['dorm_lobby_furniture_limited'][0] if room_type == 2 else home_config('BackHomeDormLevel', room_level(room))['furniture_max']
    if occupied_count > maximum:
        raise ValueError('家具摆放数量超过房间上限')
    for key, count in counts.items():
        cfg = home_config('BackHomeFurniture', int(key))
        if room_type not in cfg['scene_id']:
            raise ValueError('家具不适用于该场景')
        if room_type == 3 and cfg['is_give'] == 1 and count > given.get(key, 0):
            raise ValueError('私人心居家具尚未赠送')
        occupied = max(count, given.get(key, 0))
        for other in user['backhome']['rooms']:
            if other['id'] != room['id']:
                other_given = next((r['num'] for r in other['give_furnitures'] if r['id'] == int(key)), 0)
                occupied += max(layout_counts(other['layout']['furniture_pos_list']).get(key, 0), other_given)
        if occupied > user.get('materials', {}).get(key, 0):
            raise ValueError('可用家具不足')


def award_room_levels(user: dict, hero_id: int, room: dict, previous: int) -> None:
    """心居升级奖励由服务端发放一次，赠礼事实与奖励同次保存。"""
    aid = archive_id(hero_id)
    ids = home_config('HeroRecordCfg', aid)['hero_id']
    owned = {h['id'] for h in user['heroes'] if h.get('unlock', 1)}
    first = next(i for i in ids if i in owned and str(i) in backhome_catalog()['BackHomeHeroCfg'])
    cfg = home_config('BackHomeHeroCfg', first)
    saved = user['backhome'].setdefault('room_awards', {})
    previous = max(previous, saved.get(str(room['id']), previous))
    rewards = []
    for level in range(previous + 1, room_level(room) + 1):
        for item in cfg['level_reward'][level - 1]:
            reward = home_config('BackHomeDormLevelRewardCfg', item)
            if reward['type'] == 3:
                rewards.extend(item_cost([reward['reward']]))
    grant_rewards(user, rewards)
    saved[str(room['id'])] = max(previous, room_level(room))


def feed_hero(user: dict, request: dict) -> dict:
    """每日按档案喂食一次，增加心情和累计次数；同档案其他形态共享限制。"""
    state = backhome_state(user)
    settings = backhome_catalog()['settings']
    if request['type'] not in (1, 2):
        raise ValueError('喂食类型无效')
    heroes = list(state['heroes'].values()) if request['type'] == 1 else [home_hero(user, request['hero_id'])]
    changes = []
    for hero in heroes:
        if hero['feed_times'] >= settings['dorm_feed_max'][0] or hero['fatigue'] >= settings['canteen_hero_fatigue_max'][0]:
            continue
        hero['fatigue'] = min(settings['canteen_hero_fatigue_max'][0], hero['fatigue'] + settings['dorm_feed_fatigue'][0])
        hero['feed_times'] += 1
        hero['total_feed_times'] += 1
        changes.append({'archives_id': hero['archives_id'], 'fatigue': hero['fatigue']})
    if not changes:
        raise ValueError('角色心情已满或今日已喂食')
    return {'result': 0, 'fatigue_list': changes}


def gift_furniture(user: dict, request: dict) -> dict:
    """家具保留拥有总量并计入已赠送数量，限制单房间上限并累计房间经验。"""
    hero = home_hero(user, request['hero_id'])
    room = next((r for r in user['backhome']['rooms'] if r['id'] != 5 and hero['archives_id'] in r['archives_id']), None)
    if room is None or not request['furniture']:
        raise ValueError('角色未入住房间或家具列表为空')
    previous = room_level(room)
    totals = {str(r['id']): r['num'] for r in room['give_furnitures']}
    for row in request['furniture']:
        cfg = home_config('BackHomeFurniture', row['id'])
        if row['num'] <= 0 or cfg['is_give'] != 1:
            raise ValueError('家具不可赠送或数量无效')
        key = str(row['id'])
        totals[key] = totals.get(key, 0) + row['num']
        all_given = totals[key] + sum(r['num'] for other in user['backhome']['rooms'] if other['id'] != room['id']
                                      for r in other['give_furnitures'] if r['id'] == row['id'])
        if totals[key] > cfg['give_max'] or all_given > user.get('materials', {}).get(key, 0):
            raise ValueError('家具不足或赠送超过房间上限')
        room['exp'] += cfg['dorm_exp'] * row['num']
    room['give_furnitures'] = [{'id': int(i), 'num': n} for i, n in totals.items()]
    award_room_levels(user, request['hero_id'], room, previous)
    return {'result': 0}


def rhythm_settlement(user: dict, request: dict) -> dict:
    """按真实节奏游戏档位和每日上限结算，短时间重传不重复发奖。"""
    state = backhome_state(user)
    cfg = home_config('BackHomeBreakGameCfg', request['stage_id'])
    hero = home_hero(user, request['hero_id'])
    score = request['percentage_complete']
    if score > 200 or hero['fatigue'] < backhome_catalog()['settings']['dorm_mg_break_cost'][0]:
        raise ValueError('节奏游戏成绩无效或角色心情不足')
    now = int(time.time())
    if now - state.get('last_rhythm_settlement', 0) < cfg['total_check']:
        raise ValueError('节奏游戏重复结算或游玩时间不足')
    rank = max(i for i, value in enumerate(backhome_catalog()['settings']['dorm_mg_break_level']) if score >= value)
    item, amount = cfg['reward_list'][rank]
    maximum = backhome_catalog()['settings']['dorm_mg_reward_daily_max'][0]
    amount = min(amount, max(0, maximum - state['daily_game_currency_num']))
    rewards = [{'id': item, 'num': amount}] if amount else []
    grant_rewards(user, rewards)
    state['daily_game_currency_num'] += amount
    hero['fatigue'] -= backhome_catalog()['settings']['dorm_mg_break_cost'][0]
    state['last_rhythm_settlement'] = now
    return {'result': 0, 'reward_list': rewards, 'fatigue': hero['fatigue']}


def room_request(user: dict, command: int, request: dict) -> dict:
    """执行心居操作，复杂批量修改由外层克隆账号保证全部成功或全部回滚。"""
    state = backhome_state(user)
    if command == 58138:
        return feed_hero(user, request)
    if command == 58136:
        return gift_furniture(user, request)
    if command == 58154:
        return rhythm_settlement(user, request)
    if command == 58130:
        cfg = home_config('BackHomeCfg', request['architecture_id'])
        if cfg['type'] != 3 or any(r['id'] == cfg['id'] for r in state['rooms']):
            raise ValueError('房间无效或已经解锁')
        if not valid_position(request['pos_id']) or any(r['pos_id'] == request['pos_id'] for r in state['rooms']):
            raise ValueError('心居楼层位置无效或已占用')
        spend_items(user, item_cost(cfg['unlock_cost']))
        grant_rewards(user, item_cost(cfg['default_item']))
        state['rooms'].append(new_room(cfg['id'], request['pos_id']))
    elif command in (58132, 58134):
        room = owned_room(user, request['architecture_id'])
        ids = request['hero_id'] if command == 58132 else [request['hero_id']]
        maximum = backhome_catalog()['settings']['dorm_lobby_hero_max'][0] if room['id'] == 5 else 1
        aids = [home_hero(user, i)['archives_id'] for i in ids]
        if len(aids) != len(set(aids)) or len(aids) > maximum:
            raise ValueError('入住角色数量无效或共享档案重复')
        for hero_id in ids:
            hero = home_hero(user, hero_id)
            if hero['is_lock']:
                raise ValueError('角色已被其他游园街功能占用')
            hero['hero_id'] = hero_id
        if room['id'] != 5:
            if not aids:
                raise ValueError('私人心居必须保留入住档案')
            if room['archives_id'] and room['archives_id'] != aids:
                raise ValueError('房间已经由其他角色入住')
            if any(r['id'] not in (5, room['id']) and any(a in r['archives_id'] for a in aids) for r in state['rooms']):
                raise ValueError('角色已经入住房间')
        if room['id'] != 5 and not room['archives_id']:
            award_room_levels(user, ids[0], room, 0)
        room['archives_id'] = aids
    elif command == 58010:
        room = owned_room(user, request['architecture_id'])
        layout = deepcopy(request['furniture_layout'])
        if layout['temp_id']:
            template = next((r for r in state['template'] if r['id'] == layout['temp_id']), None)
            if template is None or template['type'] != home_config('BackHomeCfg', room['id'])['type']:
                raise ValueError('布局模板不存在或场景不符')
            layout['furniture_pos_list'] = deepcopy(template['furniture_pos_list'])
        validate_layout(user, room, layout['furniture_pos_list'])
        room['layout'] = layout
        room['picture_link'] = request['picture_link']
        state['layout_uid'] = state.get('layout_uid', 1) + 1
    elif command == 58040:
        host = request.get('host_info', {})
        if host.get('user_id') and host['user_id'] != user['uid']:
            from local_social import local_user
            target = local_user(user, host['user_id'])
            if not target.get('backhome', {}).get('share_is_open') or host['layout_uid'] != target['backhome'].get('layout_uid', 1):
                raise ValueError('目标玩家未开放模板保存或展示布局已经变更')
        if request['type'] not in (2, 3) or not request['name'].strip() or len(request['name']) > 32:
            raise ValueError('模板类型或名称无效')
        maximum = backhome_catalog()['settings']['dorm_lobby_template_max' if request['type'] == 2 else 'dorm_template_max'][0]
        if not 1 <= request['pos'] <= maximum or request['id'] <= 0:
            raise ValueError('模板位置或编号无效')
        rows = request['furniture_pos_list']
        if request['architecture_id']:
            room = owned_room(user, request['architecture_id'])
            if home_config('BackHomeCfg', room['id'])['type'] != request['type']:
                raise ValueError('模板房间类型不匹配')
            rows = room['layout']['furniture_pos_list']
        layout_counts(rows)
        state['template'] = [r for r in state['template'] if r['id'] != request['id'] and not (r['type'] == request['type'] and r['pos'] == request['pos'])]
        state['template'].append({'id': request['id'], 'type': request['type'], 'name': request['name'],
                                  'pos': request['pos'], 'furniture_pos_list': deepcopy(rows)})
    elif command in (58142, 58144):
        template = next((r for r in state['template'] if r['id'] == request['id']), None)
        if template is None:
            raise ValueError('模板不存在')
        if command == 58144:
            state['template'].remove(template)
            for room in state['rooms']:
                if room['layout']['temp_id'] == request['id']:
                    room['layout']['temp_id'] = 0
        else:
            if not request['name'].strip() or len(request['name']) > 32:
                raise ValueError('模板名称无效')
            template['name'] = request['name']
    elif command == 58146:
        if not request['dorm_pos_list'] or len({r['architecture_id'] for r in request['dorm_pos_list']}) != len(request['dorm_pos_list']):
            raise ValueError('楼层位置列表为空或重复')
        for row in request['dorm_pos_list']:
            room = owned_room(user, row['architecture_id'])
            if room['id'] == 5 or not valid_position(row['pos_id']):
                raise ValueError('楼层位置无效')
            room['pos_id'] = row['pos_id']
        positions = [r['pos_id'] for r in state['rooms']]
        if len(positions) != len(set(positions)):
            raise ValueError('楼层位置重复')
    elif command == 58152:
        room = owned_room(user, request['architecture_id'])
        state['exhibition_id'] = room['id']
        state['picture_link'] = request['picture_link']
        state['layout_uid'] = state.get('layout_uid', 1) + 1
    elif command == 58158:
        state['share_is_open'] = request['is_open']
    elif command == 58126:
        hero = home_hero(user, request['hero_id'])
        cfg = home_config('BackHomeHeroSkinCfg', request['skin_id'])
        owned = next(h for h in user['heroes'] if h['id'] == request['hero_id'])
        if cfg['hero_id'] != owned['id'] or not skin_available(user, owned, request['skin_id']):
            raise ValueError('后宅皮肤未拥有或模型不匹配')
        row = next(r for r in state['hero_setting'] if r['hero_id'] == request['hero_id'])
        row['skin_id'] = request['skin_id']
    elif command == 58218:
        if request['type'] not in (0, 1):
            raise ValueError('角色占用状态无效')
        home_hero(user, request['hero_id'])['is_lock'] = request['type']
    elif command == 58196:
        if request['type'] != 2:
            raise ValueError('图鉴类型无效')
        home_config('IdolDanceDIYActionCfg', request['id'])
        rows = state['collections'].setdefault(str(request['type']), [])
        if request['id'] not in rows:
            rows.append(request['id'])
    elif command == 58128:
        cfg = home_config('BackHomeDormBirthdayCfg', request['theatrical_id'])
        rows = state['theatrical_list']
        if any(r['id'] == request['theatrical_id'] for r in rows):
            raise ValueError('心居小剧场已观看')
        rows.append({'id': request['theatrical_id'], 'timestamp_list': [int(time.time())]})
    else:
        raise ValueError('心居指令未支持')
    return {'result': 0}
