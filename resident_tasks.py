"""依据已保存的常驻事实恢复任务进度，重放请求不累计奖励。"""

from resident_catalog import resident_config, resident_rows
from resident_rogue import rogue_state


def update_board_tasks(user: dict) -> None:
    """棋盘章节和内部任务只根据实际完成事实推进对应活动任务。"""
    from gameplay_data import client_catalog
    from resident_board_data import board_state
    state = board_state(user)
    tasks = user.setdefault('tasks', {})
    finished = {i for run in state['runs'].values() for i in run['finished_tasks']}
    for cfg in client_catalog()['tasks']:
        kind, params = cfg['condition'], cfg['additional_parameter']
        if kind == 163001:
            value = int(params[0] in state['cleared'])
        elif kind == 163003:
            value = int(params[0] in finished)
        else:
            continue
        row = tasks.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        row['progress'] = max(row['progress'], min(cfg['need'], value))


def update_resident_tasks(user: dict) -> None:
    """从已保存的常驻成绩和通关记录恢复任务，不预发未完成任务。"""
    from gameplay_data import client_catalog
    from resident_data import resident_state
    from currency_data import build_currency_balances
    from resident_minigames import pub_level_complete
    state = resident_state(user)
    tasks = user.setdefault('tasks', {})
    relevant = set()
    balances = build_currency_balances(user)
    for cfg in client_catalog()['tasks']:
        kind, params, value = cfg['condition'], cfg['additional_parameter'], None
        if kind == 300:
            if params:
                value = balances.get(params[0], user.get('materials', {}).get(str(params[0]), 0))
        elif kind == 329001:
            value = int(any(r['id'] == params[0] and r['times'] > 0 for r in state['skuld']['mission_list']))
        elif kind == 392001:
            value = int(params[0] in state['pinball']['stage_list'])
        elif kind == 392003:
            value = int(state['pinball']['challenge_score'] >= params[1])
        elif kind == 392002:
            value = state['pinball'].get('harm_totals', {}).get(str(params[0]), 0)
        elif kind == 392004:
            value = state['pinball'].get('score_totals', {}).get(str(params[0]), 0)
        elif kind == 422001:
            value = int(params[0] in state['vehicle']['stages'])
        elif kind == 422002:
            rewards = {r['id'] for r in resident_rows('MergeBallBuffCfg') if len(r['reward']) == 2}
            value = sum(r['key'] in rewards for r in state['vehicle']['unlock_buffs'])
        elif kind == 422003:
            value = state['vehicle'].get('total_merges', 0)
        elif kind == 422004:
            value = state['vehicle'].get('total_kills', 0)
        elif kind == 423001:
            value = int(params[0] in state['race']['mission_list'])
        elif kind == 423003:
            value = int(state['race']['challenge_stage_info']['max_score'] >= params[0])
        elif kind == 423002:
            from resident_race import race_unlocks
            _, options = race_unlocks(state['race'])
            value = sum(row['id'] in options and row['item_type'] in params for row in resident_rows('ActivityTankWeaponCfg'))
        elif kind == 423004:
            value = state['race'].get('equipped_kills', {}).get(str(params[0]), 0)
        elif kind == 423006:
            value = state['race'].get('challenge_total_score', 0)
        elif kind == 50103:
            from resident_music import music_grade_task
            value = music_grade_task(user, params)
        elif kind == 50104:
            value = sum(r['sign'] > 0 and resident_config('ActivityMusicCfg', r['id'])['activity_id'] == params[0]
                        for r in state['music']['challenge_info'])
        elif kind == 350001:
            value = int(pub_level_complete(state['pub'], params[0]))
        elif kind == 350002:
            value = int(params[0] in state['pub']['stage_list'])
        elif kind == 350005:
            value = sum(r['state'] == 2 for r in state['pub']['cook_stage_list'])
        elif kind == 350003:
            from resident_pub import pub_partners
            value = len(pub_partners(state['pub'], params[0] if params else 0))
        elif kind in (340001, 340002):
            value = int(any(r['stage_id'] == params[0] and (kind == 340001 or r['point'] >= params[1])
                            for r in state['osiris']['pass_stages']))
        elif 340004 <= kind <= 340009:
            from resident_combat import TACTICAL_TASK_RECORDS
            records = state.get('osiris_records', {}).get(str(params[0]), {})
            value = int(records.get(str(TACTICAL_TASK_RECORDS[kind]), 0) >= params[1])
        elif kind in (380001, 380002):
            stage = params[0]
            value = int(any(r['stage_id'] == stage and (kind == 380001 or r['point'] >= params[1])
                            for r in state['ash']['pass_stages']))
        elif kind == 400004:
            value = int(any(b['boss_id'] == params[0] for r in state['hodur']['normal_stage_info'] for b in r['normal_boss_id_list']))
        elif kind == 400005:
            from resident_hodur import hodur_chapter_complete
            value = int(hodur_chapter_complete(user, params[0]))
        elif kind == 400002:
            value = int(max((r['challenge_max_score'] for r in state['hodur']['challenge_stage_info']), default=0) >= params[0])
        elif kind == 400003:
            value = int(any(n >= params[0] for n in state.get('hodur_completed_affix_counts', [])))
        elif kind == 400001:
            event = resident_config('ActivityHodurEventCfg', params[0], 'event_id')
            value = int(params[0] in state.get('hodur_event_history', []) or
                        any(o in event['event_option'] for r in state['hodur']['normal_stage_info'] for o in r['normal_option_info']))
        if value is not None:
            relevant.add(cfg['id'])
            row = tasks.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
            if kind in (340002, 380002) and not row['complete_flag']:
                row['progress'] = min(cfg['need'], value)
            else:
                row['progress'] = max(row['progress'], min(cfg['need'], value))
    for cfg in client_catalog()['tasks']:
        if cfg['condition'] == 501 and any(i in relevant for i in cfg['additional_parameter']):
            value = sum(tasks.get(str(i), {}).get('complete_flag', 0) > 0 for i in cfg['additional_parameter'])
            row = tasks.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
            row['progress'] = max(row['progress'], min(cfg['need'], value))


