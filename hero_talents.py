"""技能、神格、跃迁与权钥同调的培养和配置校验。"""

from copy import deepcopy

from battle_rewards import grant_rewards, spend_items
from hero_training_data import item_cost, skill_level, training_catalog, training_condition


def train_skill(user: dict, hero: dict, command: int, request: dict) -> dict:
    """分别保存单个技能与强化属性等级，按每一级配置合并扣款。"""
    catalog = training_catalog()
    skills = catalog['heroes'][str(hero['id'])]['skills']
    count = request['num']
    if command == 14030:
        skill = request['skill_id']
        if skill not in skills[:5]:
            raise ValueError('技能不属于角色或不可升级')
        index = skills.index(skill) + 1
        current = skill_level(hero, skill)
        if count <= 0 or current + count > catalog['settings']['hero_skill_level_max'][0]:
            raise ValueError('技能升级次数无效或超出上限')
        costs = []
        for level in range(current, current + count):
            cfg = catalog['SkillCfg'][str(level)]
            if hero['break_level'] < cfg['skill_limit' + str(index)]:
                raise ValueError('角色突破等级不足以升级技能')
            costs.extend(item_cost(cfg['skill_cost' + str(index)]))
        spend_items(user, costs)
        hero.setdefault('skill_levels', {})[str(skill)] = current + count
    else:
        index = request['index']
        if not 1 <= index <= 5 or skill_level(hero, skills[index - 1]) < catalog['settings']['hero_skill_level_max'][0]:
            raise ValueError('技能未满级或强化索引无效')
        current = hero.get('skill_attr_levels', {}).get(str(index), hero.get('skill_attr_level', 0))
        if count <= 0 or current + count > catalog['settings']['hero_skill_attr_level_max'][0]:
            raise ValueError('技能属性强化次数无效或超出上限')
        costs = []
        for level in range(current + 1, current + count + 1):
            cfg = catalog['HeroSkillElementCfg'].get(str(hero['id'] * 100 + level), {})
            rows = cfg.get('skill_element_cost' + str(index), [])
            if not rows:
                raise ValueError('该技能没有可强化的属性')
            costs.extend(item_cost(rows))
        spend_items(user, costs)
        hero.setdefault('skill_attr_levels', {})[str(index)] = current + count
    return {'result': 0}


def unlock_astrolabe(user: dict, hero: dict, request: dict) -> dict:
    """解锁属于本角色的神格节点，并校验前置节点和配置条件。"""
    catalog = training_catalog()
    node = request['astrolabe_id']
    cfg = catalog['HeroAstrolabeCfg'].get(str(node), {})
    if not cfg or cfg['hero_astrolabe_suit_id'] not in catalog['heroes'][str(hero['id'])]['astrolabe']:
        raise ValueError('神格不属于该角色')
    unlocked = hero.setdefault('astrolabes', [])
    if node in unlocked:
        raise ValueError('神格已解锁')
    if cfg['pre_astrolabe_id'] and cfg['pre_astrolabe_id'] not in unlocked:
        raise ValueError('前置神格尚未解锁')
    if not training_condition(user, hero, cfg['unlock_condition']):
        raise ValueError('神格解锁条件未满足')
    spend_items(user, item_cost(cfg['unlock']))
    unlocked.append(node)
    return {'result': 0, 'hero_id': hero['id'], 'unlock_id': node}


def transition_slots(user: dict, hero_id: int) -> list[dict]:
    """为普通角色补齐六个零点数槽位，并保留已有满配及手选方案。"""
    from hero_preferences import hero_info_data
    info = next(h for h in hero_info_data(user)['hero_info_list'] if h['hero_base_info']['id'] == hero_id)
    slots = deepcopy(info['hero_base_info']['exclusive_skill_list'])
    for position in range(1, 7):
        if not any(s['slot_id'] == position for s in slots):
            slots.append({'slot_id': position, 'talent_points': 0, 'skill_list': []})
    return slots


def train_transition(user: dict, hero: dict, command: int, request: dict) -> dict:
    """按已购买的槽位点数保存跃迁，拒绝越过点数或角色可选技能池。"""
    settings = training_catalog()['settings']
    position = request['slot_id']
    if not 1 <= position <= 6:
        raise ValueError('跃迁槽位无效')
    slots = transition_slots(user, hero['id'])
    slot = next(s for s in slots if s['slot_id'] == position)
    if command == 14112:
        count = request['lv_up_num']
        current = slot['talent_points']
        if count <= 0 or current + count > settings['exclusive_skill_talent_num_max'][0]:
            raise ValueError('跃迁升级次数无效或超过上限')
        costs = []
        for level in range(current, current + count):
            costs.extend(item_cost(settings['exclusive_skill_talent_strengthen_cost'][level]))
        spend_items(user, costs)
        slot['talent_points'] += count
    else:
        skills = request['skill_list']
        allowed = training_catalog()['heroes'][str(hero['id'])]['hero_equip_skill'][position - 1]
        if (len(skills) > settings['exclusive_skill_use_num_max'][0]
                or len({s['skill_id'] for s in skills}) != len(skills)
                or sum(s['skill_level'] for s in skills) > slot['talent_points']):
            raise ValueError('跃迁技能超出点数或数量限制')
        if any(s['skill_id'] not in allowed or not 1 <= s['skill_level'] <= 3 for s in skills):
            raise ValueError('跃迁技能与角色或槽位不符')
        slot['skill_list'] = deepcopy(skills)
    user.setdefault('hero_preferences', {}).setdefault(str(hero['id']), {})['exclusive_skill_list'] = slots
    return {'result': 0}


def train_module(user: dict, hero: dict) -> dict:
    """根据同调阶段的全部条件和材料成本推进一次。"""
    cfg = training_catalog()['WeaponModuleCfg'].get(str(hero['id']), {})
    current = hero.get('module_level', 0)
    if not cfg or current >= len(cfg['cost']):
        raise ValueError('角色没有可升级的同调阶段')
    if not current and not hero.get('module_assignment'):
        raise ValueError('同调战斗任务尚未完成')
    if not all(training_condition(user, hero, c) for c in cfg['condition'][current]):
        raise ValueError('同调解锁条件未满足')
    spend_items(user, item_cost(cfg['cost'][current]))
    hero['module_level'] = current + 1
    return {'result': 0}


def resolve_module_items(user: dict, request: dict) -> dict:
    """只分解配置允许的同调材料，返还按物品分解表计算。"""
    rows = request['item_list']
    if not rows or any(r['num'] <= 0 for r in rows):
        raise ValueError('同调材料分解数量无效')
    rewards = []
    for row in rows:
        cfg = training_catalog()['items'].get(str(row['id']), {})
        if cfg.get('sub_type') != 617:
            raise ValueError('物品不是可分解的同调材料')
        item, count = training_catalog()['settings']['weapon_module_break_return']
        rewards.append({'id': item, 'num': count * row['num']})
    spend_items(user, rows)
    grant_rewards(user, rewards)
    return {'result': 0}
