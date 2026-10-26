"""本机玩家互访、房间展示、点赞和访客礼物。"""

from copy import deepcopy
import time

from backhome_catalog import backhome_catalog
from backhome_data import backhome_state
from backhome_rooms import layout_counts, owned_room
from battle_rewards import grant_rewards
from local_social import local_user, local_users, public_profile, public_user
from server_clock import business_day

VISIT_COMMANDS = {58050, 58052, 58054, 58056, 58058, 58060, 58148}


def home_week() -> int:
    """访客礼物按北京时间周一的业务日刷新。"""
    return (business_day(int(time.time())) + 3) // 7


def incoming_visits(user: dict) -> list[dict]:
    """访问事实只写入访问者存档，接收方按实际本机账号汇总，避免跨账号半提交。"""
    result = []
    for other in local_users(user):
        if other['uid'] == user['uid']:
            continue
        for visit in other.get('backhome', {}).get('visits', []):
            if visit['user_id'] == user['uid']:
                result.append({'key': str(other['uid']) + ':' + str(visit['day']),
                    'user_id': other['uid'], 'day': visit['day'], 'timestamp': visit['timestamp'],
                    'hero_id': visit['hero_id'], 'profile_base': public_profile(other)})
    return sorted(result, key=lambda r: (r['timestamp'], r['user_id']))


def room_social_counts(user: dict, room_id: int) -> dict:
    """点赞与访问统计由真实访问记录派生，不创建虚构在线玩家。"""
    prefix = str(user['uid']) + ':' + str(room_id) + ':'
    likes = sum(1 for other in local_users(user) if other['uid'] != user['uid']
                for key in other.get('backhome', {}).get('likes', {}) if key.startswith(prefix))
    visits = incoming_visits(user)
    return {'liked_num': likes, 'be_visited_num': len(visits)}


