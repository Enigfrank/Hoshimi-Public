"""溯梦之隙的真实前置链、重复游玩次数与首通奖励。"""

from resident_catalog import resident_config, resident_rows
from resident_data import resident_state
from battle_rewards import drop_config, roll_drops


def validate_skuld_level(user: dict, level: int) -> dict:
    """按原始SkuldSystemData要求全部pre_id实际完成，不能仅依据记录存在解锁。"""
    cfg = resident_config('SkuldStageCfg', level)
    passed = {r['id'] for r in resident_state(user)['skuld']['mission_list'] if r['times'] > 0}
    if any(previous not in passed for previous in cfg['pre_id']):
        raise ValueError('请先完成溯梦前置关卡')
    return cfg


def validate_skuld_stage(user: dict, stage: int) -> dict:
    """剧情／原生战斗只接受type=1真实映射，不能使用拼图的剧情占位配置。"""
    cfg = next((r for r in resident_rows('SkuldStageCfg') if r['type'] == [1, stage]), None)
    if cfg is None:
        raise ValueError('不是溯梦实际剧情或战斗关卡')
    return validate_skuld_level(user, cfg['id'])


def complete_skuld_level(user: dict, cfg: dict) -> list:
    """每次合法完成累计times；配置奖励仅在首次完成时返回。"""
    rows = resident_state(user)['skuld']['mission_list']
    row = next((r for r in rows if r['id'] == cfg['id']), None)
    if row is None:
        rows.append({'id': cfg['id'], 'times': 1})
        return sorted(roll_drops(drop_config(cfg['reward']), 1), key=lambda r: r['id'])
    row['times'] += 1
    return []
