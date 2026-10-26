"""训练室编舞、本机账号分享、收藏、点赞和浏览统计。"""

from copy import deepcopy
import time

from backhome_catalog import backhome_catalog, home_config
from backhome_data import backhome_state
from local_social import local_users, public_user
from server_clock import business_day

DANCE_COMMANDS = {58198, 58200, 58204, 58206, 58208, 58210, 58212, 58214, 58216}


def action_unlocked(user: dict, action: int) -> bool:
    """编舞动作以实际训练、编辑和对战统计解锁，不把浏览当作已完成任务。"""
    cfg = home_config('IdolDanceDIYActionCfg', action)
    if not cfg['unlock_condition']:
        return True
    condition = home_config('ConditionCfg', cfg['unlock_condition'])
    state = user['backhome']['idol']
    kind, args = condition['type'], condition['params']
    counts = {8110: state.get('train_times', 0), 8111: state.get('dance_edits', 0),
              8112: state.get('friend_battles', 0)}
    value = counts.get(kind, 0)
    if kind in (8113, 8114):
        field = 'hero_battles' if kind == 8113 else 'hero_trains'
        value = state.get(field, {}).get(str(args[0]), 0)
    return value >= cfg['need']


def validate_sequence(user: dict, sequence: dict) -> None:
    """音乐、舞台和动作都必须来自当前客户端，锁定动作不能通过分享绕过。"""
    home_config('IdolStageSceneCfg', sequence['scene_id'])
    music = home_config('IdolStageMusicCfg', sequence['music_id'])
    actions = sequence['action_id_list']
    if not actions or len(actions) > music['round_time'] or any(not action_unlocked(user, i) for i in actions):
        raise ValueError('编舞动作为空、超出时长或尚未解锁')


def shared_sequence(user: dict, uid: int) -> tuple:
    """分享UID由服务端生成且全本机唯一，未知或已撤销的作品不能访问。"""
    for other in local_users(user):
        row = next((r for r in other.get('backhome', {}).get('idol', {}).get('shared', []) if r['uid'] == uid), None)
        if row:
            return other, row
    raise ValueError('分享编舞不存在或已撤销')


def sequence_statistics(user: dict, uid: int) -> dict:
    """收藏、点赞和浏览只汇总真实本机账号事实，不跨账号写入半完成数据。"""
    collected, liked, viewed = 0, 0, 0
    for other in local_users(user):
        data = other.get('backhome', {}).get('idol', {})
        collected += int(uid in data.get('collected', []))
        liked += sum(r['uid'] == uid for r in data.get('sequence_likes', []))
        viewed += int(uid in data.get('sequence_views', []))
    return {'be_collected_num': collected, 'be_liked_num': liked, 'be_viewed_num': viewed}


def sequence_brief(user: dict, owner: dict, row: dict) -> dict:
    """推荐列表只发送作品摘要，详情查询才返回完整动作序列。"""
    stats = sequence_statistics(user, row['uid'])
    likes = backhome_state(user)['idol']['sequence_likes']
    base = row['base_sequence']
    return {'sequence_uid': row['uid'], 'user_id': owner['uid'], 'liked_timestamp': next(
        (r['timestamp'] for r in likes if r['uid'] == row['uid']), 0),
        'music_id': base['music_id'], 'scene_id': base['scene_id'], 'action_num': len(base['action_id_list']),
        **stats, 'recommend_value': stats['be_collected_num'] + stats['be_liked_num'] + stats['be_viewed_num']}


def save_sequence(user: dict, request: dict) -> None:
    """按槽位保存编舞，重复保存相同内容不反复增加编辑统计。"""
    state = backhome_state(user)['idol']
    row = request['sequence']
    maximum = backhome_catalog()['settings']['idol_dance_edit_max_num'][0]
    if not 1 <= row['pos'] <= maximum:
        raise ValueError('编舞保存槽位无效')
    validate_sequence(user, row['base_sequence'])
    old = next((r for r in state['sequences'] if r['pos'] == row['pos']), None)
    if old == row:
        return
    state['sequences'] = [r for r in state['sequences'] if r['pos'] != row['pos']]
    state['sequences'].append(deepcopy(row))
    state['dance_edits'] = state.get('dance_edits', 0) + 1


