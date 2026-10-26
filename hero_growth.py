"""修正者等级、突破以及权钥强化的一次性成本结算。"""

from battle_rewards import spend_items
from hero_training_data import (exp_at, exp_level, hero_break_config, item_cost,
                                material_exp, return_exp, training_catalog)
from loadout_storage import servant_data
from servant_cultivation import consume_servants, servant_config


def grow_hero(user: dict, hero: dict, command: int, request: dict) -> dict:
    """验证连续突破和目标等级，保存累计经验并返还溢出材料。"""
    current = hero['break_level']
    cfg = hero_break_config(hero, current)
    old_exp = hero.get('exp', exp_at(hero['level']))
    costs = []
    if command == 14036:
        hero_break_config(hero, current + 1)
        if hero['level'] < cfg['max_level'] or user['level'] < cfg['user_level']:
            raise ValueError('角色或管理员等级不足以突破')
        costs = item_cost(cfg['cost']) + item_cost([[2, cfg['cost_gold']]])
        spend_items(user, costs)
        hero['break_level'] += 1
        return {'result': 0}
    rows = request['item_list']
    gain = material_exp(rows, 601)
    target = cfg['max_level']
    if command == 14120:
        stages = request['break_list']
        if stages != list(range(current, current + len(stages))):
            raise ValueError('一键突破阶段必须连续且不可重复')
        target = request['level']
        for stage in stages:
            step = hero_break_config(hero, stage)
            hero_break_config(hero, stage + 1)
            if user['level'] < step['user_level'] or target <= step['max_level']:
                raise ValueError('一键突破等级条件不满足')
            costs.extend(item_cost(step['cost']) + item_cost([[2, step['cost_gold']]]))
        current += len(stages)
        maximum = hero_break_config(hero, current)['max_level']
        if not hero['level'] < target <= maximum:
            raise ValueError('角色目标等级无效')
    if hero['level'] >= target or gain <= 0:
        raise ValueError('角色已满级或未选择经验材料')
    needed = exp_at(target) - old_exp
    if command == 14120 and gain < needed:
        raise ValueError('经验不足以达到目标等级')
    applied = min(gain, needed)
    spend_items(user, costs + rows)
    hero['exp'] = old_exp + applied
    hero['level'] = exp_level(hero['exp'], target)
    hero['break_level'] = current
    returned = return_exp(user, gain - applied, 601)
    return {'result': 0, 'item_list': returned} if command == 14120 else {'result': 0}


def grow_weapon(user: dict, hero: dict, command: int, request: dict) -> dict:
    """按权钥上限、材料和未锁定钥从结算强化、突破与一键升级。"""
    settings = training_catalog()['settings']
    current = hero.get('weapon_break', 0)
    limits = settings['weapon_exp_limit']
    old_exp = hero.get('weapon_exp', 0)
    steps = request.get('breakthrough_times', 0) if command == 46034 else int(command == 46018)
    if steps < 0 or current < 0 or current + steps >= len(limits):
        raise ValueError('权钥突破次数无效或已达上限')
    costs = []
    for stage in range(current, current + steps):
        if user['level'] < settings['weapon_user_limit'][stage]:
            raise ValueError('管理员等级不足以突破权钥')
        cost_id = settings['weapon_break_cost'][stage]
        costs.extend(item_cost(training_catalog()['EquipMaterialCfg'][str(cost_id)]['item_list']))
    if command == 46018:
        if old_exp < exp_at(limits[current], True):
            raise ValueError('权钥尚未达到突破等级')
        spend_items(user, costs)
        hero['weapon_break'] = current + 1
        return {'result': 0}
    target = request['target_level'] if command == 46034 else limits[current]
    if not exp_level(old_exp, 60, True) < target <= limits[current + steps]:
        raise ValueError('权钥目标等级无效')
    if steps and target <= limits[current + steps - 1]:
        raise ValueError('权钥目标等级与突破次数不符')
    rows = request['material_list']
    gain = material_exp(rows, 602)
    inventory = servant_data(user)['servant_list']
    consumed = consume_servants(user, inventory, request['servant_list']) if request['servant_list'] else []
    for servant in consumed:
        cfg = servant_config(servant['id'])
        gain += settings['base_exp_weapon_servant'][cfg['starlevel'] - 1]
    needed = exp_at(target, True) - old_exp
    if gain <= 0 or (command == 46034 and gain < needed):
        raise ValueError('权钥强化经验不足')
    applied = min(gain, needed)
    costs.extend(rows)
    costs.extend(item_cost([[2, int(applied * settings['weapon_strengthen_gold_cost'][0])]]))
    spend_items(user, costs)
    hero['weapon_exp'] = old_exp + applied
    hero['weapon_break'] = current + steps
    user['servants'] = [s for s in inventory if s['uid'] not in request['servant_list']]
    returned = return_exp(user, gain - applied, 602)
    return {'result': 0, 'mat_list': returned} if command == 46034 else {'result': 0}