def update_rogue_tasks(user: dict) -> None:
    """按帖子、图鉴和已结算对局计算诡谈任务，支持重新登录及重复结算。"""
    from gameplay_data import client_catalog
    state = rogue_state(user)
    story = state['story']
    runs = [r for r in state['runs'].values() if r.get('closed')]
    cards = [resident_config('RogueCardItemCfg', i) for i in state['gather_card_list']]
    totals = {411010: 'use_card_num', 411011: 'discard_num', 411012: 'use_gold_num',
              411013: 'use_enhance_num', 411014: 'gain_joker_num', 411015: 'use_a_num'}
    catalog = client_catalog()['tasks']
    tasks = user.setdefault('tasks', {})
    for cfg in catalog:
        kind, params = cfg['condition'], cfg['additional_parameter']
        value = None
        if kind == 411008:
            value = int(params[0] in story['finish_thread_list'])
        elif kind == 411018:
            value = sum(resident_config('RogueCardPostingCfg', i)['type'] == 3 for i in story['view_thread_list'])
        elif kind in totals:
            value = sum(max(0, r.get('settle_info', r.get('save_data', {}).get('settle_data', {})).get(totals[kind], 0)) for r in runs)
        elif kind in (411016, 411017):
            # 客户端牌型编号：3 同花、4 顺子；数组从编号 1 开始。
            index = 2 if kind == 411016 else 3
            counts = [r.get('settle_info', r.get('save_data', {}).get('settle_data', {})).get('hand_type_contain_num', []) for r in runs]
            value = sum(max(0, row[index]) for row in counts if len(row) > index)
        elif kind == 411009:
            value = sum(r['battle_type'] == 2 and r.get('completed', False)
                and (not params[0] or r['deck'] == params[0]) and r['diff'] >= params[1] for r in runs)
        elif kind == 411003:
            value = sum(c['type'] == 3 for c in cards)
        elif kind == 411004:
            value = sum(c['type'] == params[0] for c in cards)
        elif kind == 411005:
            value = len(state['gather_weal_woe_list'])
        elif kind == 411006:
            value = sum(resident_config('RogueCardEnhanceCfg', i)['type'] == 3 for i in state['gather_enhance_list'])
        if value is not None:
            row = tasks.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
            if kind == 411006 and not row['complete_flag']:
                row['progress'] = min(cfg['need'], value)
            else:
                row['progress'] = max(row['progress'], min(cfg['need'], value))
    for cfg in catalog:
        if cfg['condition'] == 501 and any(50005000 <= i <= 50005200 for i in cfg['additional_parameter']):
            value = sum(tasks.get(str(i), {}).get('complete_flag', 0) > 0 for i in cfg['additional_parameter'])
            row = tasks.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
            row['progress'] = max(row['progress'], min(cfg['need'], value))
