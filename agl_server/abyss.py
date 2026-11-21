"""失序深阱的层级、首领战果、编队占用与重置状态。"""

from copy import deepcopy
from functools import lru_cache
import time

from .battle_progress import progress
from .battle_rewards import grant_rewards
from .gameplay_protocol import read_client_data
from .reserve_data import save_reserve
from .server_clock import business_day


@lru_cache(maxsize=1)
def abyss_catalog() -> dict:
    """读取当前深阱主题及首领分阶段血量、计分和重置配置。"""
    return read_client_data('''(function()
        local c=require('AbyssCfg'); local hp=require('AbyssBossHpCfg'); local g=require('GameSetting')
        local r={layers={},hp={},layer_limit=g.abyss_layer_reset_time.value[1],
            stage_limit=g.abyss_stage_reset_time.value[1],recover=g.abyss_daily_reset_recover.value[1],
            time_score=g.abyss_time_score.value}
        for _,id in ipairs(c.get_id_list_by_activity_id[c[c.all[1]].activity_id]) do
            r.layers[#r.layers+1]=c[id]
            for _,s in ipairs(c[id].stage_list) do
                if s[1]==3 then r.hp[tostring(s[2])]=hp[s[2]].boss_hp end
            end
        end
        return r
    end)()''')


def abyss_state(user: dict, now: int | None = None) -> dict:
    """迁移旧通关记录，保留历史最高层，并按业务日恢复有限重置次数。"""
    cfg = abyss_catalog()
    state = user.setdefault('abyss', {})
    completed = [c['level'] for c in cfg['layers']
                 if all(progress(user, 1003, s[1]).get('clear_times') for s in c['stage_list'])]
    state['history_max_layer'] = max([state.get('history_max_layer', 0)] + completed + user.get('abyss_rewards', []))
    state.setdefault('bosses', {})
    state.setdefault('serial', 0)
    day = business_day(int(time.time()) if now is None else now)
    elapsed = max(0, day - state.get('day', day))
    for field, limit in (('layer_reset_time', cfg['layer_limit']), ('stage_reset_time', cfg['stage_limit'])):
        state[field] = min(limit, state.get(field, limit) + elapsed * cfg['recover'])
    state['day'] = day
    return state


def _unlocked_layer(user: dict) -> int:
    """开发账号保留全解锁；普通账号逐层推进，重置不会伪造历史通关。"""
    layers = abyss_catalog()['layers']
    if user.get('account_template') != 'normal':
        return len(layers)
    unlocked = 1
    for cfg in layers:
        if not all(progress(user, 1003, s[1]).get('clear_times') for s in cfg['stage_list']):
            break
        unlocked = min(len(layers), cfg['level'] + 1)
    return unlocked


def _stage(stage_id: int) -> tuple[dict, list]:
    """仅接受当前主题中的真实关卡编号。"""
    for layer in abyss_catalog()['layers']:
        for stage in layer['stage_list']:
            if stage[1] == stage_id:
                return layer, stage
    raise ValueError('深阱关卡不存在')


def _boss(user: dict, stage: list) -> dict:
    """旧存档首领从第一阶段恢复，协议血量统一使用千分比。"""
    saved = abyss_state(user)['bosses'].get(str(stage[1]))
    if saved is not None:
        return deepcopy(saved)
    cleared = bool(progress(user, 1003, stage[1]).get('clear_times'))
    return {'stage_id': stage[1], 'is_completed': cleared, 'score': stage[2] if cleared else 0,
            'phase': len(abyss_catalog()['hp'][str(stage[1])]) if cleared else 1,
            'boss_hp_rate': 0 if cleared else 1000, 'teams': []}


