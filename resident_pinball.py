"""远村异闻的弹珠成绩、累计任务、原生boss与主动技能恢复。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_config, resident_rows
from resident_data import resident_state

PINBALL_ACTIVITY = 3840801


def pinball_data(pinball: dict) -> dict:
    """只下发89401的公开字段，分类累计伤害和分数保留在存档中。"""
    return {key: deepcopy(pinball[key]) for key in
            ('hai_la_level', 'stage_list', 'skills', 'select_active_skills', 'challenge_score')}


def complete_pinball_stage(user: dict, cfg: dict) -> list:
    """普通与原生boss共用首通进度、角色等级及技能解锁，不重复发奖。"""
    pinball = resident_state(user)['pinball']
    if not append_unique(pinball['stage_list'], cfg['id']):
        return []
    pinball['hai_la_level'] = max(pinball['hai_la_level'], cfg['lv_up'] or 1)
    if cfg['skill_unlock']:
        append_unique(pinball['skills'], cfg['skill_unlock'])
    from resident_minigames import reward_pairs
    return reward_pairs(user, cfg['first_reward'])


def initialize_pinball(user: dict, pinball: dict) -> None:
    """恢复旧原生boss通关的进度，并纠正旧的三槽或被动技能配装。"""
    for cfg in resident_rows('PinballStageCfg'):
        if cfg['activity_id'] != PINBALL_ACTIVITY or cfg['barrier_type'] != 2:
            continue
        progress = user.get('battle_progress', {}).get(f"603:{cfg['barrier_id']}", {})
        if cfg['id'] not in pinball['stage_list'] and progress.get('clear_times', 0) > 0 and (
                not cfg['pre_stage'] or cfg['pre_stage'] in pinball['stage_list']):
            pinball['stage_list'].append(cfg['id'])
            pinball['hai_la_level'] = max(pinball['hai_la_level'], cfg['lv_up'] or 1)
            if cfg['skill_unlock']:
                append_unique(pinball['skills'], cfg['skill_unlock'])
    active = {r['id'] for r in resident_rows('PinballSkillCfg') if r['activity_id'] == PINBALL_ACTIVITY and r['skill_type'] == 1}
    selected = []
    for item in pinball['select_active_skills']:
        if item in active and item in pinball['skills'] and item not in selected:
            selected.append(item)
    pinball['select_active_skills'] = selected[:2]


def validate_pinball_battle(user: dict, common: dict, stage: int) -> dict:
    """第5章必须通过实际原生boss战斗，校验前置和固定单人试用模板。"""
    cfg = resident_config('PinballStageCfg', stage, 'barrier_id')
    if cfg['activity_id'] != PINBALL_ACTIVITY or cfg['barrier_type'] != 2:
        raise ValueError('不是远村异闻原生boss关卡')
    if common.get('activity_id', 0) not in (0, PINBALL_ACTIVITY) or common.get('battle_times', 1) != 1:
        raise ValueError('远村活动编号或倍率无效')
    pinball = resident_state(user)['pinball']
    if cfg['pre_stage'] and cfg['pre_stage'] not in pinball['stage_list']:
        raise ValueError('请先完成远村前置章节')
    fixed = resident_config('BattleHellaPinballStageCfg', stage)['hero_list'][0][1]
    selected = [h for h in common['hero_list'] if h['hero_id']]
    if len(selected) != 1 or selected[0]['hero_type'] != 2 or selected[0]['hero_id'] != fixed:
        raise ValueError('远村boss必须使用配置的单人试用队伍')
    return cfg


def pinball_request(user: dict, command: int, request: dict) -> dict:
    """按配置分类累计成绩；89402只用于弹珠，原生boss不能伪造小游戏完成。"""
    pinball = resident_state(user)['pinball']
    if command == 89404:
        if request['activity_id'] != PINBALL_ACTIVITY:
            raise ValueError('远村活动编号不匹配')
        skills = request['skill_list']
        if len(skills) > 2 or len(skills) != len(set(skills)):
            raise ValueError('远村只能装备两个不同的主动技能')
        for item in skills:
            cfg = resident_config('PinballSkillCfg', item)
            if cfg['activity_id'] != PINBALL_ACTIVITY or cfg['skill_type'] != 1 or item not in pinball['skills']:
                raise ValueError('远村主动技能尚未解锁或类型不匹配')
        pinball['select_active_skills'] = list(skills)
    elif command == 89402:
        cfg = resident_config('PinballStageCfg', request['stage_id'])
        if cfg['activity_id'] != PINBALL_ACTIVITY or cfg['barrier_type'] not in (1, 3):
            raise ValueError('该关卡不能使用远村弹珠结算')
        if cfg['pre_stage'] and cfg['pre_stage'] not in pinball['stage_list']:
            raise ValueError('弹珠关卡尚未开放')
        if cfg['barrier_type'] == 3 and any(r['id'] not in pinball['stage_list'] for r in resident_rows('PinballStageCfg')
                if r['activity_id'] == PINBALL_ACTIVITY and r['barrier_type'] != 3):
            raise ValueError('请先完成远村十二个剧情章节')
        for field, name in (('harm', 'harm_totals'), ('score', 'score_totals')):
            totals = pinball.setdefault(name, {})
            key = str(cfg['barrier_type'])
            totals[key] = totals.get(key, 0) + request[field]
        complete_pinball_stage(user, cfg)
        if cfg['barrier_type'] == 3:
            pinball['challenge_score'] = max(pinball['challenge_score'], request['score'])
    else:
        raise ValueError('未知远村异闻请求')
    return {'result': 0}
