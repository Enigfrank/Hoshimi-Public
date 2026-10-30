"""角色送礼、交心、关系网与礼物置换的实际成长存档。"""

from copy import deepcopy
import time

from battle_rewards import grant_rewards, spend_items
from hero_training_data import (exp_level, item_cost, owned_hero, total_skill_level,
                                training_catalog, training_condition)
from server_clock import business_day


def archive_data(user: dict) -> list[dict]:
    """按拥有角色建立共享档案，恢复好感经验和已送礼物，旧满配号保持满好感。"""
    heroes = {h['id']: h for h in user.get('heroes', []) if h.get('unlock', 1)}
    result = []
    catalog = training_catalog()
    maximum = love_max_exp()
    for key, cfg in catalog['HeroRecordCfg'].items():
        owned = [heroes[i] for i in cfg['hero_id'] if i in heroes]
        if not owned:
            continue
        row = {'archive_id': int(key), 'exp': maximum if any(h.get('trust_level') == 5 for h in owned) else 0,
               'gift_list': [], 'text_list': [], 'video_list': [],
               'hero_story_list': [], 'super_heart_link_list': [], 'selected_picture': {'type': 0, 'id': 0}}
        row.update(deepcopy(user.get('archives', {}).get(key, {})))
        result.append(row)
    return result


def love_max_exp() -> int:
    """累计客户端好感等级表，交心解锁必须先达到好感上限。"""
    catalog = training_catalog()
    return sum(catalog['GameLevelSetting'][str(i)]['hero_love_exp']
               for i in range(1, catalog['settings']['love_level_max'] + 1))


def exchange_data(user: dict) -> dict:
    """按业务日恢复礼物置换次数，查询本身不改写账号。"""
    state = user.get('trust_exchange', {})
    counts = state.get('counts', {}) if state.get('day') == business_day(int(time.time())) else {}
    return {'limit_list': [{'item_id': int(i), 'use': n} for i, n in counts.items()]}


def send_gift(user: dict, request: dict) -> dict:
    """普通礼物增加共享档案好感与送礼记录，不接受任意物品。"""
    archive = next((r for r in archive_data(user) if r['archive_id'] == request['archive_id']), None)
    rows = request['gift_list']
    if archive is None or not rows:
        raise ValueError('档案未拥有或礼物为空')
    catalog = training_catalog()
    liked = catalog['HeroRecordCfg'][str(request['archive_id'])]['gift_like_id1']
    gain = 0
    gifts = {str(r['id']): r['num'] for r in archive['gift_list']}
    for row in rows:
        cfg = catalog['items'].get(str(row['id']), {})
        if cfg.get('type') != 10 or cfg.get('sub_type') != 1 or row['num'] <= 0:
            raise ValueError('物品不是好感礼物或数量无效')
        gain += catalog['settings']['gift_value'][0 if row['id'] in liked else 1] * row['num']
        gifts[str(row['id'])] = gifts.get(str(row['id']), 0) + row['num']
    if archive['exp'] >= love_max_exp():
        raise ValueError('角色好感已满')
    spend_items(user, rows)
    archive['exp'] = min(love_max_exp(), archive['exp'] + gain)
    archive['gift_list'] = [{'id': int(i), 'num': n} for i, n in gifts.items()]
    user.setdefault('archives', {})[str(request['archive_id'])] = archive
    return {'result': 0}


def exchange_gifts(user: dict, request: dict) -> dict:
    """按配置兑换比例和每日限额置换礼物，奖励总值不得超过消耗。"""
    catalog = training_catalog()
    costs, rewards = request['use_item_list'], request['reward_item_list']
    if not costs or not rewards:
        raise ValueError('置换消耗或奖励为空')
    available = 0
    for row in costs:
        cfg = catalog['items'].get(str(row['id']), {})
        if cfg.get('type') != 10 or cfg.get('sub_type') != 1 or row['num'] <= 0:
            raise ValueError('置换只能消耗普通礼物')
        available += row['num']
    required = 0
    counts = {str(r['item_id']): r['use'] for r in exchange_data(user)['limit_list']}
    rates = dict(catalog['settings']['hero_trust_exchange_need'])
    limits = dict(catalog['settings']['hero_trust_exchange_up_limit'])
    for row in rewards:
        item, count = row['id'], row['num']
        if item not in rates or count <= 0:
            raise ValueError('置换目标或数量无效')
        counts[str(item)] = counts.get(str(item), 0) + count
        if counts[str(item)] > limits[item]:
            raise ValueError('礼物置换超过每日上限')
        required += rates[item] * count
    if required != available:
        raise ValueError('礼物置换数量与配置比例不符')
    spend_items(user, costs)
    grant_rewards(user, rewards)
    user['trust_exchange'] = {'day': business_day(int(time.time())), 'counts': counts}
    return {'result': 0}


def relation_value(user: dict, hero: dict, kind: int) -> int:
    """计算关系网条件使用的等级、技能总级、权钥、星级、熟练度和跃迁点数。"""
    if kind == 1:
        return hero['level']
    if kind == 2:
        skills = training_catalog()['heroes'][str(hero['id'])]['skills']
        return sum(1 if i % 1000 == 305 else total_skill_level(hero, i) for i in skills)
    if kind == 3:
        return exp_level(hero.get('weapon_exp', 0), 60, True)
    if kind == 4:
        return hero['star']
    if kind == 5:
        return hero.get('clear_times', 0)
    if kind == 6:
        from hero_talents import transition_slots
        return sum(s['talent_points'] for s in transition_slots(user, hero['id']))
    return 0


