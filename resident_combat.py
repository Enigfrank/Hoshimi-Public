"""战术与列车的前置、明确标注的本地评分和活动领奖数据。"""

from functools import lru_cache

from currency_data import build_currency_balances
from gameplay_protocol import read_client_data
from resident_catalog import resident_config, resident_rows
from resident_data import resident_state

TACTICAL_TASK_RECORDS = {340004: 93, 340005: 94, 340006: 95,
                        340007: 96, 340008: 97, 340009: 98}


def initialize_combat_state(state: dict) -> None:
    """保留旧通关，纠正列车编号与耗时伪分；旧战术按保存的整秒作保守估分。"""
    mapping = {r['stage_id']: r['id'] for r in resident_rows('AshShootStageCfg')}
    rows = state['ash']['pass_stages']
    merged = {}
    for row in rows:
        if row['stage_id'] in mapping:
            # 旧实现把耗时写成列车积分，不能据此发放评分任务奖励。
            row['point'] = 0
        row['stage_id'] = mapping.get(row['stage_id'], row['stage_id'])
        previous = merged.get(row['stage_id'])
        if previous is None:
            merged[row['stage_id']] = row
        else:
            previous['point'] = max(previous['point'], row['point'])
    rows[:] = list(merged.values())
    if 'ash_last_stage' in state:
        state['ash_last_stage'] = mapping.get(state['ash_last_stage'], state['ash_last_stage'])
    if 'ash_current' in state:
        row = state['ash_current']
        if row['stage_id'] in mapping:
            row['point'] = 0
        row['stage_id'] = mapping.get(row['stage_id'], row['stage_id'])
    tactical = state.get('osiris', {}).get('pass_stages', [])
    if tactical and not state.get('osiris_score_rule'):
        for row in tactical:
            # 原始毫秒已丢失；整秒区间的上界避免虚增任务分数，重玩恢复精确成绩。
            row['point'] = combat_score(86, row['pass_time'], {'2': row['pass_time'] * 1000 + 999})
        if 'osiris_current' in state:
            row = state['osiris_current']
            row['point'] = combat_score(86, row['pass_time'], {'2': row['pass_time'] * 1000 + 999})
        state['osiris_score_rule'] = 1


def combat_records(info: dict) -> dict[int, int]:
    """配对原生14／15统计数组，拒绝重复编号、缺项和负数，不拼凑跨局成绩。"""
    ids, numbers = info.get('14', []), info.get('15', [])
    if len(ids) != len(numbers) or len(ids) != len(set(ids)):
        raise ValueError('战术或列车战报统计编号与数量不匹配')
    if any(not 0 <= value <= 2147483647 for value in ids + numbers):
        raise ValueError('战术或列车战报统计值无效')
    return dict(zip(ids, numbers))


def complete_tactical_records(user: dict, stage: int, info: dict) -> None:
    """按单次胜利保存各统计最高值；93～98映射来自本机battle_record_data_type。"""
    records = combat_records(info)
    saved = resident_state(user).setdefault('osiris_records', {}).setdefault(str(stage), {})
    for record in TACTICAL_TASK_RECORDS.values():
        if record in records:
            saved[str(record)] = max(saved.get(str(record), 0), records[record])


def validate_combat_stage(user: dict, common: dict, stage: int) -> None:
    """使用客户端的实际关卡前置，禁止跨活动进入或倍数结算。"""
    kind = common['type']
    table = 'ActivityGeneralityStageCfg' if kind == 86 else 'AshShootStageCfg'
    cfg = resident_config(table, stage, 'stage_id')
    if common.get('activity_id', 0) not in (0, cfg['activity_id']):
        raise ValueError('战术或列车活动编号不匹配')
    if common.get('battle_times', 1) != 1:
        raise ValueError('战术和列车只允许单局结算')
    key = 'osiris' if kind == 86 else 'ash'
    previous = cfg['pre_stage'] if kind == 86 else cfg['front_unlock_id']
    passed = {r['stage_id'] for r in resident_state(user)[key]['pass_stages']}
    if previous and previous not in passed:
        raise ValueError('请先完成战术或列车前置关卡')


def validate_train_momotalk(user: dict, story: int) -> None:
    """列车专用通讯必须达到配置中的成长值，其他活动通讯由原路由校验。"""
    cfg = next((r for r in resident_rows('AshShootUnlockItemCfg')
                if r['type'] == 3 and r['correlation_id'] == story), None)
    if cfg and build_currency_balances(user).get(79, 0) < cfg['unlock_num']:
        raise ValueError('列车通讯所需影片进程不足')


@lru_cache(maxsize=1)
def tactical_score_settings() -> tuple[int, int, int]:
    """读取本机基础分、参考耗时毫秒与系数，不声称恢复了官方公式。"""
    return tuple(read_client_data("(function()local c=require('GameSetting');return "
        "{c.activity_generality_base_point.value[1],c.activity_generality_base_time.value[1],"
        "c.activity_generality_base_factor.value[1]} end)()"))


def combat_score(kind: int, seconds: int, info: dict) -> int:
    """战术采用明确的本地耗时公式；列车使用客户端原生105号积分。"""
    records = combat_records(info) if kind in (86, 89) else {}
    if 'score' in info:
        return max(0, int(info['score']))
    if kind == 86:
        base, reference, factor = tactical_score_settings()
        elapsed = max(0, int(info.get('2', seconds * 1000)))
        return max(0, base + (reference - elapsed) * factor)
    if kind == 89:
        return records.get(105, 0)
    return 0


def combat_point_data(user: dict, activity: int) -> dict:
    """恢复60201的战术研习完成数、列车成长值与对应已领取奖励。"""
    state = resident_state(user)
    if activity == 323801:
        normal = {r['stage_id'] for r in resident_rows('ActivityGeneralityStageCfg') if not r['rank_activity']}
        point = sum(r['stage_id'] in normal for r in state['osiris']['pass_stages'])
    else:
        point = build_currency_balances(user).get(79, 0)
    rewards = {r['id'] for r in resident_rows('ActivityPointRewardCfg') if r['activity_id'] == activity}
    return {'activity_id': activity, 'point': point,
            'reward_list': [i for i in state['point_rewards'] if i in rewards]}
