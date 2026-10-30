"""角色战斗皮肤、收藏、神格与刻印装备配置的持久化。"""

from copy import deepcopy

from battle_rewards import equipment_data, grant_rewards, reward_item
from gameplay_protocol import decode_message, read_client_data
from hero_training_data import training_catalog, training_condition
from proto_bridge import get_bridge
from skin_scene_data import active_trials, skin_available, usable_skin_catalog

HERO_COMMANDS = {14028, 14038, 14040, 14046, 14106, 14108, 14114, 71116,
                 13012, 13016, 13018, 13026, 13054, 14102, 14104, 71106, 71108, 71114}


def hero_info_data(user: dict) -> dict:
    """恢复角色及独立皮肤库存；只有皮肤的角色保持未解锁。"""
    heroes = deepcopy(user.get('heroes', []))
    by_id = {h['id']: h for h in heroes}
    trials = active_trials(user, 'skin_trials')
    catalog = usable_skin_catalog()
    for skin_id in sorted(set(user.get('owned_skins', [])) | set(trials)):
        hero_id = catalog.get(skin_id)
        if hero_id is None:
            continue
        if hero_id not in by_id:
            from account_defaults import new_hero
            hero = new_hero(hero_id)
            hero['unlock'] = 0
            by_id[hero_id] = hero
            heroes.append(hero)
        if skin_id not in by_id[hero_id]['skins']:
            by_id[hero_id]['skins'].append(skin_id)
    for hero in heroes:
        hero['skins'] = [skin for skin in hero['skins'] if catalog.get(skin) == hero['id']]
        for field in ('skin_id', 'battle_skin_id'):
            if hero.get(field) and hero[field] not in hero['skins']:
                hero[field] = hero['id']
    data = decode_message(14009, get_bridge().encode_sc_14009(heroes), 'sc')
    data['favorites'] = list(user.get('favorite_heroes', []))
    data['proposal_list'] = deepcopy(user.get('equip_proposals', []))
    data['piece_list'] = [{'id': int(i), 'num': n} for i, n in user.get('hero_pieces', {}).items()]
    from hero_affinity import archive_data
    data['archives'] = archive_data(user)
    for info in data['hero_info_list']:
        base = info['hero_base_info']
        saved = next((h for h in user.get('heroes', []) if h['id'] == base['id']), {})
        base['exp'] = saved.get('exp', base['exp'])
        info['weapon_module_assignment'] = saved.get('module_assignment', info['weapon_module_assignment'])
        base['weapon']['servant_uid'] = saved.get('servant_uid', base['weapon']['servant_uid'])
        for skill in base['skill']:
            skill['skill_level'] = saved.get('skill_levels', {}).get(str(skill['skill_id']), skill['skill_level'])
        for attr in base['skill_intensify_attribute_list']:
            attr['level'] = saved.get('skill_attr_levels', {}).get(str(attr['index']), attr['level'])
        info['trust']['exp'] = saved.get('trust_exp', info['trust']['exp'])
        info['trust']['mood'] = saved.get('trust_mood', info['trust']['mood'])
        if 'relation' in saved:
            info['trust']['relation'] = deepcopy(saved['relation'])
        permanent = set(user.get('owned_skins', [])) | set(next(
            (h['skins'] for h in user.get('heroes', []) if h['id'] == base['id']), []))
        for skin in info['unlocked_skin']:
            if skin['skin_id'] in trials and skin['skin_id'] not in permanent:
                skin['time'] = trials[skin['skin_id']]
        preferences = user.get('hero_preferences', {}).get(str(base['id']), {})
        if 'exclusive_skill_list' in preferences:
            base['exclusive_skill_list'] = deepcopy(preferences['exclusive_skill_list'])
    return data


