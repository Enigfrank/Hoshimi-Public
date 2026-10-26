"""偶像竞技的本地确定性逐回合评分与完整回放。"""

from copy import deepcopy

from backhome_catalog import home_config


def idol_roll(seed: int, index: int) -> int:
    """固定种子抽样便于断线恢复与双端对照，不宣称复刻官服随机序列。"""
    return ((seed % 2147483647) * 1103515245 + index * 12345 + 1013904223) % 2147483647 % 100


def weighted_style(weights: list, roll: int) -> int:
    """使用角色实际风格权重选择本回合动作。"""
    value = roll * sum(weights) // 100
    for index, weight in enumerate(weights, 1):
        if value < weight:
            return index
        value -= weight
    return len(weights)


def idol_actor(hero_id: int, attributes: list, npc: bool) -> dict:
    """读取真实角色或NPC技能，属性总值达到门槛才解锁玩家技能。"""
    cfg = home_config('IdolPveNpcCfg' if npc else 'BackHomeHeroCfg', hero_id)
    active, passive = [], []
    for field, target in (('idol_active_skill_list', active), ('idol_passive_skill_list', passive)):
        for row in cfg[field]:
            if npc:
                target.append(row)
            elif sum(attributes) >= row[1]:
                target.append(row[0])
    return {'hero_id': hero_id, 'attributes': list(attributes), 'weights': cfg['idol_weight'],
            'active': active, 'passive': passive, 'effects': [], 'active_count': 0,
            'style': 1, 'triggered': False, 'total': 0, 'gain': 0, 'loss': 0, 'base': 0}


def queue_effect(actor: dict, target: dict, effect_id: int, round_id: int) -> None:
    """保留目标、延迟与持续回合，状态不会提前作用或永久滞留。"""
    cfg = home_config('IdolTraineeSkillEffectCfg', effect_id)
    recipient = actor if cfg['object'] == 1 else target
    start = round_id + cfg['trigger_time']
    recipient['effects'].append({'id': effect_id, 'start': start,
                                'end': 1000000 if cfg['round'] == -1 else start + max(1, cfg['round'])})


def applicable_effects(actor: dict, round_id: int, scene: int, seed: int) -> list:
    """核对场景、状态数量、主动触发次数与动作风格条件，再应用配置概率。"""
    result = []
    current = [e for e in actor['effects'] if e['start'] <= round_id < e['end']]
    actor['effects'] = [e for e in actor['effects'] if e['end'] > round_id]
    for row in current:
        cfg = home_config('IdolTraineeSkillEffectCfg', row['id'])
        kind, args = cfg['condition'], cfg['con_param']
        valid = kind == 0 or kind == 1 and scene == args[0] or kind == 2 and len(current) >= args[0]
        valid = valid or kind == 3 and actor['triggered'] or kind == 4 and actor['active_count'] == args[0]
        valid = valid or kind == 5 and actor['style'] == args[0]
        if valid and idol_roll(seed, row['id'] + round_id) < cfg['valid_probability']:
            result.append(cfg)
    return result


def idol_round_score(actor: dict, effects: list, rates: list, coefficient: int) -> dict:
    """本地评分取风格属性加全属性配置系数，叠加配置的属性、动作与固定分效果。"""
    attributes = []
    for index, value in enumerate(actor['attributes'], 1):
        percent = sum(e['unique'][1] for e in effects if e['effect_type'] == 1 and e['unique'][0] == index)
        percent += sum(e['unique'][0] for e in effects if e['effect_type'] == 6)
        attributes.append(max(0, value * (100 + percent) // 100))
    style = actor['style']
    base = actor['attributes'][style - 1] + sum(actor['attributes']) * coefficient // 100
    value = (attributes[style - 1] + sum(attributes) * coefficient // 100) * rates[style - 1] // 100
    percentage = sum(e['unique'][1] for e in effects if e['effect_type'] == 2 and e['unique'][0] == style)
    percentage += sum(e['unique'][0] for e in effects if e['effect_type'] in (5, 7))
    value = value * max(0, 100 + percentage) // 100
    value += sum(e['unique'][0] for e in effects if e['effect_type'] == 4)
    value += sum(attributes[e['unique'][0] - 1] * e['unique'][1] // 100 for e in effects if e['effect_type'] == 8)
    value = max(0, value)
    difference = value - base
    actor['base'] += base
    actor['gain'] += max(0, difference)
    actor['loss'] += max(0, -difference)
    actor['total'] += value
    return {'peculiarity_score': base, 'skill_score': difference, 'sum_score': value}


def generate_idol_battle(attacker: dict, defender: dict, cfg: dict, seed: int, rounds: int, coefficient: int) -> dict:
    """由服务端生成并保存完整双方回放，不接受客户端直接声明胜利或分数。"""
    actors = [deepcopy(attacker), deepcopy(defender)]
    prepare = []
    for index, actor in enumerate(actors):
        ids = []
        for skill in actor['passive']:
            for effect in home_config('IdolTraineeSkillCfg', skill)['skill_effect']:
                if idol_roll(seed, effect) < home_config('IdolTraineeSkillEffectCfg', effect)['probability']:
                    queue_effect(actor, actors[1 - index], effect, 1)
                    ids.append(effect)
        prepare.append(ids)
    replay = []
    rates = cfg['peculiarity_rate']
    for round_id in range(1, rounds + 1):
        actions = []
        for index, actor in enumerate(actors):
            actor['style'] = weighted_style(actor['weights'], idol_roll(seed, round_id * 31 + index))
            actor['triggered'] = False
            previous = applicable_effects(actor, round_id, cfg['scene_id'], seed + index)
            # 黑箱主动触发采用公开记录的本地基准50%，配置技能可修改触发率。
            chance = 50 + sum(e['unique'][0] for e in previous if e['effect_type'] == 3)
            skill = actor['active'][0] if actor['active'] and idol_roll(seed, round_id * 43 + index) < max(0, min(100, chance)) else 0
            chosen = []
            if skill:
                actor['triggered'] = True
                actor['active_count'] += 1
                for offset, effect in enumerate(home_config('IdolTraineeSkillCfg', skill)['skill_effect'], 1):
                    if idol_roll(seed, round_id * 53 + effect) < home_config('IdolTraineeSkillEffectCfg', effect)['probability']:
                        queue_effect(actor, actors[1 - index], effect, round_id)
                        chosen.append(offset)
            actions.append({'atk_style_id': actor['style'], 'skill_id': skill, 'index_list': chosen})
        scores = [idol_round_score(actor, applicable_effects(actor, round_id, cfg['scene_id'], seed + index), rates, coefficient)
                  for index, actor in enumerate(actors)]
        replay.append({'round_index': round_id, 'attack_user_action': actions[0], 'defend_user_action': actions[1],
                       'attack_user_score': scores[0], 'defend_user_score': scores[1]})
    totals = [{'sum_score': a['total'], 'peculiarity_score': a['base'],
               'skill_add_score': a['gain'], 'skill_decrease_score': a['loss']} for a in actors]
    return {'prepare_info': {'attacker_active_buff_list': prepare[0], 'defender_active_buff_list': prepare[1]},
            'round_list': replay, 'battle_result': 1 if totals[0]['sum_score'] >= totals[1]['sum_score'] else 2,
            'attacker_data': totals[0], 'defender_data': totals[1]}