def abyss_data(user: dict) -> dict:
    """下发已解锁层及完整首领字段，重置次数和历史奖励与存档一致。"""
    state = abyss_state(user)
    layers = []
    for cfg in abyss_catalog()['layers'][:_unlocked_layer(user)]:
        normal, bosses = [], []
        for stage in cfg['stage_list']:
            if stage[0] == 3:
                boss = _boss(user, stage)
                bosses.append({k: boss[k] for k in ('stage_id', 'is_completed', 'score', 'phase', 'boss_hp_rate')})
            else:
                normal.append({'stage_id': stage[1],
                               'is_completed': bool(progress(user, 1003, stage[1]).get('clear_times'))})
        layers.append({'layer_id': cfg['level'], 'stage_info_list': normal, 'boss_stage_info_list': bosses})
    return {'activity_id': abyss_catalog()['layers'][0]['activity_id'], 'is_back': state.get('is_back', False),
            'history_max_layer': state['history_max_layer'], 'layer_reset_time': state['layer_reset_time'],
            'stage_reset_time': state['stage_reset_time'],
            'last_version_max_unlock_layer': 1 if user.get('account_template') == 'normal' else len(layers),
            'refresh_timestamp': int(time.time()) + 7 * 86400,
            'layer_info_list': layers, 'got_layer_reward_list': list(user.get('abyss_rewards', []))}


def prepare_abyss_battle(user: dict, common: dict) -> dict:
    """校验层级和角色；重新开战放弃未确认战果，避免断线后无法继续。"""
    layer, stage = _stage(common['dest'])
    state = abyss_state(user)
    if layer['level'] > _unlocked_layer(user):
        raise ValueError('深阱层级尚未解锁')
    if common.get('battle_times', 1) not in (0, 1):
        raise ValueError('深阱不支持多倍战斗')
    if progress(user, 1003, stage[1]).get('clear_times'):
        raise ValueError('深阱关卡已通关，请先重置')
    heroes = [h['hero_id'] for h in common['hero_list'] if h['hero_id']]
    if not heroes or len(heroes) > 3 or len(set(heroes)) != len(heroes):
        raise ValueError('深阱队伍无效')
    banned = set()
    for cfg in abyss_catalog()['layers']:
        for other in cfg['stage_list']:
            saved = progress(user, 1003, other[1])
            if cfg['level'] == layer['level'] and saved.get('clear_times'):
                banned.update(saved.get('last_heroes', []))
            if other[0] == 3:
                teams = _boss(user, other)['teams']
                for team in (teams[:-1] if saved.get('clear_times') else teams):
                    banned.update(h['hero_id'] for h in team['hero_list'] if h['hero_id'])
    if set(heroes) & banned:
        raise ValueError('深阱角色已被其他战果占用')
    pending = state.pop('pending', None)
    if pending:
        _, previous = _stage(pending['stage_id'])
        _sync_teams(user, previous[1], _boss(user, previous)['teams'])
        state.pop('last_choice', None)
    return {'serial': state['serial'], 'layer': layer['level'], 'stage': deepcopy(stage)}


def _sync_teams(user: dict, stage_id: int, teams: list) -> None:
    """替换深阱指定容器，放弃战果或重置后不残留试战队伍。"""
    save_reserve(user, 63002, {'team_type': 104, 'cont_id': stage_id})
    if teams:
        save_reserve(user, 63000, {'team_type': 104,
                     'cont_team': {'cont_id': stage_id, 'teams': deepcopy(teams)}})


