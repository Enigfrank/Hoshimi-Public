"""挑战奖励、重置和难度选择的持久化。"""

from copy import deepcopy

from .battle_progress import progress
from .battle_rewards import drop_config, roll_drops, grant_rewards
from .challenge_data import (boss_area, boss_group_ids, challenge_catalog,
                            rogue_score_reward_ids, rogue_selection_catalog)

CHALLENGE_COMMANDS = {44012, 44024, 44026, 44028, 44030, 45004, 45006, 45104,
                      45108, 45110, 45204, 55002, 55004, 55006, 55012, 55020, 88100, 88316}


def challenge_request(user: dict, command: int, request: dict) -> dict:
    """按真实通关记录领奖；深阱交由独立状态处理战果、重置和历史奖励。"""
    if command in (55002, 55004, 55006, 55012, 55020):
        from .abyss import abyss_request
        return abyss_request(user, command, request)
    catalog = challenge_catalog()
    rewards = []
    response = {'result': 0}
    if command == 44012:
        difficulty = user.get('mythic_difficulty', 1)
        cfg = next((c for c in catalog['mythic'] if c['id'] == difficulty), None)
        star = request['star']
        claimed = user.setdefault('mythic_star_rewards', {}).setdefault(str(difficulty), [])
        if not cfg or not 1 <= star <= len(cfg['star_reward_list']) or star in claimed:
            raise ValueError('黑区奖励无效或已领取')
        if len(progress(user, 11, cfg['main_partition']).get('stars', [])) < star:
            raise ValueError('主分区星数不足')
        rewards = roll_drops(drop_config(cfg['star_reward_list'][star-1]), 1)
        claimed.append(star)
        response['item_list'] = rewards
    elif command in (44024, 44026, 44028):
        difficulty = request.get('difficulty_id', user.get('mythic_final_difficulty', 1))
        cfg = next((c for c in catalog['final'] if c['id'] == difficulty), None)
        if not cfg:
            raise ValueError('终末黑区难度无效')
        if command == 44024:
            user['mythic_final_difficulty'] = difficulty
        elif command == 44026:
            key = str(difficulty)
            user.setdefault('mythic_final_runs', {}).pop(key, None)
            resets = user.setdefault('mythic_final_resets', {})
            resets[key] = resets.get(key, 0) + 1
        else:
            claimed = user.get('mythic_final_rewards') or []
            user['mythic_final_rewards'] = claimed
            # 通关高等级会同时解锁低等级奖励；官服 clear_list 只记最高档，
            # 不能要求领取档位必须精确出现在 clear_list 里。
            max_clear = max((int(x) for x in (user.get('mythic_final_cleared') or [])), default=0)
            if difficulty in claimed or difficulty > max_clear:
                raise ValueError('终末黑区未通关或已领取奖励')
            rewards = [{'id': i, 'num': n} for i, n in cfg['reward_list']]
            claimed.append(difficulty)
            claimed.sort()
            response['item_list'] = rewards
    elif command == 44030:
        # 黑区奖励页“一键领取”：领取所有已通关、尚未领取的终末黑区档位。
        cleared = user.get('mythic_final_cleared') or []
        claimed = user.get('mythic_final_rewards') or []
        user['mythic_final_rewards'] = claimed
        # 通关 30 档等于 1~30 档全部达成；只遍历 clear_list 会漏掉低档奖励。
        max_clear = max((int(x) for x in cleared), default=0)
        for cfg in catalog['final']:
            difficulty = cfg['id']
            if difficulty > max_clear or difficulty in claimed:
                continue
            rewards.extend({'id': i, 'num': n} for i, n in cfg['reward_list'])
            claimed.append(difficulty)
        claimed.sort()
        response['item_list'] = rewards
    elif command in (45004, 45006):
        area = boss_area(user)
        groups = boss_group_ids(user, area)[:area['boss_nums']]
        if command == 45006:
            if request['group_id'] not in groups:
                raise ValueError('梦境分组不存在')
            for stage in catalog['boss_groups'][str(request['group_id'])]:
                user.get('battle_progress', {}).pop(f'10:{stage}', None)
            user['boss_area_id'] = area['range_id']
            user['boss_group_id'] = request['group_id']
            user['boss_star_rewards'] = []
        else:
            star = request['star']
            drop = next((drop for need, drop in area['reward'] if need == star), None)
            total = sum(len(progress(user,10,s).get('stars', [])) for group in groups
                        for s in catalog['boss_groups'][str(group)])
            claimed = user.setdefault('boss_star_rewards', [])
            if not drop or total < star or star in claimed:
                raise ValueError('梦境星数不足或已经领奖')
            rewards = roll_drops(drop_config(drop), 1)
            claimed.append(star)
    elif command in (45104, 45108, 45110):
        group = request.get('id', request.get('group_id'))
        if command == 45110:
            valid = group in catalog['boss_groups'] or str(group) in catalog['boss_groups']
        else:
            valid = any(c['id'] == group for c in catalog['advance_pool'])
        if not valid:
            raise ValueError('梦境分组不存在')
        if command == 45104:
            config = next(c for c in catalog['advance'] if c['type'] == 2)
            pool = next(c for c in catalog['advance_pool'] if c['id'] == group)
            if not 1 <= request['diffculty_index'] <= len(config['monster_value']):
                raise ValueError('梦境难度无效')
            for field, choices in (('affix_index_list', pool['affix_pool']), ('time_index_list', pool['time_pool'])):
                if len(set(request[field])) != len(request[field]) or any(not 1 <= i <= len(choices) for i in request[field]):
                    raise ValueError('梦境词缀选项无效')
            user.setdefault('boss_affixes', {})[str(group)] = {k: deepcopy(request[k]) for k in
                ('affix_index_list', 'time_index_list', 'diffculty_index')}
        else:
            heroes = request['heroes_cfg']
            if len(heroes) > 3 or any(h and h not in {x['id'] for x in user['heroes']} for h in heroes):
                raise ValueError('梦境编队角色无效')
            user.setdefault('boss_teams', {})[str(group)] = list(heroes)
    elif command == 45204:
        user['boss_teams'] = {}
        user['boss_affixes'] = {}
    elif command == 88100:
        template = next((row for row in catalog['rogue'] if row['id'] == request['template_id']), None)
        configs = rogue_selection_catalog()
        difficulty = next((row for row in configs['difficulties']
                           if row['id'] == request['difficult']), None)
        if template is None or difficulty is None or difficulty['template_id'] != template['difficulty_temp']:
            raise ValueError('迭代校验模板或难度无效')
        allowed_heroes = set(template['hero_list'])
        seen = set()
        for hero in request.get('hero_list', []):
            standard = next((row for row in configs['standards']
                             if row['id'] == hero['temp_id']), None)
            key = (hero['hero_id'], hero['temp_id'])
            if (hero['temp_id'] not in allowed_heroes or standard is None
                    or standard['hero_id'] != hero['hero_id'] or key in seen
                    or not 0 <= hero['hp_ratio'] <= 10000):
                raise ValueError('迭代校验角色模板无效')
            seen.add(key)
        affixes = request.get('affix_pool_id_list', [])
        known_affixes = {row['id'] for row in configs['affixes']}
        if len(set(affixes)) != len(affixes) or any(
                affix not in difficulty.get('affix_pool', [])
                or affix not in known_affixes for affix in affixes):
            raise ValueError('迭代校验词缀无效')
        state = user.setdefault('rogue_team', {})
        state.update({'difficult': request['difficult'], 'hero_list': deepcopy(request.get('hero_list', [])),
                      'affix_pool_id_list': list(affixes)})
    elif command == 88316:
        template = next((row for row in catalog['rogue'] if row['id'] == request['main_id']), None)
        if template is None or request['id'] not in rogue_score_reward_ids(template['id']):
            raise ValueError('迭代积分奖励档位无效')
        user.setdefault('rogue_team', {})['last_id'] = request['id']
    if rewards:
        grant_rewards(user, rewards)
    return response
