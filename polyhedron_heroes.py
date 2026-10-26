"""多维队长快照、可用角色同步与系统战斗队伍。"""

from copy import deepcopy

from battle_heroes import owned_battle_hero, trial_battle_hero
from battle_rewards import equipment_data
from hero_preferences import hero_info_data
from loadout_storage import servant_data


def sync_unlocked_heroes(user: dict, data: dict) -> dict:
    """补入账号新获得且有多维模板的角色，保留局内解锁与独立神格选择。"""
    from polyhedron_game import polyhedron_catalog
    supported = {h['id'] for h in polyhedron_catalog()['heroes']}
    rows = data['unlocked_hero_list']
    existing = {h['hero_id'] for h in rows}
    for hero in user.get('heroes', []):
        if hero['id'] in supported and hero['id'] not in existing:
            rows.append({'hero_id': hero['id'], 'astrolabe_id_list': list(hero.get('using_astrolabes', []))})
            existing.add(hero['id'])
    return data


def leader_snapshot(user: dict, hero_id: int, astrolabes: list) -> dict:
    """以账号真实配装保存队长；未拥有的局内角色使用对应多维模板。"""
    from polyhedron_game import config_row
    info = next((h for h in hero_info_data(user)['hero_info_list']
                 if h['hero_base_info']['id'] == hero_id and h['unlock']), None)
    if info:
        member = owned_battle_hero(info, equipment_data(user)['equip_list'], servant_data(user)['servant_list'])
    else:
        member = trial_battle_hero(config_row('heroes', hero_id)['standard_id'])
    base = member['hero_base_info']
    return {'hero_id': hero_id, 'star': base['star'], 'skin': base['using_skin'],
            'break_level': base['break_level'], 'astrolabe_list': list(astrolabes),
            'equip_list': [{key: deepcopy(equip[key]) for key in
                            ('prefab_id', 'exp', 'now_break_level', 'enchant_slot_list', 'race')}
                           for equip in member['equip_list']],
            'weapon': {'breakthrough': base['weapon']['breakthrough'], 'servant': deepcopy(base['servant'])},
            'hero_slot_exclusive_skill_list': [
                {'slot_id': slot['slot_id'], 'skill_list': [
                    {'id': skill['skill_id'], 'skill_level': skill['skill_level']} for skill in slot['skill_list']]}
                for slot in base['exclusive_skill_list']],
            'weapon_module': {'level': base['weapon_module_level']}}


def prepare_battle(user: dict, common: dict) -> list[dict]:
    """核对路线保存点，从存档恢复系统队伍，并按真实角色 ID 下发多维模板。"""
    from polyhedron_game import polyhedron_data, config_row
    data = polyhedron_data(user)
    game, route = data['game'], data['game']['progress']
    stage = route.get('stage', {})
    if (game['state'] != 2 or stage.get('save_point') != 1 or stage.get('stage_id') != common['dest']
            or common.get('activity_id', 0) not in (0, data['activity_id'])):
        raise ValueError('多维关卡不属于当前保存点')
    ids = route['fight_hero_id_list']
    recruited = {h['hero_id']: h for h in route['hero_list']}
    leader = game['start_info']['leader']['hero_id']
    if (not ids or len(ids) > 3 or len(set(ids)) != len(ids) or ids[0] != leader
            or any(h not in recruited for h in ids) or recruited[leader]['health'] <= 0):
        raise ValueError('多维路线队伍无效')
    active = [h for h in ids if recruited[h]['health'] > 0]
    supplied = [h for h in common.get('hero_list', []) if h['hero_id']]
    if supplied and ([h['hero_id'] for h in supplied] != active or any(h['hero_type'] != 1 for h in supplied)):
        raise ValueError('多维战斗队伍与路线不一致')
    team = []
    for hero_id in active:
        template = config_row('heroes', hero_id)['standard_id']
        if recruited[hero_id]['template_id'] != template:
            raise ValueError('多维角色模板与路线不一致')
        member = deepcopy(trial_battle_hero(template))
        member['hero_base_info']['id'] = hero_id
        member['hero_type'] = 1
        for equip in member['equip_list']:
            equip['hero_id'] = hero_id
        team.append(member)
    common['hero_list'] = [{'hero_id': h, 'hero_type': 1} for h in active]
    return team
