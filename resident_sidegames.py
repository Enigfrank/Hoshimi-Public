"""棋盘机会调用的抓鸭、节奏、弹珠气泡、拼图和收藏品子玩法接口。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_rows

SIDE_COMMANDS = {83123, 83126, 86002, 86006, 86009, 86028, 86030}


def side_state(user: dict) -> dict:
    """保存所有子玩法完成、最高分、拼图进度和收藏品状态。"""
    state = user.setdefault('resident', {}).setdefault('sidegames', {})
    state.setdefault('whack', {})
    state.setdefault('rhythm', {})
    state.setdefault('blister', {'finish_list': [], 'reward_list': []})
    state.setdefault('tangram', {})
    state.setdefault('illustration', {})
    return state


def side_pushes(user: dict) -> list[tuple]:
    """登录恢复子玩法数据，避免机会页面读取空值。"""
    s = side_state(user)
    return [(83121, {'activity_id': 282971, 'stage_info_list': [
                {'id': int(k), 'is_success': v.get('is_success', 0), 'history_max_score': v.get('score', 0)}
                for k, v in s['whack'].items()]}),
        (83124, {'rhythm_stage_list': [{'id': int(k), 'star_id_list': v.get('stars', []), 'use_seconds': v.get('seconds', 0)}
                for k, v in s['rhythm'].items()], 'story_id_list': []}),
        (86001, {'mini_game_info': {'activity_id': 283101, 'got_reward_list': s['blister']['reward_list'],
                'finish_list': [{'stage_id': int(i), 'value': 0, 'star': 0} for i in s['blister']['finish_list']], 'client_args': ''}}),
        (86008, {'activity_id': 283121, 'tangram_info_list': [{'stage_id': int(k), 'tips_id_list': v.get('tips', []), 'is_animation_finish': bool(v.get('watched', 0))}
                for k, v in s['tangram'].items()]}),
        (86033, {'activity_id': 323641, 'data': {'item_list': [{'id': int(k), 'state': v} for k, v in s['illustration'].items()]}})]


def side_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """保存子玩法结算和收藏品查看，重复请求只更新最高成绩。"""
    s = side_state(user)
    response = {'result': 0}
    if command == 83123:
        stage = int(request['id']); row = s['whack'].setdefault(str(stage), {'is_success': 0, 'score': 0})
        row['score'] = max(row['score'], request['settle_score']); row['is_success'] = max(row['is_success'], request['is_success'])
        response.update(id=stage, is_success=row['is_success'], settle_score=request['settle_score'])
    elif command == 83126:
        append_unique(user.setdefault('read_stories', []), request['story_id'])
    elif command == 86002:
        stage = int(request['stage_id']); append_unique(s['blister']['finish_list'], stage)
        response.update(activity_id=request['activity_id'], reward_list=[])
    elif command == 86006:
        row = s['tangram'].setdefault(str(request['stage_id']), {'tips': [], 'watched': 0})
        row['tips'] = []
        for tip in request['tips_id_list']:
            append_unique(row['tips'], tip)
        response['activity_id'] = request['activity_id']
    elif command == 86009:
        row = s['tangram'].setdefault(str(request['stage_id']), {'tips': [], 'watched': 0}); row['watched'] = 1
        response['activity_id'] = request['activity_id']
    elif command == 86028:
        if str(request['id']) not in s['illustration']:
            raise ValueError('收藏品尚未获得')
        s['illustration'][str(request['id'])] = 1
    elif command == 86030:
        value = s['illustration'].get(str(request['id']))
        if value != 1: raise ValueError('收藏品未阅读或已领取')
        s['illustration'][str(request['id'])] = 2; response['item_list'] = []
    else:
        raise ValueError('未知子玩法请求')
    return response, side_pushes(user)
