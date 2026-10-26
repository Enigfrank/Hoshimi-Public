"""修正者培养协议路由及成功回调之后的权威数据同步。"""

from account_defaults import unlock_hero
from battle_rewards import inventory_pushes
from hero_affinity import affinity_request, exchange_data
from hero_growth import grow_hero, grow_weapon
from hero_preferences import hero_info_data
from hero_talents import (resolve_module_items, train_module, train_skill,
                          train_transition, unlock_astrolabe)
from hero_training_data import owned_hero, training_catalog
from hero_training_progress import combo_info, upgrade_combo
from loadout_storage import servant_data

TRAINING_COMMANDS = {14014, 14016, 14026, 14030, 14036, 14042, 14044,
                     14100, 14112, 14114, 14116, 14118, 14120, 14122,
                     46016, 46018, 46034, 73010, 73012, 73014, 73016, 73018, 73020, 73022}


def training_request(user: dict, command: int, request: dict) -> dict:
    """分派实际培养操作，未知、未拥有或不满足配置条件的请求明确拒绝。"""
    if command in (14100, 73010, 73012, 73014, 73016, 73020, 73022):
        return affinity_request(user, command, request)
    if command == 14118:
        return resolve_module_items(user, request)
    if command == 73018:
        return upgrade_combo(user, request)
    hero_id = request.get('hero_id', request.get('id'))
    if command == 14016:
        if any(h['id'] == hero_id for h in user.get('heroes', [])):
            raise ValueError('角色已拥有')
        cfg = training_catalog()['heroes'].get(str(hero_id))
        if not cfg:
            raise ValueError('角色配置无效')
        needed = training_catalog()['settings']['unlock_hero_need'][cfg['rare'] - 1]
        pieces = user.get('hero_pieces', {}).get(str(hero_id), 0)
        if pieces < needed:
            raise ValueError('角色碎片不足')
        unlock_hero(user, hero_id)
        user['hero_pieces'][str(hero_id)] = pieces - needed
        return {'result': 0}
    hero = owned_hero(user, hero_id)
    if command in (14014, 14036, 14120):
        return grow_hero(user, hero, command, request)
    if command in (46016, 46018, 46034):
        return grow_weapon(user, hero, command, request)
    if command in (14030, 14044):
        return train_skill(user, hero, command, request)
    if command == 14026:
        return unlock_astrolabe(user, hero, request)
    if command in (14112, 14114):
        return train_transition(user, hero, command, request)
    if command == 14116:
        return train_module(user, hero)
    if command == 14042:
        return {'result': 0}
    if command == 14122:
        return {'result': 0, 'value': hero.get('heart_rate', 0)}
    raise ValueError('培养指令未支持')


def training_pushes(user: dict, command: int, request: dict) -> list[tuple]:
    """响应之后同步最终状态，避免原始客户端回调重复增加等级或天赋点。"""
    heroes = hero_info_data(user)
    ids = {request.get('hero_id', request.get('id', 0))}
    if command in (14100, 73010, 73016, 73022):
        ids = {h['id'] for h in user['heroes']}
    pushes = [(14007, {'hero': h}) for h in heroes['hero_info_list'] if h['hero_base_info']['id'] in ids]
    if command == 14100:
        pushes.append((14009, heroes))
    if command == 14016:
        pushes.append((14019, {'piece_list': heroes['piece_list']}))
    if command in (46016, 46034):
        pushes.append((46011, servant_data(user)))
    if command == 73010:
        pushes.append((73005, exchange_data(user)))
    if command == 73022:
        pushes.append((73007, {'got_reward_id_list': user.get('relation_story_rewards', [])}))
    if command == 73018:
        pushes.append((73003, {'skill': combo_info(user, request['cooperate_unique_skill_id'])}))
    if command not in (14042, 14122, 14114):
        pushes.extend(inventory_pushes(user)[:2])
    return pushes
