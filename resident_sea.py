"""刺桐船战真实挑战前置、主动技能与固定船只队伍校验。"""

from resident_catalog import resident_config, resident_rows
from resident_world import world_refresh, world_sea_data


def validate_sea_battle(user: dict, common: dict, stage: int) -> None:
    """按客户端配置校验四挑战前置和已解锁技能，不给胜负玩法虚构积分。"""
    cfg = resident_config('SeaWarfareStageCfg', stage, 'stage_id')
    if common.get('activity_id', 0) not in (0, cfg['activity_id']) or common.get('battle_times', 1) != 1:
        raise ValueError('船战活动编号或倍率无效')
    state = world_refresh(user)
    completed = state['sea_stages']
    if cfg['pre_stage'] and cfg['pre_stage'] not in completed:
        raise ValueError('请先完成前置船战挑战')
    if cfg['type'] == 2:
        system = next(r for r in resident_rows('SandPlaySystemCfg') if r['tag_id'] == 11010601)
        done = {r['id'] for q in state['quests'].values() for r in q['task_list'] if r['status'] == 1}
        if system['task_id'] and system['task_id'] not in done:
            raise ValueError('船战挑战系统尚未解锁')
        if not common.get('index', 0):
            raise ValueError('请选择已解锁的船战主动技能')
    skill = common.get('index', 0)
    if skill:
        ability = resident_config('SeaWarfareSkillCfg', skill)
        if ability['type'] != 2 or skill not in world_sea_data(user)['data']['affix']:
            raise ValueError('船战主动技能尚未解锁或类型不匹配')
    fixed = resident_config('BattleSeaWarfareStageCfg', stage)['hero_list'][0][1]
    heroes = [h for h in common['hero_list'] if h['hero_id']]
    if len(heroes) != 1 or heroes[0]['hero_type'] != 2 or heroes[0]['hero_id'] != fixed:
        raise ValueError('船战必须使用配置的船只试用队伍')
