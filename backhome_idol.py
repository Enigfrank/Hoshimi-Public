"""偶像训练、PVE与本机玩家挑战、回放和任务等级奖励。"""

from copy import deepcopy
import time

from backhome_catalog import archive_id, backhome_catalog, home_config, home_hero
from backhome_data import backhome_state
from backhome_idol_battle import generate_idol_battle, idol_actor
from battle_rewards import grant_rewards, spend_items
from hero_training_data import item_cost, training_catalog
from local_social import local_user, local_users, public_profile

IDOL_COMMANDS = {58160, 58164, 58166, 58174, 58176, 58178, 58180, 58182, 58184, 58186, 58192}


def idol_info(user: dict) -> dict:
    """恢复真实偶像属性、攻守选择、训练次数和已实际通关关卡。"""
    state = backhome_state(user)['idol']
    stage = min(int(i) for i in backhome_catalog()['IdolTraineePvpBattleCfg'])
    return {'attack_hero_id_list': deepcopy(state['attack_hero_id_list']),
            'defend_hero_id_list': deepcopy(state['defend_hero_id_list']),
            'hero_list': deepcopy(list(state['heroes'].values())),
            'exercise_times_info': {'use_times': state['use_times'], 'camp_list': list(state['camp_list'])},
            'pvp_stage_info': {'stage_id': stage, 'refresh_timestamp': 0}, 'hero_pos_list': deepcopy(state['hero_pos_list'])}


def idol_pve_data(user: dict) -> dict:
    """未通关关卡不生成成功占位记录，最高分与实际胜利分别保存。"""
    state = backhome_state(user)['idol']
    return {'chapter_list': [{'chapter_id': int(i), 'stage_list': [deepcopy(state['pve'][str(s)])
            for s in cfg['section_id_list'] if str(s) in state['pve']]}
            for i, cfg in backhome_catalog()['IdolTraineeChapterCfg'].items()]}


def idol_hero(user: dict, hero_id: int) -> dict:
    """偶像属性只允许真实拥有、可参加偶像竞技的角色。"""
    home_hero(user, hero_id)
    cfg = home_config('BackHomeHeroCfg', hero_id)
    if not cfg['idol_usable']:
        raise ValueError('该角色尚不可参加偶像竞技')
    return user['backhome']['idol']['heroes'][str(hero_id)]


def idol_skin(user: dict, hero_id: int) -> int:
    """回放使用玩家实际保存的游园街皮肤模型。"""
    return next((r['skin_id'] for r in backhome_state(user)['hero_setting'] if r['hero_id'] == hero_id), hero_id)


def validate_idol_team(user: dict, ids: list) -> None:
    """攻守名单不可为空、重复或使用同档案多形态。"""
    if not ids or len(ids) > 1 or len(set(archive_id(i) for i in ids)) != len(ids):
        raise ValueError('偶像对战需要选择一名角色')
    for hero in ids:
        idol_hero(user, hero)


