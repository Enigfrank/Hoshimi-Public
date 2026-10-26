"""诡谈夜话：帖子、故事、挑战与完整局内存档。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_config

ROGUE_COMMANDS = {89602, 89604, 89606, 89608, 89610, 89612, 89616, 89618,
                  89620, 89622, 89624, 89626, 89628}


def rogue_state(user: dict) -> dict:
    """为单个账号建立空进度，不预先完成帖子或挑战。"""
    state = user.setdefault('resident_rogue', {})
    state.setdefault('story', {'thread_id': 0, 'thread_state': 0, 'battle_id': 0,
        'finish_thread_list': [], 'like_thread_list': [], 'post_thread_list': [], 'view_thread_list': []})
    state.setdefault('challenge', {'deck': 1, 'diff': 1, 'state': 0, 'battle_id': 0, 'status_list': []})
    for key in ('gather_card_list', 'gather_enhance_list', 'gather_weal_woe_list'):
        state.setdefault(key, [])
    state.setdefault('runs', {})
    state.setdefault('next_battle_id', 1)
    return state


def rogue_data(user: dict) -> dict:
    """登录及操作后恢复客户端需要的两个模式与图鉴进度。"""
    state = rogue_state(user)
    return {key: deepcopy(state[key]) for key in (
        'story', 'challenge', 'gather_card_list', 'gather_enhance_list', 'gather_weal_woe_list')}


def rogue_run(state: dict, request: dict, active: bool = True) -> dict:
    """按账号、战斗编号及模式查找存档，拒绝跨模式和过期写入。"""
    kind = request['battle_type']
    run = state['runs'].get(str(request['battle_id']))
    if kind not in (1, 2) or not run or run['battle_type'] != kind:
        raise ValueError('诡谈夜话存档不存在或模式不匹配')
    mode = state['story' if kind == 1 else 'challenge']
    if mode['battle_id'] != request['battle_id'] or active and run.get('closed'):
        raise ValueError('诡谈夜话对局已结束')
    return run


def rogue_collect(state: dict, info: dict) -> None:
    """依据结算中的实际使用记录保存图鉴，不把任意整数写入解锁列表。"""
    for source, target, table in (
        ('use_item_id', 'gather_card_list', 'RogueCardItemCfg'),
        ('enhance_id', 'gather_enhance_list', 'RogueCardEnhanceCfg'),
        ('weal_woe_id', 'gather_weal_woe_list', 'RogueCardBossEffectCfg')):
        for item in info.get(source, []):
            resident_config(table, item)
            append_unique(state[target], item)


def rogue_check_challenge(state: dict, deck: int, difficulty: int) -> None:
    """按客户端条件检查牌组与逐级难度，普通账号不能跳过解锁。"""
    cfg = resident_config('RogueCardDeckCfg', deck)
    resident_config('RogueCardDifficultyCfg', difficulty)
    status = {r['deck']: r['max_diff'] for r in state['challenge']['status_list']}
    if cfg['condition']:
        condition = resident_config('ConditionCfg', cfg['condition'])
        params = condition['params']
        unlocked = (params[0] in state['story']['finish_thread_list'] if condition['type'] == 12701
                    else status.get(params[0], 0) >= params[1] if condition['type'] == 12702 else False)
        if not unlocked:
            raise ValueError('挑战牌组尚未解锁')
    if difficulty > status.get(deck, 0) + 1:
        raise ValueError('请先完成前一挑战难度')


def rogue_request(user: dict, command: int, request: dict) -> dict:
    """实现客户端全部 896xx 请求；局内出牌由客户端计算，服务端保存回合快照。"""
    state = rogue_state(user)
    story, challenge = state['story'], state['challenge']
    response = {'result': 0}
    if command in (89602, 89604, 89606, 89608, 89610, 89612):
        thread = request['thread_id']
        cfg = resident_config('RogueCardPostingCfg', thread)
        if cfg['pre_id'] and cfg['pre_id'] not in story['finish_thread_list']:
            raise ValueError('请先完成前置帖子')
        if command in (89602, 89608):
            if cfg['type'] != 1:
                raise ValueError('该帖子不能开始调查')
            if story['battle_id'] and not state['runs'][str(story['battle_id'])].get('closed'):
                if story['thread_id'] != thread:
                    raise ValueError('请先结束当前调查')
            story.update(thread_id=thread, thread_state=1)
            append_unique(story['view_thread_list'], thread)
        elif command == 89606:
            if thread in story['like_thread_list']:
                story['like_thread_list'].remove(thread)
            else:
                story['like_thread_list'].append(thread)
        elif command == 89610:
            append_unique(story['view_thread_list'], thread)
        elif command == 89604:
            index = request['story_id_index']
            if index not in (1, 2) or request['story_id'] != cfg[f'story_id{index}']:
                raise ValueError('帖子剧情不匹配')
            if index == 2 and thread not in story['finish_thread_list']:
                raise ValueError('调查尚未完成')
            if cfg['balatro_id'] == 0 and index == 1:
                append_unique(story['finish_thread_list'], thread)
                story['thread_state'] = 0
            if request['story_id']:
                append_unique(user.setdefault('read_stories', []), request['story_id'])
        else:
            run = rogue_run(state, {'battle_id': story['battle_id'], 'battle_type': 1}, False)
            if run['stage_id'] != cfg['balatro_id'] or story['thread_id'] != thread:
                raise ValueError('帖子与对局不匹配')
            if run.get('closed'):
                if run.get('completed'):
                    return response
                raise ValueError('对局已放弃')
            if request['joker_result'] not in (1, 2, 3):
                raise ValueError('结算结果无效')
            rogue_collect(state, request['info'])
            run.update(closed=True, completed=request['joker_result'] == 1, settle_info=deepcopy(request['info']))
            if run['completed']:
                append_unique(story['finish_thread_list'], thread)
                append_unique(story['post_thread_list'], thread)
            story['thread_state'] = 0
    elif command == 89618:
        stage = resident_config('RogueCardStageCfg', request['stage_id'])
        kind = 2 if stage['type'] == 2 else 1
        mode = challenge if kind == 2 else story
        if kind == 1:
            cfg = resident_config('RogueCardPostingCfg', story['thread_id'])
            if cfg['balatro_id'] != stage['id']:
                raise ValueError('请先选择对应调查帖子')
        else:
            rogue_check_challenge(state, request['deck'], request['diff'])
        previous = state['runs'].get(str(mode['battle_id']))
        if previous and not previous.get('closed'):
            if (previous['stage_id'], previous['deck'], previous['diff']) != (stage['id'], request['deck'], request['diff']):
                raise ValueError('请先结束当前对局')
            response['battle_id'] = mode['battle_id']
            return response
        battle_id = state['next_battle_id']
        state['next_battle_id'] += 1
        state['runs'][str(battle_id)] = {'stage_id': stage['id'], 'battle_type': kind,
            'deck': request['deck'], 'diff': request['diff'], 'closed': False}
        mode['battle_id'] = battle_id
        if kind == 2:
            mode.update(deck=request['deck'], diff=request['diff'], state=1)
        else:
            mode['thread_state'] = 1
        response['battle_id'] = battle_id
    elif command == 89616:
        run = rogue_run(state, {'battle_id': challenge['battle_id'], 'battle_type': 2}, False)
        if (request['deck'], request['diff']) != (run['deck'], run['diff']):
            raise ValueError('挑战牌组或难度不匹配')
        if run.get('closed'):
            if run.get('completed'):
                return response
            raise ValueError('挑战已放弃')
        rogue_collect(state, request['info'])
        run.update(closed=True, completed=True, settle_info=deepcopy(request['info']))
        row = next((x for x in challenge['status_list'] if x['deck'] == request['deck']), None)
        if row is None:
            row = {'deck': request['deck'], 'max_diff': 0}
            challenge['status_list'].append(row)
        row['max_diff'] = max(row['max_diff'], request['diff'])
        challenge['state'] = 0
    elif command in (89620, 89622, 89624, 89626, 89628):
        run = rogue_run(state, request, command != 89624)
        if command in (89620, 89626):
            key = 'save_data' if command == 89620 else 'save_rollback'
            snapshot = request.get(key)
            if not isinstance(snapshot, dict) or snapshot.get('stage_id') != run['stage_id']:
                raise ValueError('局内存档与关卡不匹配')
            if request['battle_type'] == 2 and (snapshot.get('deck_id'), snapshot.get('difficulty')) != (run['deck'], run['diff']):
                raise ValueError('局内存档与挑战配置不匹配')
            if command == 89626 and request['battle_type'] != 1:
                raise ValueError('挑战模式不支持回溯')
            run[key] = deepcopy(snapshot)
            if command == 89620:
                run['check_sum'] = request.get('check_sum', '')
        elif command in (89622, 89628):
            if 'save_data' not in run:
                raise ValueError('该对局尚未保存')
            response['save_data'] = deepcopy(run['save_data'])
            response['battle_id'] = request['battle_id']
            if command == 89622:
                response['battle_id'] = request['battle_id']
                if 'save_rollback' in run:
                    response['save_rollback'] = deepcopy(run['save_rollback'])
        else:
            run['closed'] = True
            state['story' if request['battle_type'] == 1 else 'challenge'][
                'thread_state' if request['battle_type'] == 1 else 'state'] = 0
            response.update(battle_id=request['battle_id'], battle_type=request['battle_type'],
                settle_info=deepcopy(run.get('settle_info', run.get('save_data', {}).get('settle_data', {}))))
    else:
        raise ValueError('未知诡谈夜话请求')
    return response
