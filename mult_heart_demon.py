"""古远虚影队伍、连续挑战、可选保存及本地评分。"""

from copy import deepcopy
from functools import lru_cache
import time

from activity_data import RESOURCE_ACTIVITIES
from gameplay_protocol import read_client_data, lua_value

MULT_HEART_COMMANDS = {75020, 75022, 75026, 75028}


@lru_cache(maxsize=1)
def current_edition() -> int:
    """选择本机开放且有玩法配置的一期，客户端仅支持一个当前期次。"""
    activities = sorted(aid for aid, (_, template, _) in RESOURCE_ACTIVITIES.items() if template == 350)
    return read_client_data('''(function()
        local cfg=require('MultHeartDemonCfg')
        for _,id in ipairs(__ACTIVITIES__) do
            if cfg.get_id_list_by_activity_id[id] then return id end
        end
        error('古远虚影缺少有效活动配置')
    end)()'''.replace('__ACTIVITIES__', lua_value(activities)))


@lru_cache(maxsize=1)
def demon_catalog() -> dict:
    """读取当前期次的双方队伍、关卡和本地评分系数。"""
    return read_client_data('''(function()
        local c=require('MultHeartDemonCfg');local a=__EDITION__
        local g=require('GameSetting');local s={}
        for _,k in ipairs({'basescore','timescore','hitscore','hpscore','boss_hp'}) do
            s[k]=g['multi_heart_demon_'..k].value[1]
        end
        local cfg=c[c.get_id_list_by_activity_id[a][1]];local trials={}
        for _,id in ipairs(cfg.trial_hero) do
            trials[#trials+1]={id=id,hero_id=require('HeroStandardSystemCfg')[id].hero_id}
        end
        return {edition=a,config=cfg,score=s,trials=trials}
    end)()'''.replace('__EDITION__', str(current_edition())))


def demon_state(user: dict) -> dict:
    """按期次恢复挑战与历史最高分，重置一局不清除领奖记录。"""
    edition = current_edition()
    state = user.setdefault('mult_heart_demon', {})
    if state.get('activity_id') != edition:
        state.clear()
        state.update(activity_id=edition, runs={}, best={}, serial=0)
    return state


def stage_difficulty(stage: int) -> int:
    """限制战斗关卡为本机当前期次的两档挑战。"""
    for difficulty, stages in demon_catalog()['config']['stage_id']:
        if stage in stages:
            return difficulty
    raise ValueError('古远虚影关卡不属于当前期次')


def remaining_health(run: dict) -> dict:
    """从最新到最旧的战绩恢复双方百分比生命。"""
    health = {hero: 100 for hero in run['hero_id_list'] + demon_catalog()['config']['heart_damon_id']}
    for row in reversed(run['stage_info_list']):
        health[row['hero_id']] = row['hero_hp']
        difficulty = stage_difficulty(row['stage_id'])
        stages = dict(demon_catalog()['config']['stage_id'])[difficulty]
        boss = demon_catalog()['config']['heart_damon_id'][stages.index(row['stage_id'])]
        health[boss] = row['boss_hp']
    return health


def validate_demon_battle(user: dict, common: dict) -> dict:
    """只允许当前存活队员单独挑战下一位存活心魔。"""
    difficulty = stage_difficulty(common['dest'])
    run = demon_state(user)['runs'].get(str(difficulty))
    if not run or not run['enter_flag']:
        raise ValueError('请先保存古远虚影队伍')
    health = remaining_health(run)
    hero = next((h for h in run['hero_id_list'] if health[h] > 0), None)
    bosses = demon_catalog()['config']['heart_damon_id']
    index = next((i for i, boss in enumerate(bosses) if health[boss] > 0), None)
    selections = [h['hero_id'] for h in common['hero_list'] if h['hero_id']]
    stages = dict(demon_catalog()['config']['stage_id'])[difficulty]
    if hero is None or index is None or common['dest'] != stages[index] or selections != [hero]:
        raise ValueError('古远虚影关卡或出战队员不符合当前进度')
    if common.get('battle_times', 1) != 1:
        raise ValueError('古远虚影不支持多倍战斗')
    return run


