"""决斗王棋子技能描述与经验等级同步，不预发未选择的管理员技能。"""

from resident_catalog import resident_config


def buff_record(uid: int, buff_id: int, owner: dict) -> dict:
    """生成原始chess_buff要求的来源与归属，动画使用实例编号定位效果。"""
    resident_config('AutoChessBuffCfg', buff_id)
    return {'unique_id': uid, 'buff_id': buff_id, 'owner_type': 0, 'source_type': 0,
        'source_uid': owner['unique_id'], 'source_cfg_id': owner['chess_id'], 'other_param': []}


def chess_exp_thresholds(chess: dict, adjustment: int = 0) -> list:
    """遵循原始CalculateLevel的逐级经验折减，而非只改最终一级门槛。"""
    rows = resident_config('AutoChessCfg',chess['chess_id'])['exp']
    return [threshold+adjustment-index if adjustment else threshold for index,threshold in enumerate(rows)]


def chess_prop_count(chess: dict) -> int:
    """从特殊技能的实际增量还原已使用道具次数，升级时保留同一实例的成长。"""
    count = 0
    for buff in chess['buff_list']:
        cfg = resident_config('AutoChessBuffCfg',buff['buff_id'])
        if cfg['action_type'] == 36:
            for key in (1,2):
                amount = next((r['value'] for r in buff['other_param'] if r['key'] == key),0)
                factor = cfg['param'][3][key-1]
                if factor:count = max(count,amount//factor)
    return count


def sync_prop_growth(chess: dict, count: int) -> None:
    """按当前等级技能保存道具成长，使战斗数值与原始Lua提示保持一致。"""
    for buff in chess['buff_list']:
        cfg = resident_config('AutoChessBuffCfg',buff['buff_id'])
        if cfg['action_type'] == 36:
            buff['other_param'] = [{'key':key,'value':count*cfg['param'][3][key-1]} for key in (1,2)]


def sync_chess_buffs(chess: dict, adjustment: int = 0) -> None:
    """按真实经验阈值替换棋子等级技能，保留道具与管理员所选技能。"""
    cfg = resident_config('AutoChessCfg', chess['chess_id'])
    levels = cfg['level_buffs']
    if not levels:
        return
    props = chess_prop_count(chess)
    if cfg['type'] == 1:
        wanted = levels[2]
        native_ids = set(wanted)
    else:
        exp = next((r['value'] for r in chess['chess_attr_list'] if r['key'] == 3), 1)
        index = min(len(levels)-1, sum(exp >= threshold for threshold in chess_exp_thresholds(chess,adjustment)))
        wanted = levels[index]
        native_ids = {i for row in levels for i in row}
    required = {buff_id:sum(value == buff_id for value in wanted) for buff_id in set(wanted)}
    counts = {}
    current = []
    for buff in chess['buff_list']:
        buff_id = buff['buff_id']
        if buff_id not in native_ids:
            current.append(buff)
        elif counts.get(buff_id,0) < required.get(buff_id,0):
            current.append(buff)
            counts[buff_id] = counts.get(buff_id,0)+1
    for i, buff_id in enumerate(wanted, 1):
        if counts.get(buff_id,0) < required[buff_id]:
            uid = chess['unique_id']*100+i
            while any(r['unique_id'] == uid for r in current):uid += 1
            current.append(buff_record(uid, buff_id, chess))
            counts[buff_id] = counts.get(buff_id,0)+1
    chess['buff_list'] = current
    if props:sync_prop_growth(chess,props)