def train_idol(user: dict, request: dict) -> dict:
    """按心情、每日次数、神系加成和配置上限训练单项属性。"""
    state = backhome_state(user)
    hero = home_hero(user, request['hero_id'])
    data = idol_hero(user, request['hero_id'])
    index = request['attribute_index']
    settings = backhome_catalog()['settings']
    rooms = sum(r['id'] != 5 for r in state['rooms'])
    maximum = max(n for count, n in settings['dorm_idol_hero_exercise_times_limit'] if count <= rooms)
    cost = settings['dorm_idol_hero_exercise_cost'][0]
    if not 1 <= index <= 5 or state['idol']['use_times'] >= maximum or hero['fatigue'] < cost:
        raise ValueError('训练属性、每日次数或角色心情不足')
    cfg = home_config('BackHomeHeroCfg', request['hero_id'])
    current = data['attribute_list'][index - 1]
    if current >= cfg['idol_base_attribute'][index - 1][1]:
        raise ValueError('偶像属性已达到上限')
    base = max(n for ratio, n in settings['dorm_idol_hero_exercise_default_value'] if current * 100 // cfg['idol_base_attribute'][index - 1][1] >= ratio)
    fatigue_bonus = max(n for need, n in settings['dorm_idol_hero_exercise_fatigue_addition'] if hero['fatigue'] >= need)
    race = training_catalog()['heroes'][str(request['hero_id'])]['race']
    camp_bonus = settings['dorm_idol_hero_exercise_camp_addition'][0] if race in state['idol']['camp_list'] else 0
    amount = max(1, base * (100 + fatigue_bonus + camp_bonus) // 100)
    data['attribute_list'][index - 1] = min(cfg['idol_base_attribute'][index - 1][1], current + amount)
    hero['fatigue'] -= cost
    state['idol']['use_times'] += 1
    state['idol']['train_times'] = state['idol'].get('train_times', 0) + 1
    counts = state['idol'].setdefault('hero_trains', {})
    key = str(request['hero_id'])
    counts[key] = counts.get(key, 0) + 1
    return {'result': 0, 'fatigue': hero['fatigue'], 'attribute_value': data['attribute_list'][index - 1]}


def begin_idol_battle(user: dict, command: int, request: dict) -> dict:
    """创建不可由客户端修改的完整回放快照，断线重试复用尚未结算的对局。"""
    state = backhome_state(user)['idol']
    kind = 3 if command == 58186 else request['battle_type']
    if kind not in (1, 2, 3):
        raise ValueError('偶像对战类型无效')
    stage = request['stage_id']
    cfg = home_config('IdolTraineePveBattleCfg' if kind == 3 else 'IdolTraineePvpBattleCfg', stage)
    target_uid = request.get('defender_uid', 0)
    old = state.get('pending')
    if old and not old.get('settled'):
        if old['stage_id'] != stage or old['kind'] != kind or old['defender_uid'] != target_uid:
            raise ValueError('已有未结算的偶像对战')
        return {'result': 0, **deepcopy(old['start'])}
    validate_idol_team(user, state['attack_hero_id_list'])
    attacker_id = state['attack_hero_id_list'][0]
    attack = idol_actor(attacker_id, idol_hero(user, attacker_id)['attribute_list'], False)
    if kind == 3:
        chapter = next(c for c in backhome_catalog()['IdolTraineeChapterCfg'].values() if stage in c['section_id_list'])
        offset = chapter['section_id_list'].index(stage)
        if offset and not state['pve'].get(str(chapter['section_id_list'][offset - 1]), {}).get('is_clear'):
            raise ValueError('偶像竞技前置关卡尚未通关')
        npc = home_config('IdolPveNpcCfg', cfg['npc_id'])
        defender_id, defender_skin = npc['id'], npc['id']
        defend = idol_actor(defender_id, npc['attribute'], True)
        profile = {'nick': npc['name'], 'icon': 2110841, 'icon_frame': 2001}
    else:
        target = local_user(user, target_uid)
        if target_uid == user['uid'] or kind == 1 and target_uid not in user.get('friends', []):
            raise ValueError('对战目标不是合法本机对手')
        other = backhome_state(target)['idol']
        validate_idol_team(target, other['defend_hero_id_list'])
        defender_id = other['defend_hero_id_list'][0]
        defender_skin = idol_skin(target, defender_id)
        defend = idol_actor(defender_id, idol_hero(target, defender_id)['attribute_list'], False)
        profile = public_profile(target)
    serial = state.get('next_battle', 1)
    state['next_battle'] = serial + 1
    battle_id = user['uid'] * 1000000 + serial
    music = home_config('IdolStageMusicCfg', cfg.get('bgm', 1002))
    replay = generate_idol_battle(attack, defend, cfg, battle_id, music['round'], backhome_catalog()['settings']['dorm_idol_attribute_score_coefficient'][0])
    start = {'prepare_info': replay['prepare_info'], 'round_list': replay['round_list'],
             'attacker_skin_id': idol_skin(user, attacker_id), 'defender_skin_id': defender_skin}
    state['pending'] = {'battle_id': battle_id, 'kind': kind, 'stage_id': stage, 'defender_uid': target_uid,
                        'attacker_id': attacker_id, 'profile': profile, 'start': deepcopy(start),
                        'outcome': {k: v for k, v in replay.items() if k not in ('round_list', 'prepare_info')},
                        'created': int(time.time()), 'settled': False}
    return {'result': 0, **start}


def finish_idol_battle(user: dict, request: dict) -> dict:
    """结算只使用已保存战报，重复结算复用结果，不再次加任务进度或领奖货币。"""
    state = backhome_state(user)['idol']
    pending = state.get('pending')
    if not pending or pending['kind'] != request['battle_type']:
        raise ValueError('不存在对应的偶像对战')
    events = request.get('acc_event', {})
    if any(n > 1000 for n in events.values()):
        raise ValueError('偶像对战互动计数无效')
    if pending['settled']:
        return {'result': 0, **deepcopy(pending['outcome'])}
    score = pending['outcome']['attacker_data']['sum_score']
    if pending['kind'] == 3:
        record = state['pve'].setdefault(str(pending['stage_id']), {'stage_id': pending['stage_id'], 'score': 0, 'is_clear': False})
        record['score'] = max(record['score'], score)
        record['is_clear'] = record['is_clear'] or pending['outcome']['battle_result'] == 1
        from gameplay_data import client_catalog
        for cfg in client_catalog()['tasks']:
            if cfg['type'] == 903 and cfg['condition'] == 2012 and cfg['additional_parameter'][0] == pending['stage_id'] and score >= cfg['additional_parameter'][1]:
                entry = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
                entry['progress'] = cfg['need']
    else:
        state['friend_battles'] = state.get('friend_battles', 0) + int(pending['kind'] == 1)
    counts = state.setdefault('hero_battles', {})
    key = str(pending['attacker_id'])
    counts[key] = counts.get(key, 0) + 1
    state['history'] = [{'battle_id': pending['battle_id'], 'battle_type': pending['kind'],
        'timestamp': pending['created'], 'is_attacker': True, 'battle_result': pending['outcome']['battle_result'],
        'another_user_id': pending['defender_uid'], 'another_profile_base': pending['profile']}] + state['history']
    maximum = backhome_catalog()['settings']['dorm_idol_max_history_battle_list_num'][0]
    state['history'] = state['history'][:maximum]
    state.setdefault('replays', {})[str(pending['battle_id'])] = deepcopy(pending)
    state['replays'] = {str(r['battle_id']): state['replays'][str(r['battle_id'])] for r in state['history']}
    pending['settled'] = True
    return {'result': 0, **deepcopy(pending['outcome'])}


def rank_reward(user: dict, request: dict) -> dict:
    """偶像阶段奖励按实际配置付费、自选数量、可选道具和领取事实校验。"""
    state = backhome_state(user)['idol']
    cfg = home_config('IdolTraineeRewardRankCfg', request['id'])
    if cfg['id'] in state['get_id_list']:
        raise ValueError('偶像阶段奖励已经领取')
    if cfg['id'] > 1 and cfg['id'] - 1 not in state['get_id_list']:
        raise ValueError('偶像阶段前置奖励尚未领取')
    rows = request['select_list']
    if not rows or any(r['id'] not in cfg['reward'] or r['num'] <= 0 for r in rows) or sum(r['num'] for r in rows) != cfg['num']:
        raise ValueError('偶像自选奖励或数量无效')
    spend_items(user, item_cost([cfg['item_id']]))
    grant_rewards(user, rows)
    state['get_id_list'].append(cfg['id'])
    return {'result': 0, 'get_id_list': list(state['get_id_list']), 'reward_list': deepcopy(rows)}


def idol_history(user: dict) -> list:
    """汇总自己发起及本机其他玩家已确认的挑战，不跨账号写入对方历史。"""
    rows = deepcopy(backhome_state(user)['idol']['history'])
    for other in local_users(user):
        if other['uid'] == user['uid']:
            continue
        for row in other.get('backhome', {}).get('idol', {}).get('history', []):
            if row['another_user_id'] == user['uid'] and row['battle_type'] in (1, 2):
                copied = deepcopy(row)
                copied['is_attacker'] = False
                copied['another_user_id'] = other['uid']
                copied['another_profile_base'] = public_profile(other)
                copied['battle_result'] = 2 if row['battle_result'] == 1 else 1
                rows.append(copied)
    maximum = backhome_catalog()['settings']['dorm_idol_max_history_battle_list_num'][0]
    return sorted(rows, key=lambda r: (r['timestamp'], r['battle_id']), reverse=True)[:maximum]


def idol_replay(user: dict, battle_id: int) -> dict:
    """被挑战玩家以自己的视角读取回放，交换双方动作、分数和皮肤后返回。"""
    own = backhome_state(user)['idol'].get('replays', {}).get(str(battle_id))
    if own:
        return deepcopy(own)
    for other in local_users(user):
        state = other.get('backhome', {}).get('idol', {})
        replay = state.get('replays', {}).get(str(battle_id))
        if (not replay or replay['defender_uid'] != user['uid'] or replay['kind'] not in (1, 2)
                or not any(r['battle_id'] == battle_id for r in state.get('history', []))):
            continue
        copied = deepcopy(replay)
        start, outcome = copied['start'], copied['outcome']
        start['attacker_skin_id'], start['defender_skin_id'] = start['defender_skin_id'], start['attacker_skin_id']
        info = start['prepare_info']
        info['attacker_active_buff_list'], info['defender_active_buff_list'] = info['defender_active_buff_list'], info['attacker_active_buff_list']
        for row in start['round_list']:
            row['attack_user_action'], row['defend_user_action'] = row['defend_user_action'], row['attack_user_action']
            row['attack_user_score'], row['defend_user_score'] = row['defend_user_score'], row['attack_user_score']
        outcome['attacker_data'], outcome['defender_data'] = outcome['defender_data'], outcome['attacker_data']
        outcome['battle_result'] = 2 if outcome['battle_result'] == 1 else 1
        return copied
    raise ValueError('偶像对战回放不存在')


def idol_request(user: dict, command: int, request: dict) -> dict:
    """处理偶像竞技全部本机协议，不生成虚构对手或空成功战报。"""
    state = backhome_state(user)['idol']
    if command == 58166:
        return train_idol(user, request)
    if command in (58160, 58186):
        return begin_idol_battle(user, command, request)
    if command == 58164:
        return finish_idol_battle(user, request)
    if command == 58192:
        return rank_reward(user, request)
    if command in (58176, 58178):
        field = 'attack_hero_id_list' if command == 58176 else 'defend_hero_id_list'
        validate_idol_team(user, request[field])
        state[field] = list(request[field])
    elif command == 58174:
        rows = request['hero_pos_list']
        if (len(rows) > backhome_catalog()['settings']['dorm_idol_room_max_place'][0]
                or len({r['pos'] for r in rows}) != len(rows)
                or len({archive_id(r['hero_id']) for r in rows}) != len(rows)):
            raise ValueError('训练室位置或共享档案重复')
        for row in rows:
            idol_hero(user, row['hero_id'])
            if not 1 <= row['pos'] <= backhome_catalog()['settings']['dorm_idol_room_max_place'][0]:
                raise ValueError('训练室位置无效')
        state['hero_pos_list'] = deepcopy(rows)
    elif command == 58180:
        if request['battle_type'] not in (1, 2):
            raise ValueError('偶像对手列表类型无效')
        rows = []
        for other in local_users(user):
            if other['uid'] == user['uid'] or not other.get('backhome', {}).get('starter_claimed'):
                continue
            if request['battle_type'] == 1 and other['uid'] not in user.get('friends', []):
                continue
            data = backhome_state(other)['idol']
            if data['defend_hero_id_list']:
                hero_id = data['defend_hero_id_list'][0]
                rows.append({'user_id': other['uid'], 'profile_base': public_profile(other), 'hero_list': [
                    {'skin_id': idol_skin(other, hero_id), 'attribute_list': idol_hero(other, hero_id)['attribute_list']}]})
        return {'result': 0, 'pvp_fight_list': rows[:20]}
    elif command == 58182:
        return {'result': 0, 'history_mini_battle_list': idol_history(user)}
    elif command == 58184:
        replay = idol_replay(user, request['battle_id'])
        return {'result': 0, **deepcopy(replay['start']), **deepcopy(replay['outcome']), 'stage_id': replay['stage_id']}
    else:
        raise ValueError('偶像竞技指令未支持')
    return {'result': 0}
