"""极限机装的真实关卡、图鉴和每局累计统计。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_config, resident_rows
from resident_data import resident_state

VEHICLE_ACTIVITY = 4243101


def vehicle_data(vehicle: dict) -> dict:
    """只下发89701定义的三个字段，累计计数保留在存档内部。"""
    return {key: deepcopy(vehicle[key]) for key in ('stages', 'using_vehicle', 'unlock_buffs')}


def initialize_vehicle(vehicle: dict) -> None:
    """移除客户端测试关卡的展示影响，恢复合法的已解锁车体选择。"""
    stages = {r['id'] for r in resident_rows('MergeBallStageCfg') if r['activity_id'] == VEHICLE_ACTIVITY}
    vehicle['stages'][:] = [s for s in vehicle['stages'] if s in stages]
    choices = [r for r in resident_rows('MergeBallVehicleCfg') if r['type'] == 1
               and (not r['unlock_stage'] or r['unlock_stage'] in vehicle['stages'])]
    if not any(r['id'] == vehicle['using_vehicle'] for r in choices):
        vehicle['using_vehicle'] = choices[0]['id']


def vehicle_request(user: dict, command: int, request: dict) -> dict:
    """接收客户端kv键1击杀／键2改造，失败也累计实际操作；领奖只发一次。"""
    if request['activity_id'] != VEHICLE_ACTIVITY:
        raise ValueError('极限机装活动编号不匹配')
    vehicle = resident_state(user)['vehicle']
    response = {'result': 0}
    if command == 89702:
        cfg = resident_config('MergeBallStageCfg', request['stage_id'])
        rows = [r for r in resident_rows('MergeBallStageCfg') if r['activity_id'] == VEHICLE_ACTIVITY]
        if cfg['activity_id'] != VEHICLE_ACTIVITY:
            raise ValueError('不是当前极限机装活动关卡')
        index = next(i for i, r in enumerate(rows) if r['id'] == cfg['id'])
        if index and rows[index-1]['id'] not in vehicle['stages']:
            raise ValueError('请先完成前置机装关卡')
        if request['is_pass'] not in (0, 1):
            raise ValueError('机装结算结果无效')
        counters = {}
        for item in request['kv_list']:
            if item['key'] not in (1, 2) or item['key'] in counters:
                raise ValueError('机装统计类型无效或重复')
            counters[item['key']] = item['value']
        buffs = request['buffs']
        if len(buffs) != len(set(buffs)):
            raise ValueError('机装图鉴列表重复')
        for item in buffs:
            resident_config('MergeBallBuffCfg', item)
        vehicle['total_kills'] = vehicle.get('total_kills', 0) + counters.get(1, 0)
        vehicle['total_merges'] = vehicle.get('total_merges', 0) + counters.get(2, 0)
        for item in buffs:
            if not any(r['key'] == item for r in vehicle['unlock_buffs']):
                vehicle['unlock_buffs'].append({'key': item, 'value': 0})
        if request['is_pass']:
            append_unique(vehicle['stages'], cfg['id'])
    elif command == 89704:
        cfg = resident_config('MergeBallVehicleCfg', request['vehicle'])
        if cfg['type'] != 1 or cfg['unlock_stage'] and cfg['unlock_stage'] not in vehicle['stages']:
            raise ValueError('请选择已解锁的机装车体')
        vehicle['using_vehicle'] = cfg['id']
    elif command in (89706, 89708):
        cfg = resident_config('MergeBallBuffCfg', request['buff_id'])
        row = next((r for r in vehicle['unlock_buffs'] if r['key'] == cfg['id']), None)
        if command == 89708:
            if row is None:
                vehicle['unlock_buffs'].append({'key': cfg['id'], 'value': 0})
        else:
            if len(cfg['reward']) != 2 or row is None or row['value'] != 0:
                raise ValueError('图鉴没有奖励、未解锁或已领取')
            from resident_minigames import reward_pairs
            response['reward_list'] = reward_pairs(user, [cfg['reward']])
            row['value'] = 1
    else:
        raise ValueError('未知极限机装请求')
    return response
