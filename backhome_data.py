"""游园街初始化、业务日刷新、角色心情和客户端快照。"""

from copy import deepcopy
import time

from backhome_catalog import archive_id, backhome_catalog, home_config, room_level
from battle_rewards import grant_rewards
from server_clock import business_day


def empty_layout() -> dict:
    """初始化客户端需要的五个墙面与地面布局集合。"""
    return {'temp_id': 0, 'furniture_pos_list': [
        {'type': i, 'default_suit_id': 0, 'furniture_pos': []} for i in range(1, 6)]}


def new_room(room_id: int, position: int) -> dict:
    """创建真实房间，不占用未拥有的家具。"""
    return {'id': room_id, 'pos_id': position, 'archives_id': [], 'layout': empty_layout(),
            'liked_num': 0, 'give_furnitures': [], 'exp': 0}


def new_entrust(position: int, task_id: int) -> dict:
    """以固定配置顺序选择委托；不伪称官方随机概率。"""
    cfg = home_config('BackHomeCanteenTaskCfg', task_id)
    tags = [{'type': row[0][0], 'tag': row[0][1]} for row in cfg['tag_list'] if row]
    return {'pos': position, 'id': task_id, 'hero_list': [], 'tags': tags,
            'num_max': cfg['need'][1], 'refresh_times': 0, 'start_time': 0, 'duration': 0}


def backhome_state(user: dict) -> dict:
    """补齐领域存档；登录只初始化结构，首次进入时才发放配置初始物品。"""
    now = int(time.time())
    catalog = backhome_catalog()
    if 'backhome' not in user:
        careers = []
        for job in (1, 2, 3):
            npc = min((c['id'] for c in catalog['BackHomeNpcCfg'].values() if c['npc_work'] == job), default=0)
            careers.append({'type': job, 'hero_id': npc})
        user['backhome'] = {'version': 1, 'starter_claimed': False, 'day': business_day(now),
            'last_fatigue_update_time': now, 'heroes': {}, 'hero_setting': [],
            'rooms': [new_room(5, 0), new_room(6, 101)], 'template': [], 'exhibition_id': 5,
            'share_is_open': False, 'daily_game_currency_num': 0, 'theatrical_list': [], 'fatigue_remainder': {},
            'backhome_suit_id_list': [], 'canteen_furnitures': [
                {'uid': int(i), 'level': 1} for i, c in catalog['BackHomeCanteenFurnitureIDCfg'].items() if not c['unlock']],
            'food': {}, 'canteen': {'id': 4, 'entrust': [new_entrust(1, 1)], 'careers': careers,
                'accruing_earnings': 0, 'signature_dish': [], 'special_event': {'event_id': 0, 'start_time': 0},
                'business': {'earnings': 0, 'last_earnings_update_time': now, 'last_receive_earnings_time': now},
                'attractive': {'dynamic': 0, 'dynamic_update_time': now}}, 'canteen_mode': 0,
            'manual_orders': [], 'visits': [], 'likes': {}, 'claimed_visits': [], 'collections': {},
            'idol': {'hero_pos_list': [], 'attack_hero_id_list': [], 'defend_hero_id_list': [],
                     'heroes': {}, 'use_times': 0, 'camp_list': [], 'pve': {}, 'history': [],
                     'get_id_list': [], 'point': 0, 'sequences': [], 'shared': [], 'collected': [],
                     'sequence_likes': [], 'sequence_views': []}}
    state = user['backhome']
    for owned in user.get('heroes', []):
        hero_id = owned['id']
        if not owned.get('unlock', 1) or str(hero_id) not in catalog['BackHomeHeroCfg']:
            continue
        aid = archive_id(hero_id)
        if str(aid) not in state['heroes']:
            state['heroes'][str(aid)] = {'archives_id': aid, 'hero_id': hero_id,
                'fatigue': catalog['settings']['canteen_hero_fatigue_max'][0],
                'feed_times': 0, 'total_feed_times': 0, 'is_lock': 0}
        if not any(r['hero_id'] == hero_id for r in state['hero_setting']):
            state['hero_setting'].append({'hero_id': hero_id, 'skin_id': hero_id})
        if catalog['BackHomeHeroCfg'][str(hero_id)]['idol_usable'] and str(hero_id) not in state['idol']['heroes']:
            state['idol']['heroes'][str(hero_id)] = {'hero_id': hero_id,
                'attribute_list': [a[0] for a in home_config('BackHomeHeroCfg', hero_id)['idol_base_attribute']]}
    if not state['idol']['attack_hero_id_list'] and state['idol']['heroes']:
        first = next((i for i in catalog['settings']['dorm_idol_default_battle_hero'] if str(i) in state['idol']['heroes']), min(int(i) for i in state['idol']['heroes']))
        state['idol']['attack_hero_id_list'] = [first]
        state['idol']['defend_hero_id_list'] = [first]
    if state['day'] != business_day(now):
        state['day'] = business_day(now)
        state['daily_game_currency_num'] = 0
        state['idol']['use_times'] = 0
        state['idol']['camp_list'] = []
        for hero in state['heroes'].values():
            hero['feed_times'] = 0
    week = (business_day(now) + 3) // 7
    if state.get('week', week) != week:
        state['idol']['point'], state['idol']['get_id_list'] = 0, []
    state['week'] = week
    if not state['idol']['camp_list']:
        camps = catalog['settings']['dorm_idol_hero_exercise_camp_random_list']
        count = catalog['settings']['dorm_idol_hero_exercise_camp_random_num'][0]
        offset = state['day'] % len(camps)
        state['idol']['camp_list'] = (camps + camps)[offset:offset + count]
    sync_room_levels(user)
    return state