def visit_status(user: dict) -> dict:
    """恢复访客红点、周领取次数与最近访客，查询不会发奖。"""
    state = backhome_state(user)
    visits = incoming_visits(user)
    week = home_week()
    claimed = {r['key'] for r in state['claimed_visits']}
    received = sum(r['week'] == week for r in state['claimed_visits'])
    limit = backhome_catalog()['settings']['dorm_visit_gift_week_num'][0]
    available = [r for r in visits if (r['day'] + 3) // 7 == week and r['key'] not in claimed]
    count = backhome_catalog()['settings']['dorm_be_visited_show_limit'][0]
    return {'visited_user_list': [{'nick': r['profile_base']['nick'], 'hero_id': r['hero_id']} for r in visits[-count:]],
            'is_have_gift': bool(available and received < limit), 'received_be_visited_gift_num': received}


def exhibit_room(user: dict) -> dict:
    """只展示目标账号实际拥有并选中的房间。"""
    state = backhome_state(user)
    return owned_room(user, state['exhibition_id'])


def exhibit_brief(user: dict) -> dict:
    """展示列表仅包含公开房间和公开名片字段。"""
    state, room = backhome_state(user), exhibit_room(user)
    stats = room_social_counts(user, room['id'])
    return {**public_user(user), 'architecture_id': room['id'], 'skin_id_list': [
        next((s['skin_id'] for s in state['hero_setting'] if s['hero_id'] == state['heroes'][str(aid)]['hero_id']), state['heroes'][str(aid)]['hero_id'])
        for aid in room['archives_id']], 'furniture_num': sum(layout_counts(room['layout']['furniture_pos_list']).values()),
        'liked_num': stats['liked_num'], 'visited_num': stats['be_visited_num'],
        'picture_link': state.get('picture_link', ''), 'liked_timestamp': 0}


def visit_request(user: dict, command: int, request: dict) -> dict:
    """本机互访与礼物领取独立持久化，重复点赞和重复领奖不叠加奖励。"""
    state = backhome_state(user)
    if command == 58058:
        data = visit_status(user)
        return {'result': 0, 'visited_user_list': data['visited_user_list'], 'is_have_gift': data['is_have_gift']}
    if command == 58148:
        if request['type'] not in (1, 2, 3):
            raise ValueError('房间推荐类型无效')
        others = [u for u in local_users(user) if u['uid'] != user['uid'] and u.get('backhome', {}).get('starter_claimed')]
        if request['type'] == 1:
            others = [u for u in others if u['uid'] in user.get('friends', [])]
        rows = []
        for other in others:
            row = exhibit_brief(other)
            prefix = str(other['uid']) + ':' + str(row['architecture_id']) + ':'
            if request['type'] == 3 and not any(key.startswith(prefix) for key in state['likes']):
                continue
            row['liked_timestamp'] = max((stamp for key, stamp in state.get('like_timestamps', {}).items() if key.startswith(prefix)), default=0)
            rows.append(row)
        return {'result': 0, 'exhibition_brief': rows[:20]}
    if command == 58054:
        owned_room(user, request['architecture_id'])
        return {'result': 0, **room_social_counts(user, request['architecture_id'])}
    if command == 58060:
        claimed = {r['key'] for r in state['claimed_visits']}
        limit = backhome_catalog()['settings']['dorm_visit_gift_week_num'][0]
        remaining = limit - sum(r['week'] == home_week() for r in state['claimed_visits'])
        pending = [r for r in incoming_visits(user) if (r['day'] + 3) // 7 == home_week() and r['key'] not in claimed]
        result = []
        for visit in pending[:max(0, remaining)]:
            pool = backhome_catalog()['settings']['dorm_visit_gift_rand']
            rewards = [{'id': i, 'num': n} for i, n in pool[(visit['user_id'] + visit['day']) % len(pool)]]
            grant_rewards(user, rewards)
            result.append({'user_id': visit['user_id'], 'profile_base': visit['profile_base'], 'reward_list': rewards})
            state['claimed_visits'].append({'key': visit['key'], 'week': home_week()})
        return {'result': 0, 'be_visited_reward_list': result}
    target = local_user(user, request['user_id'])
    other = backhome_state(target)
    room = exhibit_room(target)
    if command == 58050:
        stats = room_social_counts(target, room['id'])
        status = visit_status(target)
        day = business_day(int(time.time()))
        key = str(target['uid']) + ':' + str(room['id']) + ':' + str(day)
        brief = exhibit_brief(target)
        return {'result': 0, 'furniture_pos_list': deepcopy(room['layout']['furniture_pos_list']),
                'layout_uid': other.get('layout_uid', 1), 'architecture_id': room['id'],
                'skin_id_list': brief['skin_id_list'], 'liked_num': stats['liked_num'],
                'own_today_liked_num': int(key in state['likes']), 'visited_num': stats['be_visited_num'],
                'visited_user_list': status['visited_user_list']}
    if target['uid'] == user['uid']:
        raise ValueError('不能访问或点赞自己的展示房间')
    if command == 58052:
        if request['architecture_id'] != room['id']:
            raise ValueError('目标展示房间已经变更')
        day = business_day(int(time.time()))
        key = str(target['uid']) + ':' + str(room['id']) + ':' + str(day)
        maximum = backhome_catalog()['settings']['dorm_room_like_limted'][1]
        if key not in state['likes'] and sum(d == day for d in state['likes'].values()) >= maximum:
            raise ValueError('今日点赞次数已达上限')
        if key not in state['likes']:
            state['likes'][key] = day
            state.setdefault('like_timestamps', {})[key] = int(time.time())
    elif command == 58056:
        day = business_day(int(time.time()))
        if not any(v['user_id'] == target['uid'] and v['day'] == day for v in state['visits']):
            choices = [r['hero_id'] for r in state['hero_setting']]
            selected = user.get('profile', {}).get('poster_girl', 0)
            hero_id = selected if selected in choices else min(choices, default=0)
            if not hero_id:
                raise ValueError('没有可用于游园街访问的角色')
            state['visits'].append({'user_id': target['uid'], 'day': day, 'timestamp': int(time.time()),
                                    'hero_id': hero_id})
    else:
        raise ValueError('游园街访问指令未支持')
    return {'result': 0}