def settle_abyss_battle(user: dict, battle: dict, seconds: int) -> None:
    """累计保存队伍的用时；胜利提交，失败等待 55012 保存或放弃。"""
    state = abyss_state(user)
    stage = battle['abyss']['stage']
    common = battle['common']
    win = battle['reported']['result'] == 1
    team = {'team_index': 0, 'hero_list': deepcopy(common['hero_list']),
            'cooperate_unique_skill_id': common.get('cooperate_unique_skill_id', 0),
            'mimir_info': deepcopy((common.get('mimir_info') or [{}])[0])}
    if stage[0] != 3:
        if win:
            _sync_teams(user, stage[1], [team])
        return
    boss = _boss(user, stage)
    hp = abyss_catalog()['hp'][str(stage[1])]
    info = battle['reported']['info']
    records = dict(zip(info.get('14', []), info.get('15', [])))
    phase = max(boss['phase'], min(len(hp), records.get(36, boss['phase'])))
    rate = max(0, min(1000, (max(0, records.get(37, hp[phase - 1])) * 1000 + hp[phase - 1] - 1) // hp[phase - 1]))
    if phase == boss['phase']:
        rate = min(rate, boss['boss_hp_rate'])
    boss.update({'phase': len(hp) if win else phase, 'boss_hp_rate': 0 if win else rate, 'is_completed': win})
    boss['use_time'] = boss.get('use_time', 0) + seconds
    team['team_index'] = len(boss['teams']) + 1
    boss['teams'].append(team)
    if win:
        base, decay = abyss_catalog()['time_score']
        boss['score'] = stage[2] + max(0, base - boss['use_time'] * decay)
        state['bosses'][str(stage[1])] = boss
        _sync_teams(user, stage[1], boss['teams'])
    else:
        state.pop('last_choice', None)
        state['pending'] = {'battle_id': battle['id'], 'stage_id': stage[1], 'boss': boss}
    abyss_state(user)


def validate_abyss_settlement(user: dict, battle: dict) -> None:
    """重置后的旧战斗与缺少路线信息的旧会话不得重新写回进度。"""
    snapshot = battle.get('abyss')
    if not snapshot or snapshot['serial'] != abyss_state(user)['serial']:
        raise ValueError('深阱路线已重置，请重新进入关卡')


def abyss_request(user: dict, command: int, request: dict) -> dict:
    """处理有限重置、历史层奖励、首领战果选择与回归提示确认。"""
    state = abyss_state(user)
    if command == 55020:
        state['is_back'] = False
    elif command == 55006:
        ids = request['layer_list']
        claimed = user.get('abyss_rewards', [])
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('深阱领奖列表无效')
        configs = [next((c for c in abyss_catalog()['layers'] if c['level'] == i), None) for i in ids]
        if any(c is None or c['level'] > state['history_max_layer'] or c['level'] in claimed for c in configs):
            raise ValueError('深阱未通关或已领取')
        grant_rewards(user, [{'id': i, 'num': n} for c in configs for i, n in c['reward_list']])
        user['abyss_rewards'] = claimed + ids
    elif command == 55012:
        _, stage = _stage(request['stage_id'])
        pending = state.get('pending')
        if not pending or pending['stage_id'] != stage[1]:
            if state.get('last_choice') == request:
                return {'result': 0}
            raise ValueError('没有可选择的深阱首领战果')
        if request['is_save']:
            state['bosses'][str(stage[1])] = pending['boss']
        _sync_teams(user, stage[1], _boss(user, stage)['teams'])
        state.pop('pending')
        state['last_choice'] = deepcopy(request)
    elif command in (55002, 55004):
        if command == 55002:
            layer = next((c for c in abyss_catalog()['layers'] if c['level'] == request['layer']), None)
            selected = layer['stage_list'] if layer else []
        else:
            layer, stage = _stage(request['stage_id'])
            selected = [stage]
        if not layer or layer['level'] > _unlocked_layer(user):
            raise ValueError('深阱层级不存在或尚未解锁')
        if any(c['level'] > layer['level'] and any(
                progress(user, 1003, s[1]).get('clear_times') or
                (s[0] == 3 and _boss(user, s)['teams']) for s in c['stage_list'])
               for c in abyss_catalog()['layers']):
            raise ValueError('深阱更高阶层已有战果，无法重置当前阶层或关卡')
        field = 'layer_reset_time' if command == 55002 else 'stage_reset_time'
        if state[field] <= 0:
            raise ValueError('深阱重置次数已用完')
        if state.get('pending'):
            _, pending_stage = _stage(state['pending']['stage_id'])
            _sync_teams(user, pending_stage[1], _boss(user, pending_stage)['teams'])
        baseline = 1 if user.get('account_template') == 'normal' else len(abyss_catalog()['layers'])
        upper = [s for c in abyss_catalog()['layers'] if c['level'] > max(layer['level'], baseline)
                 for s in c['stage_list']]
        for stage in selected + upper:
            user.get('battle_progress', {}).pop(f'1003:{stage[1]}', None)
            state['bosses'].pop(str(stage[1]), None)
            _sync_teams(user, stage[1], [])
        state[field] -= 1
        state['serial'] += 1
        state.pop('pending', None)
        state.pop('last_choice', None)
    return {'result': 0}
