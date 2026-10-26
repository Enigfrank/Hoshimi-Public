"""谧光叙录的章节、事件、路线重置及历史任务事实。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_config, resident_rows
from resident_data import resident_state

HODUR_ACTIVITY = 3941101
HODUR_COMMANDS = {89422, 89424, 89426}


def hodur_chapter(chapter: int) -> dict:
    """只接收本机配置的真实章节编号。"""
    return resident_config('ActivityHodurChapterCfg', chapter, 'chapter_id')


def hodur_row(user: dict, chapter: int) -> dict:
    """补齐该章节的协议字段，不预发任何通关、成绩或奖励。"""
    cfg = hodur_chapter(chapter)
    challenge = cfg['chapter_type'] == 2
    key, field = ('challenge_stage_info', 'challenge_stage_id') if challenge else ('normal_stage_info', 'normal_stage_id')
    rows = resident_state(user)['hodur'][key]
    row = next((r for r in rows if r[field] == chapter), None)
    if row is None:
        row = {field: chapter}
        rows.append(row)
    if challenge:
        defaults = {'challenge_buff_info': [], 'challenge_max_score': 0, 'challenge_hero_stage_info': [],
                    'challenge_boss_list': [], 'last_status': [], 'score_list': [0, 0, 0, 0]}
        for name, value in defaults.items():
            row.setdefault(name, value)
        bosses = row['challenge_boss_list']
        for stages in cfg['stage_list']:
            if not any(r['boss_id'] == stages[0] for r in bosses):
                bosses.append({'boss_id': stages[0], 'score': 0, 'max_score': 0})
        bosses.sort(key=lambda r: next(i for i, stages in enumerate(cfg['stage_list']) if stages[0] == r['boss_id']))
    else:
        for name in ('normal_option_info', 'normal_hero_stage_info', 'normal_boss_id_list'):
            row.setdefault(name, [])
    return row


def hodur_chapter_complete(user: dict, chapter: int) -> bool:
    """普通章节历史通关由全部boss记录决定，重置当前路线不会撤销历史。"""
    cfg = hodur_chapter(chapter)
    rows = resident_state(user)['hodur']['normal_stage_info']
    row = next((r for r in rows if r['normal_stage_id'] == chapter), None)
    passed = {b['boss_id'] for b in row['normal_boss_id_list']} if row else set()
    return all(stages[0] in passed for stages in cfg['stage_list'])


def validate_hodur_chapter(user: dict, chapter: int) -> dict:
    """章节前置读取历史通关，与原始HodurTools.GetChapterState一致。"""
    cfg = hodur_chapter(chapter)
    if cfg['previous_chapter'] and not hodur_chapter_complete(user, cfg['previous_chapter']):
        raise ValueError('请先完成谧光叙录前置章节')
    return cfg


def hodur_serial(user: dict, chapter: int) -> int:
    """读取路线代数，确保重置前已开始的战斗不能结算到新路线。"""
    return resident_state(user).get('hodur_serials', {}).get(str(chapter), 0)


def hodur_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """校验普通事件互斥选择和路线重置，保留最高分及已完成任务事实。"""
    if request['activity_id'] != HODUR_ACTIVITY:
        raise ValueError('谧光叙录活动编号不匹配')
    state = resident_state(user)
    chapter = request.get('chapter_id', 4)
    cfg = validate_hodur_chapter(user, chapter)
    row = hodur_row(user, chapter)
    result = {'result': 0}
    if command == 89426:
        if cfg['chapter_type'] != 1:
            raise ValueError('挑战章节不能选择普通事件')
        event = next((resident_config('ActivityHodurEventCfg', stages[1], 'event_id')
                      for stages in cfg['stage_list'] if len(stages) > 1 and request['option_id'] in
                      resident_config('ActivityHodurEventCfg', stages[1], 'event_id')['event_option']), None)
        if event is None:
            raise ValueError('事件不属于当前谧光章节')
        current = {b['boss_id'] for b in row['normal_boss_id_list'] if b['result'] == 1}
        if event['condition'] and event['condition'] not in current:
            raise ValueError('事件尚未开放')
        selected = next((i for i in row['normal_option_info'] if i in event['event_option']), None)
        if selected is not None and selected != request['option_id']:
            raise ValueError('同一事件只能选择一个选项，请重置章节后重选')
        append_unique(row['normal_option_info'], request['option_id'])
        append_unique(state.setdefault('hodur_event_history', []), event['event_id'])
        result['round'] = len(row['normal_option_info'])
    elif command == 89422:
        if cfg['chapter_type'] != 1:
            raise ValueError('不是普通章节')
        row.update(normal_option_info=[], normal_hero_stage_info=[])
        for boss in row['normal_boss_id_list']:
            boss['result'] = 0
    elif command == 89424:
        if cfg['chapter_type'] != 2:
            raise ValueError('不是挑战章节')
        row['challenge_max_score'] = max(row['challenge_max_score'], sum(b['score'] for b in row['challenge_boss_list']))
        row.update(challenge_buff_info=[], challenge_hero_stage_info=[], last_status=[], score_list=[0, 0, 0, 0])
        for boss in row['challenge_boss_list']:
            boss['score'] = 0
    else:
        raise ValueError('未知谧光叙录请求')
    if command in (89422, 89424):
        serials = state.setdefault('hodur_serials', {})
        serials[str(chapter)] = serials.get(str(chapter), 0) + 1
    return result, [(89421, deepcopy(state['hodur']))]
