"""常驻活动战斗完成后保存专用进度。"""

from resident_catalog import append_unique, resident_rows
from battle_rewards import drop_config, roll_drops
from resident_data import resident_state

RESIDENT_STAGE_CONFIGS = {602: 'BattleSkuldStageCfg', 603: 'BattleHellaPinballStageCfg',
    71: 'BattleWhackMoleStageCfg', 72: 'BattleActivityMonsterCosplayCfg', 73: 'BattleCatchDuckStageCfg', 74: 'BattleRhythmGameStageCfg',
    84: 'BattleSeaWarfareStageCfg', 85: 'BattleSandPlayStageCfg',
    86: 'BattleOsirisGameStageCfg', 87: 'BattleSummerPubStageCfg',
    89: 'BattleAshShootStageCfg', 92: 'BattleHodurStageCfg', 75: 'BattleChessBoardCfg'}


def resident_stage_id(kind: int, dest: int) -> int:
    """溯梦和料理请求发送玩法编号，映射为实际战斗配置编号。"""
    if kind == 84:
        return next((c['stage_id'] for c in resident_rows('SeaWarfareStageCfg') if c['id'] == dest), dest)
    if kind == 71:
        return next((c['stage_id'] for c in resident_rows('ActivityWhackMoleCfg') if c['id'] == dest), dest)
    for cfg in resident_rows('SkuldStageCfg' if kind == 602 else 'SummerPubLevelCfg'):
        if cfg['id'] == dest:
            if kind == 602:
                return cfg['type'][1] if cfg['type'][0] == 1 else 0
            return cfg['stage_id'][0] if cfg['type'] == 1 else 0
    return dest


def complete_resident_battle(user: dict, battle: dict, seconds: int, info: dict | None = None) -> list:
    """只在实际战斗获胜时记录通关，专用推送与普通战斗记录一致。"""
    kind = battle['common']['type']
    info = info or {}
    from resident_combat import combat_score
    score = combat_score(kind, seconds, info)
    rewards = []
    if kind not in RESIDENT_STAGE_CONFIGS:
        return rewards
    stage = battle['config']['id']
    state = resident_state(user)
    if kind in (84, 85):
        from resident_world import world_battle_complete
        rewards.extend(world_battle_complete(user, kind, stage))
    elif kind in (71, 72, 73, 75):
        from resident_board import board_battle_complete
        board_battle_complete(user, kind, stage)
    elif kind == 74:
        from resident_board import board_battle_complete
        board_battle_complete(user, kind, stage)
        music_id = battle['common'].get('dest') or stage
        cfg = next((r for r in resident_rows('ActivityMusicCfg') if r['id'] == music_id), None)
        if cfg:
            row = next((r for r in state['music']['challenge_info'] if r['id'] == music_id), None)
            if row is None:
                row = {'id': music_id, 'score': 0, 'sign': 0, 'seconds': 0}
                state['music']['challenge_info'].append(row)
            row['score'] = max(row['score'], score)
            row['sign'] = 1
            row['seconds'] = min(row.get('seconds', seconds) or seconds, seconds)
            state['music_last_stage'] = music_id
    elif kind == 602:
        from resident_skuld import validate_skuld_stage, complete_skuld_level
        rewards.extend(complete_skuld_level(user, validate_skuld_stage(user, stage)))
    elif kind == 603:
        from resident_pinball import complete_pinball_stage, validate_pinball_battle
        complete_pinball_stage(user, validate_pinball_battle(user, battle['common'], stage))
    elif kind == 86:
        from resident_combat import complete_tactical_records
        state['osiris_score_rule'] = 1
        complete_tactical_records(user, stage, info)
        rows = state['osiris']['pass_stages']
        row = next((r for r in rows if r['stage_id'] == stage), None)
        if row is None:
            rows.append({'stage_id': stage, 'pass_time': seconds, 'point': score})
        else:
            row['pass_time'] = min(row['pass_time'], seconds)
            row['point'] = max(row['point'], score)
        state['osiris_last_stage'] = stage
        state['osiris_current'] = {'stage_id': stage, 'pass_time': seconds, 'point': score}
        passed = {r['stage_id'] for r in rows}
        stages = resident_rows('ActivityGeneralityStageCfg')
        for chapter in {r['chapter_id'] for r in stages}:
            if all(r['stage_id'] in passed for r in stages if r['chapter_id'] == chapter):
                append_unique(state['osiris']['pass_chapters'], chapter)
    elif kind == 89:
        from resident_catalog import resident_config
        stage = resident_config('AshShootStageCfg', stage, 'stage_id')['id']
        rows = state['ash']['pass_stages']
        if not any(r['stage_id'] == stage for r in rows):
            rows.append({'stage_id': stage, 'point': score})
            rewards.extend(roll_drops(drop_config(battle['config'].get('drop_lib_id', 0)), 1))
        else:
            row = next(r for r in rows if r['stage_id'] == stage)
            row['point'] = max(row['point'], score)
        state['ash_last_stage'] = stage
        state['ash_current'] = {'stage_id': stage, 'point': score}
    elif kind == 87:
        from resident_pub import complete_pub_battle
        rewards.extend(complete_pub_battle(user, stage))
    elif kind == 92:
        from resident_hodur_battle import complete_hodur_battle
        complete_hodur_battle(user, battle, info)
    return rewards