def settle_demon_battle(user: dict, battle: dict) -> None:
    """保存待确认的真实战报；本地分为基础分减耗时和受击扣分。"""
    run = validate_demon_battle(user, battle['common'])
    if run['serial'] != battle.get('demon_serial') or run.get('battle_id') != battle['id']:
        raise ValueError('古远虚影战斗已被重置或替换')
    report, stage = battle['reported'], battle['common']['dest']
    info = report['info']
    # native 固定上报三个槽位，未使用的槽位可以为空消息或全零记录。
    characters = [c for c in info.get('characters', []) if c.get('1', 0) or c.get('3', 0)]
    if len(characters) != 1 or characters[0].get('3', 0) <= 0:
        raise ValueError('古远虚影战报缺少单人生命统计')
    character = characters[0]
    hero = next(h['hero_id'] for h in battle['common']['hero_list'] if h['hero_id'])
    actual_hero = next((h['hero_id'] for h in demon_catalog()['trials'] if h['id'] == hero), hero)
    if character.get('1') != actual_hero:
        # 实机统计使用当前角色的皮肤 ID；既有测试和旧战报也可能使用角色 ID。
        skin_hero = read_client_data(f"(function() local c=require('SkinCfg')[{int(character.get('1', 0))}]; return c and c.hero or 0 end)()")
        if skin_hero != actual_hero:
            raise ValueError('古远虚影战报角色与队伍不一致')
    hp = max(0, min(100, (max(0, character.get('2', 0))*100 + character['3']//2)//character['3']))
    if character.get('2', 0) > 0:
        hp = max(1, hp)
    factors = demon_catalog()['score']
    seconds, hits = max(0, info.get('2', 0))//1000, max(0, info.get('4', 0))
    won = report['result'] == 1
    score = max(0, factors['basescore'] - seconds*factors['timescore'] - hits*factors['hitscore']) if won else 0
    difficulty = stage_difficulty(stage)
    stages = dict(demon_catalog()['config']['stage_id'])[difficulty]
    boss = demon_catalog()['config']['heart_damon_id'][stages.index(stage)]
    max_boss_hp = factors['boss_hp'] * battle['config']['attribute_factor'][2] // 1000
    boss_hp = 0 if won else max(1, remaining_health(run)[boss] - max(0, character.get('4', 0))*100//max(1, max_boss_hp))
    run['pending'] = {'battle_id': battle['id'], 'seconds': seconds, 'hits': hits,
        'row': {'stage_id': stage, 'challenge_state': 1, 'max_score': score,
                'least_injure_num': max(0, info.get('5', 0)), 'least_hit_num': hits,
                'hero_hp': hp, 'boss_hp': boss_hp, 'hero_id': hero}}
    user['mult_heart_demon_score'] = score


def demon_request(user: dict, command: int, request: dict) -> dict:
    """校验队伍、保存一次战报、放弃或确认服务端配置的心魔生命倍率。"""
    catalog = demon_catalog()
    state = demon_state(user)
    if command != 75028 and request['activity_id'] != catalog['edition']:
        raise ValueError('古远虚影期次无效')
    difficulty = stage_difficulty(request['stage_id']) if command in (75022, 75028) else request['difficulty']
    if difficulty not in (1, 2):
        raise ValueError('古远虚影难度无效')
    key = str(difficulty)
    run = state['runs'].get(key)
    if command == 75020:
        heroes = request['hero_id_list']
        allowed = catalog['config']['trial_hero'] if difficulty == 2 else [h['id'] for h in user['heroes']] + catalog['config']['trial_hero']
        if len(heroes) != 3 or len(set(heroes)) != 3 or not set(heroes) <= set(allowed):
            raise ValueError('古远虚影队伍须包含三名有效且不同的角色')
        actual_ids = [next((row['hero_id'] for row in catalog['trials'] if row['id'] == hero), hero) for hero in heroes]
        if len(set(actual_ids)) != 3:
            raise ValueError('古远虚影不能重复选择同一角色的不同模板')
        if run and run['enter_flag']:
            if heroes == run['hero_id_list']:
                return {'result': 0}
            raise ValueError('古远虚影挑战中不能更换队伍')
        health = request.get('hero_max_hp', [])
        if len(health) != 3 or {h['hero_id'] for h in health} != set(heroes) or any(h['hero_max_hp'] <= 0 for h in health):
            raise ValueError('古远虚影队伍生命参数无效')
        state['serial'] += 1
        state['runs'][key] = {'difficulty': difficulty, 'hero_id_list': list(heroes),
            'stage_info_list': [], 'enter_flag': True, 'serial': state['serial'], 'pending': None,
            'battle_id': 0, 'saved_battle_id': 0}
    elif command == 75026:
        state['runs'].pop(key, None)
    elif command == 75028:
        from battle_data import stage_config
        if not run or not run['enter_flag'] or request['boss_hp_rate'] != stage_config(82, request['stage_id'])['attribute_factor'][2]:
            raise ValueError('古远虚影生命倍率不符合关卡配置')
        battle = user.get('battle_session')
        if not battle or battle['id'] != run.get('battle_id') or battle['common']['dest'] != request['stage_id']:
            raise ValueError('古远虚影战斗未开始')
    else:
        pending = run.get('pending') if run else None
        if not pending:
            if run and run.get('saved_battle_id') and run['stage_info_list'][0]['stage_id'] == request['stage_id']:
                return {'result': 0}
            raise ValueError('古远虚影没有可保存的战报')
        if pending['row']['stage_id'] != request['stage_id']:
            raise ValueError('古远虚影保存关卡与战报不一致')
        run['stage_info_list'] = [pending['row']] + run['stage_info_list']
        run['saved_battle_id'] = pending['battle_id']
        run['pending'] = None
        health = remaining_health(run)
        finished = all(health[b] == 0 for b in catalog['config']['heart_damon_id'])
        defeated = all(health[h] == 0 for h in run['hero_id_list'])
        if finished or defeated:
            total = sum(row['max_score'] for row in run['stage_info_list'])
            total += sum(health[h] for h in run['hero_id_list'])*catalog['score']['hpscore']//100
            best = state['best'].get(key, {})
            if total > best.get('score', 0):
                state['best'][key] = {'score': total, 'time': int(time.time()),
                    'heroes': list(run['hero_id_list']), 'stages': deepcopy(run['stage_info_list'])}
            run['enter_flag'] = False
            run['finished'] = finished
    return {'result': 0}


def update_demon_tasks(user: dict) -> None:
    """使用已保存的完整挑战事实恢复四项常驻奖励任务。"""
    from gameplay_data import client_catalog
    state = demon_state(user)
    for cfg in client_catalog()['tasks']:
        if cfg['type'] != 3007:
            continue
        params = cfg['additional_parameter']
        runs = state['runs']
        if cfg['condition'] == 11007:
            value = int(str(params[0]) in runs)
        elif cfg['condition'] == 11006:
            value = int(any(r.get('finished') and r['difficulty'] >= params[0] for r in runs.values()))
        elif cfg['condition'] == 11008:
            value = int(any(r['score'] >= params[0] for r in state['best'].values()))
        else:
            continue
        task = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        task['progress'] = max(task['progress'], min(cfg['need'], value))


def mult_heart_demon_pushes(user: dict | None = None) -> list[tuple]:
    """下发当前挑战、持久化最高分和有效刷新时间。"""
    edition = current_edition()
    state = demon_state(user if user is not None else {})
    refresh = int(time.time()) + 5 * 365 * 86400
    return [(64031, {'activity_id': edition, 'refresh_time': [refresh]}),
            (75017, {'open_edition': edition, 'challenge_stage': 0,
                     'info_list': [{key: run[key] for key in ('difficulty', 'hero_id_list', 'stage_info_list', 'enter_flag')}
                                   for run in state['runs'].values()],
                     'max_score_list': [{'difficulty': difficulty, 'max_score': state['best'].get(str(difficulty), {}).get('score', 0)}
                                        for difficulty in (1, 2)]})]
