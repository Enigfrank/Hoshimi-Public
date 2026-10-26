"""使用真实战斗统计推进同调任务、熟练度和连携奥义培养条件。"""

from gameplay_protocol import read_client_data
from hero_affinity import exchange_data
from hero_training_data import owned_hero, total_skill_level, training_catalog


def combo_info(user: dict, combo_id: int) -> dict:
    """恢复连携奥义等级及独立条件进度，未培养的连携从零阶开始。"""
    saved = user.get('combo_skills', {}).get(str(combo_id), {})
    return {'id': combo_id, 'level': saved.get('level', 0),
            'progress_list': [{'id': int(i), 'times': n} for i, n in saved.get('progress', {}).items()]}


def training_initial_pushes(user: dict) -> list[tuple]:
    """登录恢复连携等级、每日礼物置换限额与关系故事领奖事实。"""
    heroes = {h['id'] for h in user.get('heroes', []) if h.get('unlock', 1)}
    combos = [combo_info(user, int(i)) for i, cfg in training_catalog()['ComboSkillCfg'].items()
              if all(h in heroes for h in cfg['cooperate_role_ids'])]
    return [(73001, {'info': {'skill_list': combos}}), (73005, exchange_data(user)),
            (73007, {'got_reward_id_list': user.get('relation_story_rewards', [])})]


def upgrade_combo(user: dict, request: dict) -> dict:
    """所有条件达标才推进连携奥义一阶，重复请求不能越过下一阶条件。"""
    catalog = training_catalog()
    combo = request['cooperate_unique_skill_id']
    cfg = catalog['ComboSkillCfg'].get(str(combo))
    if not cfg:
        raise ValueError('连携奥义不存在')
    heroes = [owned_hero(user, h) for h in cfg['cooperate_role_ids']]
    current = combo_info(user, combo)['level']
    if current >= cfg['maxLevel']:
        raise ValueError('连携奥义已达最高等级')
    state = user.setdefault('combo_skills', {}).setdefault(str(combo), {'level': 0, 'progress': {}})
    for key, condition in catalog['ComboSkillLevelCfg'].items():
        if condition['level'] != current:
            continue
        if condition['condition_type'] == 1:
            value = min(total_skill_level(h, catalog['heroes'][str(h['id'])]['skills'][4]) for h in heroes)
        else:
            value = state['progress'].get(key, 0)
        if value < condition['target']:
            raise ValueError('连携奥义升级条件未满足')
    state['level'] = current + 1
    return {'result': 0}


def combo_battle_condition(user: dict, battle: dict, condition: dict) -> bool:
    """依据实际梦境模式、关卡难度与黑区层级核对连携任务。"""
    kind = battle['common']['type']
    args = condition['args']
    if condition['condition_type'] == 2:
        return True
    if condition['condition_type'] == 3:
        from challenge_data import challenge_catalog
        catalog = challenge_catalog()
        if args[0] == 0 and kind == 10:
            area = next((c for c in catalog['boss'] if c['level'][0] <= user.get('level', 1) <= c['level'][1]), None)
            return area is not None and area['range_id'] >= args[1] and any(
                battle['common']['dest'] in catalog['boss_groups'][str(g)][args[2] - 1:] for g in area['boss_list'])
        if args[0] == 1 and kind == 100:
            pool = next((p for p in catalog['advance_pool'] if p['stage_id'] == battle['config']['id']), None)
            if pool is None:
                return False
            options = user.get('boss_affixes', {}).get(str(pool['id']), {})
            active = next(c for c in catalog['advance'] if c['type'] == 2)
            return active['id'] >= args[1] and options.get('diffculty_index', 1) >= args[2]
        return False
    if condition['condition_type'] == 4:
        if args[0] == 0 and kind == 11:
            return user.get('mythic_difficulty', 1) >= args[1]
        if args[0] == 1 and kind == 35:
            return battle.get('final_difficulty', 0) >= args[1]
        return False
    return condition['condition_type'] == 5


def complete_training_battle(user: dict, battle: dict, times: int) -> None:
    """胜利结算仅调用一次；native统计38/39/40的值是完成同调任务的角色ID。"""
    common, info = battle['common'], battle['reported']['info']
    records = dict(zip(info.get('14', []), info.get('15', [])))
    selected = set()
    for member in common['hero_list']:
        hero_id = member['hero_id']
        if not hero_id:
            continue
        if member.get('hero_type', 1) == 2:
            hero_id = read_client_data(f"(require('HeroStandardSystemCfg')[{int(hero_id)}] or {{}}).hero_id or 0")
        selected.add(hero_id)
    for hero in user.get('heroes', []):
        if hero['id'] in selected and hero.get('unlock', 1):
            hero['clear_times'] = hero.get('clear_times', 0) + times
            if hero['id'] in [records.get(i, 0) for i in (38, 39, 40)]:
                hero['module_assignment'] = 1
    combo = common.get('cooperate_unique_skill_id', 0)
    cfg = training_catalog()['ComboSkillCfg'].get(str(combo))
    if not cfg or not all(h in selected for h in cfg['cooperate_role_ids']):
        return
    state = user.setdefault('combo_skills', {}).setdefault(str(combo), {'level': 0, 'progress': {}})
    for key, condition in training_catalog()['ComboSkillLevelCfg'].items():
        if (condition['level'] == state['level'] and condition['condition_type'] != 1
                and combo_battle_condition(user, battle, condition)):
            count = max(0, records.get(12, 0)) if condition['condition_type'] == 5 else times
            state['progress'][key] = min(condition['target'], state['progress'].get(key, 0) + count)


def training_battle_pushes(user: dict) -> list[tuple]:
    """战斗结算后立即更新参战角色的熟练度、同调任务和连携条件。"""
    from hero_preferences import hero_info_data
    battle = user.get('battle_session', {})
    if not battle or not battle.get('settlement') or battle['reported']['result'] != 1:
        return []
    ids = {h['hero_id'] for h in battle['common']['hero_list'] if h['hero_id']}
    rows = hero_info_data(user)['hero_info_list']
    pushes = [(14007, {'hero': h}) for h in rows if h['hero_base_info']['id'] in ids]
    combo = battle['common'].get('cooperate_unique_skill_id', 0)
    if str(combo) in user.get('combo_skills', {}):
        pushes.append((73003, {'skill': combo_info(user, combo)}))
    return pushes