def equip_on_hero(user: dict, hero: dict, equip_id: int, position: int) -> None:
    """验证刻印槽位，解绑旧拥有者，保证一个刻印只装备在一个角色上。"""
    inventory = user['equipment']
    equipped = next((e for e in inventory if e['equip_id'] == equip_id), None)
    if not equipped or reward_item(equipped['prefab_id'])['equip']['pos'] != position:
        raise ValueError('刻印不存在或槽位不匹配')
    if not 1 <= position <= 6:
        raise ValueError('刻印槽位无效')
    for owned in user['heroes']:
        owned['equip_ids'] = [0 if i == equip_id else i for i in owned['equip_ids']]
    old_id = hero['equip_ids'][position-1]
    old = next((e for e in inventory if e['equip_id'] == old_id), None)
    if old:
        old['hero_id'] = 0
    hero['equip_ids'][position-1] = equip_id
    equipped['hero_id'] = hero['id']


def hero_request(user: dict, command: int, request: dict) -> dict:
    """保存角色选择和刻印操作，使用真实协议返回对应结果。"""
    response = {'result': 0}
    if command == 13054:
        if request['type'] not in (1, 2, 3, 4, 5) or request['sign'] not in (0, 1):
            raise ValueError('刻印自动分解设置无效')
        ids = user.setdefault('equip_auto_decompose', [])
        if request['sign'] and request['type'] not in ids:
            ids.append(request['type'])
        elif not request['sign'] and request['type'] in ids:
            ids.remove(request['type'])
        return response
    if command in (14102, 14104, 71106, 71108, 71114):
        archive_id = request.get('archive_id')
        anecdote = None
        if command == 71108:
            hero_id = request['hero_id']
            hero = next((h for h in user['heroes']
                         if h['id'] == hero_id and h.get('unlock', 1)), None)
            if hero is None:
                raise ValueError('档案角色未拥有')
            # 逸闻解锁由配置和交心等级决定，hero_story_list 只记录已读状态。
            catalog = training_catalog()
            anecdote = catalog['HeroAnedoteCfg'].get(str(hero_id))
            if anecdote is None:
                raise ValueError('逸闻配置不存在')
            conditions = catalog['settings'].get('hero_anecdote_unlock_condition') or []
            if not conditions or not training_condition(user, hero, conditions[0]):
                raise ValueError('档案故事尚未解锁')
            ids = read_client_data(f"require('HeroRecordCfg').get_id_list_by_hero_id[{int(hero_id)}] or {{}}")
            archive_id = ids[0] if ids else None
        archive = next((a for a in hero_info_data(user)['archives'] if a['archive_id'] == archive_id), None)
        if archive is None:
            raise ValueError('角色档案不存在')
        if command in (14102, 14104):
            field = 'text_list' if command == 14102 else 'video_list'
            if any(i <= 0 for i in request[field]):
                raise ValueError('档案阅读索引无效')
            archive[field] = sorted(set(archive[field]) | set(request[field]))
        elif command == 71106:
            if request['type'] not in (1, 2) or request['id'] <= 0:
                raise ValueError('档案壁纸无效')
            archive['selected_picture'] = {'type': request['type'], 'id': request['id']}
        else:
            field = 'hero_story_list' if command == 71108 else 'super_heart_link_list'
            key = 'hero_id' if command == 71108 else 'index'
            index = request[key]
            row = next((r for r in archive[field] if r[key] == index), None)
            was_viewed = bool(row and row.get('is_viewed'))
            if row is None:
                if command == 71114:
                    raise ValueError('档案故事尚未解锁')
                row = {key: index, 'is_viewed': True}
                archive[field].append(row)
            else:
                row['is_viewed'] = True
            if command == 71108 and not was_viewed:
                rewards = [{'id': int(i), 'num': int(n)}
                           for i, n in (anecdote.get('reward') or []) if n > 0]
                grant_rewards(user, rewards)
                response['reward_list'] = rewards
        user.setdefault('archives', {})[str(archive_id)] = archive
        return response
    if command in (13012, 13016, 13018, 13026):
        if not user.get('equipment_initialized'):
            user['equipment'] = deepcopy(equipment_data(user)['equip_list'])
            user['equipment_initialized'] = True
        if command == 13016:
            equip = next((e for e in user['equipment'] if e['equip_id'] == request['equip_id']), None)
            if equip is None:
                raise ValueError('刻印未拥有')
            equip['is_lock'] = bool(request['is_lock'])
            return response
    hero_id = request.get('hero_id', request.get('id'))
    hero = next((h for h in user['heroes'] if h['id'] == hero_id), None)
    if hero is None:
        raise ValueError('角色未拥有')
    if command == 14046:
        skin = request['skin_id'] or hero_id
        if not skin_available(user, hero, skin):
            raise ValueError('战斗皮肤未解锁')
        hero['battle_skin_id'] = skin
    elif command in (14106, 14108):
        favorites = user.setdefault('favorite_heroes', [])
        if command == 14106 and hero_id not in favorites:
            favorites.append(hero_id)
        elif command == 14108 and hero_id in favorites:
            favorites.remove(hero_id)
    elif command in (14028, 14038, 14040, 71116):
        selected = list(hero.get('using_astrolabes', []))
        if command == 14040:
            selected = []
        elif command == 14038:
            suit = request['astrolabe_suit_id']
            selected = read_client_data(f"require('HeroAstrolabeCfg').get_id_list_by_hero_astrolabe_suit_id[{int(suit)}] or {{}}")
            selected = selected or []
        elif command == 71116:
            selected = request['astrolabe_id_list']
        elif request['operation'] == 1:
            if request['astrolabe_id'] not in selected:
                selected.append(request['astrolabe_id'])
        elif request['operation'] == 2:
            selected = [i for i in selected if i != request['astrolabe_id']]
        else:
            raise ValueError('神格操作无效')
        if len(selected) > 3 or len(set(selected)) != len(selected) or any(i not in hero['astrolabes'] for i in selected):
            raise ValueError('神格未解锁或超出装备数量')
        hero['using_astrolabes'] = selected
    elif command == 14114:
        slot = request['slot_id']
        if not 1 <= slot <= 6:
            raise ValueError('跃迁槽位无效')
        skills = request['skill_list']
        if sum(s['skill_level'] for s in skills) > 6 or len({s['skill_id'] for s in skills}) != len(skills):
            raise ValueError('跃迁配置无效')
        for skill in skills:
            cfg = read_client_data(f"require('EquipSkillCfg')[{int(skill['skill_id'])}] or {{}}")
            if not cfg or not 1 <= skill['skill_level'] <= 3:
                raise ValueError('跃迁技能无效')
        current = next(h for h in hero_info_data(user)['hero_info_list'] if h['hero_base_info']['id'] == hero_id)
        slots = current['hero_base_info']['exclusive_skill_list']
        selected = next(s for s in slots if s['slot_id'] == slot)
        selected['skill_list'] = deepcopy(skills)
        user.setdefault('hero_preferences', {}).setdefault(str(hero_id), {})['exclusive_skill_list'] = slots
    elif command == 13018:
        for equip in user['equipment']:
            if equip.get('hero_id') == hero_id:
                equip['hero_id'] = 0
        hero['equip_ids'] = [0] * 6
    elif command == 13012:
        if request['equip_id'] == 0:
            position = request['pos']
            if not 1 <= position <= 6:
                raise ValueError('刻印槽位无效')
            old_id = hero['equip_ids'][position-1]
            for equip in user['equipment']:
                if equip['equip_id'] == old_id:
                    equip['hero_id'] = 0
            hero['equip_ids'][position-1] = 0
        else:
            equip_on_hero(user, hero, request['equip_id'], request['pos'])
    elif command == 13026:
        for row in request['use_equip_list']:
            equip_on_hero(user, hero, row['equip_id'], row['pos'])
        response = {'result': [{'equip_id': row['equip_id'], 'result': 0} for row in request['use_equip_list']]}
    return response
