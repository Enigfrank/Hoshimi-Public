"""神经矩阵：按周期轮换四期并发序列、锁队、任务及明确的本地计分。"""

from copy import deepcopy
from functools import lru_cache
from math import log10

from .activity_periods import NEURAL_ACTIVITY_IDS, neural_activity
from .battle_rewards import grant_rewards
from .gameplay_protocol import read_client_data

NEURAL_COMMANDS = {89802, 89804, 89806}


def neural_catalog(activity_id: int | None = None) -> dict:
    """读取指定期次的关卡和奖励条件，不混入前三代或其他期次。"""
    return _neural_catalog(int(activity_id or neural_activity()))


@lru_cache(maxsize=4)
def _neural_catalog(activity_id: int) -> dict:
    return read_client_data(f'''(function()
        local activity=require('ActivityCfg')[{activity_id}]
        local stageActivity=activity.sub_activity_list[2]
        local stages,rewards,conditions={{}},{{}},{{}}
        local s=require('CoreVerificationClMode4Cfg')
        for _,id in ipairs(s.all) do
            if s[id].activity_id==stageActivity then stages[#stages+1]=s[id] end
        end
        local r=require('CoreVerificationClRewardCfg')
        for _,id in ipairs(r.all) do
            if r[id].activity_id==stageActivity and r[id].reward_type==1 then
                rewards[#rewards+1]=r[id]
                conditions[tostring(r[id].condition)]=require('ConditionCfg')[r[id].condition]
            end
        end
        return {{stages=stages,rewards=rewards,conditions=conditions}}
    end)()''')


def neural_state(user: dict) -> dict:
    """恢复本期状态；跨期时清空旧期进度，手动重置只递增局号。"""
    state = user.setdefault('neural_matrix', {})
    current = neural_activity()
    saved_activity = state.get('activity_id')
    # 旧存档没有期次字段，此前只可能是固定第 4 期；其余期次必须清空旧进度。
    if saved_activity is None:
        if state and current != NEURAL_ACTIVITY_IDS[-1]:
            state.clear()
    elif saved_activity != current:
        state.clear()
    state['activity_id'] = current
    for key, value in {'stages': {}, 'available': [], 'claimed': [], 'serial': 0,
                       'max_point': 0, 'last_score': 0, 'first_enter': False}.items():
        state.setdefault(key, value)
    return state


def neural_stage(stage_id: int) -> dict:
    """校验关卡属于当前开放的期次。"""
    row = next((row for row in neural_catalog()['stages'] if row['stage_id'] == stage_id), None)
    if row is None:
        raise ValueError('神经矩阵关卡不属于当前期次')
    return row


def neural_data(user: dict) -> dict:
    """只下发真实完成的锁队记录，空队伍不伪造成已通关。"""
    state = neural_state(user)
    catalog = neural_catalog(state['activity_id'])
    return {'common_stages': [
        {'stage_id': cfg['stage_id'], 'common_locks': [
            {'index': int(index), **deepcopy(row)}
            for index, row in sorted(state['stages'].get(str(cfg['stage_id']), {}).items(), key=lambda pair: int(pair[0]))]}
        for cfg in catalog['stages']],
        'finish_assignment_list': [{'assignment_id': row['task_id'],
            'state': 2 if row['task_id'] in state['claimed'] else int(row['task_id'] in state['available'])}
            for row in catalog['rewards']],
        'first_enter': state['first_enter'], 'max_point': state['max_point']}


def prepare_neural_battle(user: dict, request: dict) -> dict:
    """校验零起始队号、拥有角色、跨队锁定和当前期次允许的战斗选项。"""
    common = request['common_info']
    if common.get('activity_id') != neural_activity() or common.get('battle_times', 1) != 1:
        raise ValueError('神经矩阵期次或战斗倍率无效')
    cfg = neural_stage(common['dest'])
    opts = common.get('opts', [])
    teams = [row['value'] for row in opts if row['key'] == 2]
    if (len({row['key'] for row in opts}) != len(opts)
            or len(teams) != 1 or len(teams[0]) != 1 or not 0 <= teams[0][0] < cfg['stage_team']
            or any(row['key'] not in (1, 2) or row['key'] == 1 and row['value'] for row in opts)):
        raise ValueError('神经矩阵队伍索引或选项无效')
    index = teams[0][0]
    selections = [row for row in common['hero_list'] if row['hero_id']]
    heroes = [row['hero_id'] for row in selections]
    owned = {row['id'] for row in user.get('heroes', [])}
    if (not 1 <= len(heroes) <= 3 or len(common['hero_list']) > 3 or len(set(heroes)) != len(heroes)
            or not set(heroes) <= owned or any(row['hero_type'] != 1 for row in selections)):
        raise ValueError('神经矩阵仅允许一至三名不同的已拥有角色')
    state = neural_state(user)
    locked = {hero for stage, teams in state['stages'].items() for team, row in teams.items()
              if (stage, team) != (str(cfg['stage_id']), str(index)) for hero in row['hero_id_list']}
    if locked.intersection(heroes):
        raise ValueError('角色已被神经矩阵其他关卡或队伍锁定')
    state['first_enter'] = True
    return {'team_index': index, 'serial': state['serial'],
            'activity_id': state['activity_id']}