def affinity_request(user: dict, command: int, request: dict) -> dict:
    """按等级、专属礼物和心情结算交心；仅首次解锁校验档案好感。"""
    if command == 14100:
        return send_gift(user, request)
    if command == 73010:
        return exchange_gifts(user, request)
    catalog = training_catalog()
    if command == 73016:
        cfg = catalog['HeroRelationNetCfg'].get(str(request['id']), {})
        if not cfg:
            raise ValueError('关系网配置不存在')
        hero = owned_hero(user, cfg['hero_id'])
        index = request['group_index']
        if not 1 <= index <= len(cfg['relation_upgrade_group']) or hero.get('trust_level', 1) < cfg['index']:
            raise ValueError('关系网层级或组号无效')
        condition = catalog['HeroRelationUpgradeCfg'][str(cfg['relation_upgrade_group'][index - 1])]['condition_id']
        condition = catalog['HeroRelationUpgradeConditionCfg'][str(condition)]
        candidates = [h for h in user['heroes'] if h['id'] in cfg['intimate'] and h.get('unlock', 1)]
        if not any(relation_value(user, h, condition['type']) >= condition['arg'][0] for h in candidates):
            raise ValueError('关系网解锁条件未满足')
        from hero_preferences import hero_info_data
        info = next(h for h in hero_info_data(user)['hero_info_list'] if h['hero_base_info']['id'] == hero['id'])
        relation = deepcopy(info['trust']['relation'])
        tier = next((t for t in relation['tier_list'] if t['tier'] == cfg['index']), None)
        if tier is None:
            tier = {'tier': cfg['index'], 'upgrade_complete_list': []}
            relation['tier_list'].append(tier)
        if index in tier['upgrade_complete_list']:
            raise ValueError('关系网节点已解锁')
        tier['upgrade_complete_list'].append(index)
        hero['relation'] = relation
        return {'result': 0}
    if command == 73022:
        cfg = catalog['HeroRelationCfg'].get(str(request['id']), {})
        if not cfg or not cfg['story_id']:
            raise ValueError('关系故事不存在')
        hero = owned_hero(user, cfg['main_hero_id'])
        tiers = [c['index'] for c in catalog['HeroRelationNetCfg'].values()
                 if c['hero_id'] == hero['id'] and cfg['relate_hero_id'] in c['intimate']]
        if (not tiers or hero.get('trust_level', 1) < min(tiers)
                or not training_condition(user, hero, cfg['story_condtion_id'])):
            raise ValueError('关系故事尚未解锁')
        claimed = user.setdefault('relation_story_rewards', [])
        if request['id'] in claimed:
            raise ValueError('关系故事奖励已领取')
        rewards = item_cost(cfg['story_reward'])
        grant_rewards(user, rewards)
        claimed.append(request['id'])
        return {'result': 0, 'reward_list': rewards}
    hero = owned_hero(user, request['hero_id'])
    level = hero.get('trust_level', 1)
    if command == 73012:
        cfg = catalog['HeroTrustLevelCfg'].get(str(level))
        if level < 1 or level >= 5 or not cfg or not request['item_list']:
            raise ValueError('交心已满级或礼物为空')
        liked = next((c['gift_like_id1'] for c in catalog['HeroRecordCfg'].values()
                      if hero['id'] in c['hero_id']), [])
        mood = catalog['HeroTrustMoodCfg'].get(str(hero.get('trust_mood', 1)),
                                               catalog['HeroTrustMoodCfg']['1'])
        gain = 0
        for row in request['item_list']:
            item = catalog['items'].get(str(row['id']), {})
            if row['num'] <= 0 or item.get('type') != 10:
                raise ValueError('交心礼物类型或数量无效')
            if row['id'] in liked and item.get('sub_type') == 1:
                exp = catalog['settings']['hero_trust_favor_gift_add_exp'][0]
            elif row['id'] in cfg['gift_list'] and item.get('sub_type') == 2:
                exp = item['param'][0]
            else:
                raise ValueError('礼物不适用于当前交心等级')
            gain += exp * mood['trust_exp_rate'] // 1000 * row['num']
        current = hero.get('trust_exp', 0)
        if current >= cfg['exp']:
            raise ValueError('交心经验已满，请先提升等级')
        spend_items(user, request['item_list'])
        hero['trust_exp'] = min(cfg['exp'], current + gain)
        return {'result': 0}
    if command == 73020:
        archive = next((r for r in archive_data(user)
                        if hero['id'] in catalog['HeroRecordCfg'][str(r['archive_id'])]['hero_id']), None)
        if archive is None or archive['exp'] < love_max_exp():
            raise ValueError('角色好感未满')
        if level != 0:
            raise ValueError('交心已经解锁')
        hero['trust_level'], hero['trust_exp'], hero['trust_mood'] = 1, 0, 1
        return {'result': 0, 'mood': 1}
    cfg = next((c for c in catalog['HeroTrustCfg'].values()
                if c['hero_id'] == hero['id'] and c['trust_level'] == level), None)
    if (level < 1 or level >= 5 or not cfg or hero.get('trust_exp', 0) < catalog['HeroTrustLevelCfg'][str(level)]['exp']
            or not all(training_condition(user, hero, c) for c in cfg['condition_list'])):
        raise ValueError('交心升级条件未满足')
    rewards = item_cost(cfg['reward_item_list'])
    grant_rewards(user, rewards)
    hero['trust_level'], hero['trust_exp'] = level + 1, 0
    return {'result': 0, 'reward_list': rewards}
