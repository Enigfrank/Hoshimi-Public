"""卡达斯假日赛的客户端胜负语义、车体／武装解锁和累计成绩。"""

from functools import lru_cache

from gameplay_protocol import read_client_data
from resident_catalog import resident_config, resident_rows, append_unique


@lru_cache(maxsize=1)
def race_defaults() -> list:
    """客户端指定默认车体312和钻头106，不根据锁定标记猜默认配置。"""
    return read_client_data("require('GameSetting').summer_race_default_tank.value")


def race_unlocks(race: dict) -> tuple[set, set]:
    """标记0为默认开放；已完成关卡的1/2/3奖励分别解锁武器、配件和车体。"""
    bodies = {row['id'] for row in resident_rows('ActivityTankBodyCfg') if row['is_unlocked'] == 0}
    options = {row['id'] for row in resident_rows('ActivityTankWeaponCfg') if row['is_unlocked'] == 0}
    for cfg in resident_rows('ActivityTankCfg'):
        if cfg['id'] not in race['mission_list']:
            continue
        for kind, item in cfg['reward_list']:
            if kind == 3:
                bodies.add(item)
            elif kind in (1, 2):
                options.add(item)
    return bodies, options


def initialize_race(race: dict) -> None:
    """恢复合法已解锁配装；旧初始化错误选择的锁定车体改为客户端默认车体。"""
    bodies, options = race_unlocks(race)
    defaults = race_defaults()
    if race['tank_id'] not in bodies:
        race['tank_id'] = defaults[0][0]
        race['tank_info'] = [r for r in race['tank_info'] if r['tank_id'] in bodies]
    row = next((r for r in race['tank_info'] if r['tank_id'] == race['tank_id']), None)
    if row is None:
        race['tank_info'].append({'tank_id': race['tank_id'], 'weapon_list': list(defaults[1])})
    else:
        row['weapon_list'] = [item for item in row['weapon_list'] if item in options]


def race_data(race: dict) -> dict:
    """只下发客户端84331定义的字段，累计统计单独保存在账号中。"""
    return {key: race[key] for key in ('activity_id','mission_list','tank_id','tank_info','challenge_stage_info')}


def race_request(user: dict, command: int, request: dict) -> dict:
    """使用当前客户端的胜利0／失败1，首通奖励只解锁赛车配置。"""
    from resident_data import resident_state
    if request.get('activity_id', 4343601) != 4343601:
        raise ValueError('常驻活动编号不匹配')
    race = resident_state(user)['race']
    bodies, options = race_unlocks(race)
    if command == 84334:
        cfg = resident_config('ActivityTankBodyCfg', request['tank_id'])
        if cfg['id'] not in bodies:
            raise ValueError('赛车车体尚未解锁')
        ids = request['weapon_list']
        if len(ids) != len(set(ids)):
            raise ValueError('赛车配装项目重复')
        items = [resident_config('ActivityTankWeaponCfg', item) for item in ids]
        if any(row['id'] not in options for row in items) or sum(row['cost'] for row in items) > cfg['cost']:
            raise ValueError('赛车武装未解锁或超出容量')
        race['tank_id'] = cfg['id']
        race['tank_info'] = [r for r in race['tank_info'] if r['tank_id'] != cfg['id']] + [
            {'tank_id': cfg['id'], 'weapon_list': list(ids)}]
    elif command == 84332:
        cfg = resident_config('ActivityTankCfg', request['stage_id'])
        if cfg['main_activity_id'] != 4343601 or cfg['pre_stage'] and cfg['pre_stage'] not in race['mission_list']:
            raise ValueError('请先完成前置赛车关卡')
        if request['result'] not in (0, 1):
            raise ValueError('赛车结算结果无效')
        if request['result'] == 0:
            append_unique(race['mission_list'], cfg['id'])
        kills = request.get('kill_num', 0)
        if kills:
            row = next(r for r in race['tank_info'] if r['tank_id'] == race['tank_id'])
            totals = race.setdefault('equipped_kills', {})
            for item in row['weapon_list']:
                if resident_config('ActivityTankWeaponCfg', item)['item_type'] == 1:
                    totals[str(item)] = totals.get(str(item), 0) + kills
        if cfg['mode_type'] == 4:
            challenge = race['challenge_stage_info']
            challenge['round'] = max(challenge['round'], request.get('round', 0))
            challenge['max_score'] = max(challenge['max_score'], request.get('score', 0))
            race['challenge_total_score'] = race.get('challenge_total_score', 0) + request.get('score', 0)
    else:
        raise ValueError('未知赛车请求')
    return {'result': 0}