def validate_neural_settlement(user: dict, battle: dict) -> None:
    """手动重置或跨期后拒绝旧战斗，包括已经缓存过的旧结算。"""
    info = battle.get('neural')
    if (not info or info['serial'] != neural_state(user)['serial']
            or info.get('activity_id', neural_activity()) != neural_activity()):
        raise ValueError('神经矩阵挑战已重置或期次已变化，请重新进入战斗')


@lru_cache(maxsize=256)
def report_hero(identifier: int) -> int:
    """native 统计可使用皮肤编号，按原始配置还原实际角色归属。"""
    return read_client_data(f"(function() local c=require('SkinCfg')[{int(identifier)}]; return c and c.hero or {int(identifier)} end)()")


def neural_score(battle: dict) -> tuple[int, int, int]:
    """返回结算结果、积分和用时。挑战击杀首领后按总伤害计分。"""
    report = battle['reported']
    info = report['info']
    result = report['result']
    if result not in (1, 2, 3) or '2' not in info:
        raise ValueError('神经矩阵战报缺少有效结果或耗时')
    expected = {row['hero_id'] for row in battle['common']['hero_list'] if row['hero_id']}
    characters = [row for row in info.get('characters', []) if row.get('1', 0) or row.get('3', 0)]
    actual = [report_hero(row.get('1', 0)) for row in characters]
    if len(actual) != len(expected) or set(actual) != expected or any(row.get('3', 0) <= 0 for row in characters):
        raise ValueError('神经矩阵战报角色与出战队伍不一致')
    seconds = max(0, info['2']) // 1000
    cfg = neural_stage(battle['common']['dest'])
    if cfg['stage_type'] == 1:
        score = max(4000, 5060 - (seconds * seconds + 19) // 20) if result == 1 else 0
        return result, score, seconds
    if result == 3:
        return result, 0, seconds
    # BattleResultInfo.EnemyDeadNum 是 protobuf int32，缺省值为 0。
    if not 0 < info.get('11', 0) < 1 << 31:
        return 2, 0, seconds
    damage = 0.0
    for row in characters:
        amount = row.get('4', 0)  # CharResultInfo.Damage 是 protobuf int64。
        if 0 < amount < 1 << 63:
            damage += amount
    score = max(4000, int(16000 * log10(max(1, damage) / 1e8) - 3000))
    return 1, score, seconds


def settle_neural_battle(user: dict, battle: dict) -> int:
    """保存本次锁队与分数，并返回向客户端下发的战斗结果。"""
    validate_neural_settlement(user, battle)
    result, score, seconds = neural_score(battle)
    state = neural_state(user)
    catalog = neural_catalog(state['activity_id'])
    state['last_score'] = score
    if score:
        stage = str(battle['common']['dest'])
        index = str(battle['neural']['team_index'])
        state['stages'].setdefault(stage, {})[index] = {
            'hero_id_list': [row['hero_id'] for row in battle['common']['hero_list'] if row['hero_id']],
            'cur_value': score, 'seconds': seconds}
    challenge = sum(row['cur_value'] for cfg in catalog['stages'] if cfg['stage_type'] == 2
                    for row in state['stages'].get(str(cfg['stage_id']), {}).values())
    current = sum(row['cur_value'] for teams in state['stages'].values() for row in teams.values()) if challenge else 0
    state['max_point'] = max(state['max_point'], current)
    for row in catalog['rewards']:
        condition = catalog['conditions'][str(row['condition'])]
        teams = state['stages'].get(str(condition['params'][0]), {})
        kind = condition['type']
        achieved = bool(teams) if kind == 11304 else kind == 11307 and sum(team['cur_value'] for team in teams.values()) >= condition['params'][1]
        if achieved and row['task_id'] not in state['available']:
            state['available'].append(row['task_id'])
    return result


def neural_request(user: dict, command: int, request: dict) -> dict:
    """处理单队／单关／全部重置及原子领奖；重复与跨期请求一律拒绝。"""
    if request['activity_id'] != neural_activity():
        raise ValueError('神经矩阵期次无效')
    state = neural_state(user)
    state['first_enter'] = True
    catalog = neural_catalog(state['activity_id'])
    if command == 89804:
        identifiers = request['assignment_id']
        rewards = {row['task_id']: row for row in catalog['rewards']}
        if (not identifiers or len(set(identifiers)) != len(identifiers)
                or any(i not in rewards or i not in state['available'] or i in state['claimed'] for i in identifiers)):
            raise ValueError('神经矩阵任务未达成、已领取或不属于当前期次')
        gains = [{'id': item, 'num': count} for i in identifiers for item, count in rewards[i]['reward']]
        grant_rewards(user, gains)
        state['claimed'].extend(identifiers)
        return {'result': 0, 'reward_list': gains}
    stage = request['stage_id']
    if command == 89802:
        cfg = neural_stage(stage)
        if not 0 <= request['team_index'] < cfg['stage_team']:
            raise ValueError('神经矩阵队伍索引无效')
        state['stages'].get(str(stage), {}).pop(str(request['team_index']), None)
    elif command == 89806:
        if stage:
            neural_stage(stage)
            state['stages'].pop(str(stage), None)
        else:
            state['stages'] = {}
    else:
        raise ValueError('神经矩阵请求未实现')
    state['serial'] += 1
    return {'result': 0}
