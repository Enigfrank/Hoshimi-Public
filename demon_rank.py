"""古远虚影本地账号排行榜，不使用官服实时排名。"""

from gameplay_protocol import read_client_data
from mult_heart_demon import current_edition


def demon_rank_request(user: dict, command: int, request: dict) -> dict:
    """查询本地已完成挑战的最高分、分页和本人名次。"""
    if request['rank_id'] != 7:
        raise ValueError('当前仅实现古远虚影本地排名')
    from user_storage import get_user_manager
    manager = get_user_manager()
    users = [manager.get_user_by_uid(int(uid)) for uid in manager.get_meta().get('accounts', {}).values()]
    users = [u for u in users if u and u['uid'] != user['uid']] + [user]
    rows = []
    for account in users:
        state = account.get('mult_heart_demon', {})
        if state.get('activity_id') != current_edition():
            continue
        candidates = [(int(k), v) for k, v in state.get('best', {}).items() if v.get('score', 0) > 0]
        if not candidates:
            continue
        difficulty, best = max(candidates, key=lambda row: (row[1]['score'], row[0]))
        heroes = read_client_data('''(function()
            local r={};local c=require('HeroStandardSystemCfg')
            for _,id in ipairs(__IDS__) do r[#r+1]=c[id] and c[id].hero_id or id end
            return r
        end)()'''.replace('__IDS__', '{'+','.join(str(h) for h in best['heroes'])+'}'))
        rows.append({'user_id': account['uid'], 'score': best['score'], 'difficulty': difficulty,
            'rank': 0, 'nick': account.get('nick', account['account']), 'portrait': account.get('icon', 1084),
            'frame': account.get('icon_frame', 2001), 'timestamp': best['time'],
            'stage_team_list': [{'stage_id': 0, 'team_info_list': [{'index': 1,
                'hero_info_list': [{'hero_id': h, 'skin_id': h} for h in heroes]}]}], 'extra_info': []})
    rows = sorted(rows, key=lambda row: (-row['score'], row['timestamp'], row['user_id']))
    for index, row in enumerate(rows, 1):
        row['rank'] = index
    own = next((row for row in rows if row['user_id'] == user['uid']),
        {'user_id': user['uid'], 'score': 0, 'difficulty': 0, 'rank': 0,
         'nick': user.get('nick', user['account']), 'portrait': user.get('icon', 1084),
         'frame': user.get('icon_frame', 2001), 'timestamp': 0, 'stage_team_list': [], 'extra_info': []})
    if command == 69002:
        children = request['children_id_list']
        if len(children) != len(set(children)) or any(c != 0 for c in children):
            raise ValueError('古远虚影排名分组无效')
        return {'result': 0, 'children_rank_list': [{'children_id': c, 'my_rank': own, 'total': len(rows)} for c in children]}
    if request['children_id'] != 0 or not 1 <= request['start'] <= request['stop'] <= 300:
        raise ValueError('古远虚影排名分页无效')
    return {'result': 0, 'rank_list': rows[request['start']-1:request['stop']], 'my_rank': own,
            'total': len(rows), 'version': 1 + sum(row['score'] + row['timestamp'] for row in rows) % 4294967294}