def sync_room_levels(user: dict) -> None:
    """心居经验由实际赠礼及档案专属家具持有量恢复，新增升级奖励只发一次。"""
    from backhome_rooms import award_room_levels
    state = user['backhome']
    cfgs = backhome_catalog()['BackHomeFurniture']
    for room in state['rooms']:
        if room['id'] == 5 or not room['archives_id']:
            continue
        previous = room_level(room)
        aid = room['archives_id'][0]
        room['exp'] = sum(home_config('BackHomeFurniture', r['id'])['dorm_exp'] * r['num'] for r in room['give_furnitures'])
        room['exp'] += sum(c['dorm_exp'] * user.get('materials', {}).get(i, 0) for i, c in cfgs.items()
                           if c['is_give'] == 3 and c['hero_id'] == aid)
        award_room_levels(user, state['heroes'][str(aid)]['hero_id'], room, previous)


def enter_backhome(user: dict) -> dict:
    """首次进街按实际已解锁房间配置发放一次初始物品。"""
    state = backhome_state(user)
    if not state['starter_claimed']:
        for room in state['rooms']:
            grant_rewards(user, [{'id': i, 'num': n} for i, n in home_config('BackHomeCfg', room['id'])['default_item']])
        state['starter_claimed'] = True
    return state


def furniture_data(user: dict) -> list[dict]:
    """家具库存以共用物品背包为准，赠送数另存，兼容商店和GM发放。"""
    state = backhome_state(user)
    given = {}
    for room in state['rooms']:
        for row in room['give_furnitures']:
            key = str(row['id'])
            given[key] = given.get(key, 0) + row['num']
    materials = user.get('materials', {})
    return [{'furniture_id': int(i), 'num': materials.get(i, 0), 'give_num': given.get(i, 0)}
            for i in backhome_catalog()['BackHomeFurniture'] if materials.get(i, 0) or given.get(i, 0)]


def update_home_suits(user: dict) -> list[int]:
    """只有实际集齐套装家具才加入已解锁名单，不额外发放家具。"""
    state = backhome_state(user)
    for key, cfg in backhome_catalog()['BackHomeSuitCfg'].items():
        if (int(key) not in state['backhome_suit_id_list'] and cfg['suit_num']
                and all(user.get('materials', {}).get(str(i), 0) >= n for i, n in cfg['suit_num'])):
            state['backhome_suit_id_list'].append(int(key))
    return state['backhome_suit_id_list']


def ingredient_data(user: dict) -> list[dict]:
    """食材和常规奖励共享同一库存，避免重新进入饭堂时丢失。"""
    ids = {str(i) for c in backhome_catalog()['BackHomeCanteenFoodCfg'].values() for i, _ in c['ingredient_list']}
    return [{'id': int(i), 'num': user.get('materials', {}).get(i, 0), 'time_valid': 0} for i in sorted(ids)]