def dance_request(user: dict, command: int, request: dict) -> dict:
    """实现完整本机编舞保存、分享、收藏、详情和统计闭环。"""
    state = backhome_state(user)['idol']
    if command == 58198:
        save_sequence(user, request)
    elif command == 58200:
        row = next((r for r in state['sequences'] if r['pos'] == request['pos']), None)
        if row is None:
            raise ValueError('编舞槽位为空')
        state['sequences'].remove(row)
    elif command == 58204:
        validate_sequence(user, request['base_sequence'])
        if len(state['shared']) >= backhome_catalog()['settings']['idol_dance_edit_share_max_num'][0]:
            raise ValueError('编舞分享数量达到上限')
        existing = next((r for r in state['shared'] if r['base_sequence'] == request['base_sequence']), None)
        if existing:
            return {'result': 0, 'uid': existing['uid']}
        serial = state.get('next_sequence', 1)
        state['next_sequence'] = serial + 1
        uid = user['uid'] * 1000000 + serial
        state['shared'].append({'uid': uid, 'base_sequence': deepcopy(request['base_sequence']),
                                'statistics': {'be_collected_num': 0, 'be_liked_num': 0, 'be_viewed_num': 0}})
        return {'result': 0, 'uid': uid}
    elif command == 58206:
        row = next((r for r in state['shared'] if r['uid'] == request['uid']), None)
        if row is None:
            raise ValueError('自己的分享作品不存在')
        state['shared'].remove(row)
    elif command == 58208:
        owner, row = shared_sequence(user, request['uid'])
        if owner['uid'] == user['uid']:
            raise ValueError('不能点赞自己的编舞')
        day = business_day(int(time.time()))
        likes = state['sequence_likes']
        if not any(r['uid'] == row['uid'] and r['day'] == day for r in likes):
            if sum(r['day'] == day for r in likes) >= backhome_catalog()['settings']['idol_dance_edit_like_recommend_limit'][0]:
                raise ValueError('今日编舞点赞次数已达上限')
            likes.append({'uid': row['uid'], 'day': day, 'timestamp': int(time.time())})
    elif command == 58210:
        owner, row = shared_sequence(user, request['uid'])
        if row['base_sequence'] != request['sequence']['base_sequence']:
            raise ValueError('收藏动作与分享作品不一致')
        save_sequence(user, request)
        if row['uid'] not in state['collected']:
            state['collected'].append(row['uid'])
        return {'result': 0, 'be_collected_num': sequence_statistics(user, row['uid'])['be_collected_num']}
    elif command == 58212:
        owner, row = shared_sequence(user, request['sequence_uid'])
        if row['uid'] not in state['sequence_views']:
            state['sequence_views'].append(row['uid'])
        stats = sequence_statistics(user, row['uid'])
        day = business_day(int(time.time()))
        return {'result': 0, 'own_today_liked_num': int(any(
            r['uid'] == row['uid'] and r['day'] == day for r in state['sequence_likes'])),
            'base_sequence': deepcopy(row['base_sequence']), **stats}
    elif command == 58214:
        if request['type'] not in (1, 2, 3):
            raise ValueError('编舞推荐类型无效')
        briefs, authors = [], []
        for other in local_users(user):
            if request['type'] == 1 and other['uid'] not in user.get('friends', []):
                continue
            for row in other.get('backhome', {}).get('idol', {}).get('shared', []):
                if request['type'] == 3 and row['uid'] not in state['sequence_views']:
                    continue
                briefs.append(sequence_brief(user, other, row))
            authors.append(public_user(other))
        return {'result': 0, 'sequence_brief_list': briefs[:20], 'user_brief_list': authors}
    elif command == 58216:
        return {'result': 0, 'sequence_statistics_list': [
            {'sequence_uid': r['uid'], 'statistics': sequence_statistics(user, r['uid'])} for r in state['shared']]}
    else:
        raise ValueError('编舞指令未支持')
    return {'result': 0}
