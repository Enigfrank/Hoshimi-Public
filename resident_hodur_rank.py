"""谧光叙录与远村异闻的本地账号最高成绩排行榜。"""

from copy import deepcopy
import zlib

from gameplay_protocol import encode_message, read_client_data
from resident_data import resident_state

HODUR_RANK_ACTIVITY = 3941201
PINBALL_RANK_ACTIVITY = 3840901


def save_hodur_best(user: dict, row: dict, affixes: list) -> None:
    """保存单条实际路线最高总分的队伍和模块，不拼凑不同轮次的各boss最高分。"""
    state = resident_state(user)
    score = sum(b['score'] for b in row['challenge_boss_list'])
    if score <= state.get('hodur_best', {}).get('score', 0):
        return
    from resident_hodur_battle import hodur_trial_rules
    rules = hodur_trial_rules()
    trials = set(rules['normal'] + rules['locked'])
    heroes = []
    for hp in row['challenge_hero_stage_info']:
        item = hp['hero_id']
        if item in trials:
            cfg = read_client_data(f"require('HeroStandardSystemCfg')[{int(item)}]")
            heroes.append({'hero_id': cfg['hero_id'], 'skin_id': cfg['skin_id']})
        else:
            owned = next((h for h in user.get('heroes', []) if h['id'] == item), {})
            heroes.append({'hero_id': item, 'skin_id': owned.get('using_skin', 0) or item})
    state['hodur_best'] = {'score': score, 'heroes': heroes, 'affixes': deepcopy(affixes)}


def hodur_rank_request(user: dict, command: int, request: dict) -> dict:
    """查询谧光／远村本地最佳成绩，提供空榜、本人排名、合法分页和数据版本。"""
    if request['activity_id'] not in (HODUR_RANK_ACTIVITY, PINBALL_RANK_ACTIVITY):
        raise ValueError('常驻排行榜活动编号不匹配')
    from user_storage import get_user_manager
    manager = get_user_manager()
    users = [manager.get_user_by_uid(int(uid)) for uid in manager.get_meta().get('accounts', {}).values()]
    users = [u for u in users if u and u['uid'] != user['uid']] + [user]
    rows = []
    for account in users:
        state = account.get('resident', {})
        best = (state.get('hodur_best', {}) if request['activity_id'] == HODUR_RANK_ACTIVITY else
                {'score': state.get('pinball', {}).get('challenge_score', 0), 'affixes': [], 'heroes': []})
        if not best.get('score', 0):
            continue
        rows.append({'user_id': account['uid'], 'score': best['score'], 'difficulty': len(best['affixes']), 'rank': 0,
            'nick': account.get('nick', account['account']), 'portrait': account.get('icon', 1084),
            'frame': account.get('icon_frame', 2001), 'timestamp': 0,
            'stage_team_list': [{'stage_id': 0, 'team_info_list': [{'index': 1, 'hero_info_list': best['heroes']}]}],
            'extra_info': [{'key': i, 'value': value} for i, value in enumerate(best['affixes'], 1)],
            'static_info': {'clear_time': 0, 'hit_num': 0}})
    rows.sort(key=lambda r: (-r['score'], r['user_id']))
    for rank, row in enumerate(rows, 1):
        row['rank'] = rank
    own = next((r for r in rows if r['user_id'] == user['uid']),
        {'user_id': user['uid'], 'score': 0, 'difficulty': 0, 'rank': 0, 'nick': user.get('nick', user['account']),
         'portrait': user.get('icon', 1084), 'frame': user.get('icon_frame', 2001), 'timestamp': 0,
         'stage_team_list': [], 'extra_info': [], 'static_info': {'clear_time': 0, 'hit_num': 0}})
    if command == 69006:
        return {'result': 0, 'children_rank_list': [{'children_id': 0, 'my_rank': own, 'total': len(rows)}]}
    if request['children_id'] != 0 or not 1 <= request['start'] <= request['stop'] <= 300:
        raise ValueError('常驻排行榜分页无效')
    result = {'result': 0, 'rank_list': rows, 'my_rank': own, 'total': len(rows)}
    result['version'] = zlib.crc32(encode_message(69005, result)) or 1
    result['rank_list'] = rows[request['start']-1:request['stop']]
    return result