def backhome_simple(user: dict) -> dict:
    """58001包含可直接读取的首个食堂、角色和房间，不能发送空占位结构。"""
    state = backhome_state(user)
    update_home_suits(user)
    fields = ('canteen_furnitures', 'hero_setting', 'last_fatigue_update_time', 'theatrical_list',
              'daily_game_currency_num', 'exhibition_id', 'share_is_open', 'backhome_suit_id_list')
    data = {k: deepcopy(state[k]) for k in fields}
    data['canteens'] = [{k: deepcopy(v) for k, v in state['canteen'].items()
                         if k in ('id', 'entrust', 'signature_dish', 'careers', 'accruing_earnings')}]
    data['dorm'] = [{k: deepcopy(v) for k, v in room.items() if k not in ('layout', 'picture_link')} for room in state['rooms']]
    data['backhome_hero'] = deepcopy(list(state['heroes'].values()))
    data['ingredients'] = ingredient_data(user)
    data['furnitures'] = furniture_data(user)
    data['received_be_visited_gift_num'] = 0
    return data


def backhome_detail(user: dict) -> dict:
    """58003恢复完整房间、食堂、模板和食材，所有列表保持真实持有状态。"""
    state = backhome_state(user)
    return {'result': 0, 'canteens': [deepcopy(state['canteen'])], 'ingredients': ingredient_data(user),
            'food': deepcopy(list(state['food'].values())), 'dorms': [
                {k: deepcopy(v) for k, v in r.items() if k != 'picture_link'} for r in state['rooms']],
            'exhibition_id': state['exhibition_id'], 'template': deepcopy(state['template']),
            'furnitures': furniture_data(user)}


def tick_fatigue(user: dict) -> list:
    """按真实时长恢复心情并扣除上岗消耗；心情耗尽时换回对应NPC。"""
    state = backhome_state(user)
    now = int(time.time())
    elapsed = max(0, now - state['last_fatigue_update_time'])
    if elapsed < 3600:
        return []
    hours = elapsed // 3600
    settings = backhome_catalog()['settings']
    working = {archive_id(c['hero_id']) for c in state['canteen']['careers'] if c['hero_id'] and str(c['hero_id']) in backhome_catalog()['BackHomeHeroCfg']}
    busy = {archive_id(i) for e in state['canteen']['entrust'] for i in e['hero_list'] if e['start_time']}
    changed = []
    public = next(r for r in state['rooms'] if r['id'] == 5)['archives_id']
    for hero in state['heroes'].values():
        if hero['archives_id'] in working:
            hero['fatigue'] = max(0, hero['fatigue'] - hours * settings['dorm_canteen_work_fatigue'][0] // 100)
            if not hero['fatigue']:
                for career in state['canteen']['careers']:
                    if career['hero_id'] and archive_id(career['hero_id']) == hero['archives_id']:
                        career['hero_id'] = min((c['id'] for c in backhome_catalog()['BackHomeNpcCfg'].values() if c['npc_work'] == career['type']), default=0)
                        changed.append(career['hero_id'])
            continue
        if hero['archives_id'] in busy:
            continue
        rate = settings['dorm_lobby_fatigue_increase'][0] * (3600 // settings['dorm_fatigue_time'][0]) if hero['archives_id'] in public else settings['dorm_base_fatigue_increase'][0]
        private = next((r for r in state['rooms'] if r['id'] != 5 and hero['archives_id'] in r['archives_id']), None)
        if private and hero['archives_id'] not in public:
            rate = settings['dorm_fatigue_increase'][room_level(private) - 1][1] * (3600 // settings['dorm_fatigue_time'][0])
        remainders = state.setdefault('fatigue_remainder', {})
        key = str(hero['archives_id'])
        gain, remainders[key] = divmod(hours * rate + remainders.get(key, 0), 100)
        hero['fatigue'] = min(settings['canteen_hero_fatigue_max'][0], hero['fatigue'] + gain)
    state['last_fatigue_update_time'] += hours * 3600
    return changed
