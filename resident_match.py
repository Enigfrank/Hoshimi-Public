"""决斗王联机匹配、准备操作、战斗动作、结算和排行榜。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import threading
import time
import uuid

from resident_autochess import auto_request, auto_state, make_game, base_info
from resident_autochess_round import shop_attributes, repair_round_state


ONLINE_COMMANDS = {90002, 90004, 90008, 90080, 90082, 90102, 90104, 90106,
    90108, 90118, 90120, 90122, 90126, 90128, 90134, 90140, 90144}
_ACTION_MAP = {90102: 89102, 90104: 89104, 90106: 89134, 90118: 89118,
    90120: 89120, 90122: 89122, 90128: 89130}


@dataclass
class OnlineEvent:
    """发送给指定连接的服务器推送。"""

    session: object
    command: int
    data: dict
    delay: float = 0.0


@dataclass
class Match:
    """一场两人联机对局，账号进度仍保存到各自存档。"""

    match_id: str
    sessions: dict[int, object]
    users: dict[int, dict]
    games: dict[int, dict]
    ready: set[int]
    state: int = 1
    round: int = 1
    battle_uid: int = 0


_LOCK = threading.RLock()
_QUEUE: list[object] = []
_MATCHES: dict[str, Match] = {}


def _state(user: dict) -> dict:
    """建立可以跨重启恢复的联机状态。"""
    state = auto_state(user).setdefault('online', {})
    for key, value in {
        'match_id': '', 'state': 0, 'round': 1, 'rank_score': 1000,
        'rank_index': 0, 'records': [], 'wins': 0, 'losses': 0,
        'game': None, 'opponent': {},
    }.items():
        state.setdefault(key, value)
    if isinstance(state.get('game'), dict):
        repair_round_state(state['game'])
    return state


def _save(user: dict) -> None:
    """原子保存账号。"""
    from user_storage import get_user_manager
    get_user_manager().save_user(user)


def _board(game: dict | None) -> dict:
    """生成客户端 chessboard_record。"""
    if not game:
        return {'base_info_list': [], 'chess_list': [], 'card_ids': []}
    return {'base_info_list': base_info(game),
        'chess_list': deepcopy(game.get('chess_list', [])), 'card_ids': []}


def _player_info(game: dict) -> dict:
    """生成 90101 的 auto_chess_player_info。"""
    now = int(time.time())
    return {'auto_chessboard_info': _board(game), 'shop_items': deepcopy(game.get('shop_items', [])),
        'hero_upgrade_info': {'old_level': 0, 'new_level': 0, 'type': 0,
            'hero_upgrade_attrs': [], 'hero_upgrade_buffs': []}, 'buff_list': [],
        'attr_list': shop_attributes(game), 'start_timestamp': now,
        'end_timestamp': now + 60, 'close_opt': {'prepare_num': 1, 'all_num': 2},
        'uuid': str(game.get('uuid', ''))}


def _actions(game: dict) -> list[dict]:
    """生成客户端可消费的 action_v2_record 战斗链。"""
    return [{'action_id': index, 'action_type': 2,
        'action_battle_info': {'action_list': [
            {'key': row['unique_id'], 'value': -10, 'value2': 0}]}}
        for index, row in enumerate(game.get('chess_list', []), 1)]


def _match(user: dict) -> Match | None:
    """按存档中的对局编号取得进程内对局。"""
    match_id = str(_state(user).get('match_id', ''))
    return _MATCHES.get(match_id) if match_id else None


def _new_game(user: dict, match_id: str, seat: int) -> dict:
    """创建联机准备棋盘。"""
    game = make_game(user, 1, 0)
    game.update(uuid=f'{match_id}-{seat}', state=1, round=1)
    return game


def _entry_pushes(match: Match, uid: int) -> list[tuple[int, dict]]:
    """生成匹配成功后的有序推送。"""
    now = int(time.time())
    return [(90001, {'result': 0}), (90115, {'state': 1}),
        (90101, {'player_info': _player_info(match.games[uid])}),
        (90153, {'start_timestamp': now, 'end_timestamp': now + 60})]


def _rank_rows(current: dict | None = None) -> list[dict]:
    """从现有账号存档生成稳定排行榜。"""
    from user_storage import get_user_manager
    manager = get_user_manager()
    users = [manager.get_user_by_uid(int(uid)) for uid in manager.get_meta().get('accounts', {}).values()]
    users = [u for u in users if u]
    if current and not any(u.get('uid') == current.get('uid') for u in users):
        users.append(current)
    users.sort(key=lambda u: (-int(_state(u).get('rank_score', 1000)), int(u.get('uid', 0))))
    rows = []
    for rank, user in enumerate(users, 1):
        state = _state(user)
        state['rank_index'] = rank
        rows.append({'user_id': user.get('uid', 0), 'name': user.get('nick', user.get('account', '玩家')),
            'icon': user.get('icon', 1084), 'icon_frame': user.get('icon_frame', 2001),
            'chessboard_info': _board(state.get('game')), 'rank_index': rank,
            'old_rank_index': rank, 'battle_user_id': 0})
    return rows


def _battle_record(match: Match, uid: int) -> dict:
    """生成 90111 的双方棋盘记录。"""
    enemy = next(other for other in match.users if other != uid)
    enemy_user = match.users[enemy]
    return {'result': 1, 'battle_uid': match.battle_uid, 'user_id1': uid,
        'auto_chessboard_info1': _board(match.games[uid]), 'user_id2': enemy,
        'auto_chessboard_info2': _board(match.games[enemy]), 'max_group_id': match.round,
        'enemy_name': enemy_user.get('nick', enemy_user.get('account', '玩家')),
        'enemy_icon': enemy_user.get('icon', 1084)}


def _settle(match: Match) -> list[OnlineEvent]:
    """按双方当前棋盘计算本地积分，并延迟发送总结算。"""
    uids = list(match.users)
    power = {uid: len(match.games[uid].get('chess_list', [])) * 100 +
        sum(int(row.get('value', 0)) for row in _board(match.games[uid])['base_info_list']) for uid in uids}
    winner = max(uids, key=lambda uid: (power[uid], -uid))
    for uid in uids:
        user, game = match.users[uid], match.games[uid]
        state = _state(user)
        old = int(state['rank_score']); change = 20 if uid == winner else -20
        state['rank_score'] = max(0, old + change)
        state['wins'] += int(uid == winner); state['losses'] += int(uid != winner)
        state['records'].append({'time': int(time.time()), 'auto_chessboard_info': _board(game),
            'rank': 1 if uid == winner else 2, 'rank_score': old,
            'new_rank_score': state['rank_score'], 'point': change, 'point_detail': []})
        state['records'] = state['records'][-100:]
        state.update(state=4, game=deepcopy(game), match_id=match.match_id)
        _save(user)
    rows = _rank_rows()
    events = []
    for uid, session in match.sessions.items():
        if session is None:
            continue
        state = _state(match.users[uid]); change = 20 if uid == winner else -20
        events.extend([OnlineEvent(session, 90139, {'users_info': rows}, 1.0),
            OnlineEvent(session, 90137, {'rank_index': state['rank_index'],
                'total_score': state['rank_score'], 'change_score': change}, 1.0)])
    match.state = 4
    return events


def _finish_prepare(match: Match) -> list[OnlineEvent]:
    """双方准备完成后发送动作链，再发送延迟总结算。"""
    match.state = 2
    events = []
    for uid, session in match.sessions.items():
        if session is None:
            continue
        events.extend([OnlineEvent(session, 90151, {}), OnlineEvent(session, 90115, {'state': 2}),
            OnlineEvent(session, 90113, {'battle_uid': match.battle_uid,
                'battle_info': {'group_id': match.round, 'action_list': _actions(match.games[uid])}}),
            OnlineEvent(session, 90111, {'battle_info': _battle_record(match, uid)})])
    events.extend(_settle(match))
    return events


def _recover(session: object, user: dict) -> Match | None:
    """服务端重启后用存档对局恢复连接，缺少真实对手时仅用于继续查看。"""
    state = _state(user)
    if not state['state']:
        return None
    match = _match(user)
    if match:
        match.sessions[user['uid']] = session; match.users[user['uid']] = user
        match.games[user['uid']] = deepcopy(state.get('game'))
        return match
    match_id = str(state.get('match_id') or uuid.uuid4().hex)
    game = deepcopy(state.get('game') or _new_game(user, match_id, 0))
    bot = {'uid': -1, 'nick': '训练对手', 'account': '训练对手', 'icon': 1084, 'icon_frame': 2001}
    bot_game = _new_game(user, match_id, 1)
    match = Match(match_id, {user['uid']: session, -1: None},
        {user['uid']: user, -1: bot}, {user['uid']: game, -1: bot_game}, set(), state['state'], state['round'], int(time.time()))
    _MATCHES[match_id] = match
    state['match_id'] = match_id
    return match


def online_login_pushes(user: dict) -> list[tuple[int, dict]]:
    """登录时提示存在可恢复的联机对局。"""
    return [(90007, {'can_reload': True})] if _state(user)['state'] else []


def online_disconnect(session: object) -> None:
    """断开连接时移除排队项，保留已匹配存档。"""
    with _LOCK:
        _QUEUE[:] = [item for item in _QUEUE if item is not session]
        user = getattr(session, 'user_data', None)
        if user and not _state(user).get('match_id'):
            _state(user)['state'] = 0
            _save(user)


def online_request(session: object, command: int, request: dict) -> tuple[dict, list[tuple[int, dict]], list[OnlineEvent]]:
    """处理联机请求。"""
    user = session.user_data; uid = int(user['uid'])
    with _LOCK:
        state = _state(user)
        if command == 90002:
            match = _match(user)
            if match and state['state'] in (1, 2):
                return {'result': 0, 'present_time': int(time.time()) + 1}, _entry_pushes(match, uid), []
            if match: _MATCHES.pop(match.match_id, None)
            _QUEUE[:] = [item for item in _QUEUE if item is not session]; _QUEUE.append(session)
            state.update(state=1, match_id='', round=1); _save(user)
            if len(_QUEUE) < 2:
                return {'result': 0, 'present_time': int(time.time()) + 5}, [], []
            first, second = _QUEUE.pop(0), _QUEUE.pop(0)
            match_id = uuid.uuid4().hex
            users = {int(first.user_data['uid']): first.user_data, int(second.user_data['uid']): second.user_data}
            sessions = {int(first.user_data['uid']): first, int(second.user_data['uid']): second}
            games = {}; now = int(time.time() * 1000) & 0x7fffffff
            for seat, (player_uid, player) in enumerate(users.items()):
                games[player_uid] = _new_game(player, match_id, seat)
                _state(player).update(match_id=match_id, state=1, game=deepcopy(games[player_uid]), opponent={'uid': next(x for x in users if x != player_uid)})
                _save(player)
            match = Match(match_id, sessions, users, games, set(), 1, 1, now); _MATCHES[match_id] = match
            other = first if first is not session else second
            other_uid = int(other.user_data['uid'])
            events = [OnlineEvent(other, c, d) for c, d in _entry_pushes(match, other_uid)]
            return {'result': 0, 'present_time': int(time.time()) + 1}, _entry_pushes(match, uid), events
        if command == 90004:
            _QUEUE[:] = [item for item in _QUEUE if item is not session]
            match = _match(user)
            if match:
                for player in match.users.values():
                    _state(player).update(state=0, match_id='', game=None, opponent={}); _save(player)
                _MATCHES.pop(match.match_id, None)
            state.update(state=0, match_id='', game=None, opponent={}); _save(user)
            return {'result': 0}, [], []
        if command == 90008:
            match = _recover(session, user)
            return ({'result': 0}, _entry_pushes(match, uid), []) if match else ({'result': 1}, [], [])
        if command == 90080:
            return {'result': 0, 'record_list': deepcopy(state['records']) + deepcopy(auto_state(user)['records'])}, [], []
        if command == 90082:
            from user_storage import get_user_manager
            target = get_user_manager().get_user_by_uid(int(request.get('user_id', 0)))
            if not target: return {'result': 1}, [], []
            record = deepcopy((_state(target)['records'] or [{'time': 0, 'auto_chessboard_info': _board(None), 'rank': 0, 'rank_score': _state(target)['rank_score'], 'new_rank_score': 0, 'point': 0, 'point_detail': []}])[-1])
            return {'result': 0, 'record': record}, [], []
        match = _match(user)
        if command == 90134:
            from user_storage import get_user_manager
            users = [get_user_manager().get_user_by_uid(int(uid)) for uid in get_user_manager().get_meta().get('accounts', {}).values()]
            users = [u for u in users if u]; users.sort(key=lambda u: -int(_state(u)['rank_score']))
            return {'result': 0, 'all_score_info': [{'user_id': u['uid'], 'name': u.get('nick', u.get('account', '玩家')), 'icon': u.get('icon', 1084), 'icon_frame': u.get('icon_frame', 2001), 'chessboard_info': _board(_state(u).get('game')), 'rank_index': i, 'old_rank_index': i, 'battle_user_id': 0} for i, u in enumerate(users, 1)]}, [], []
        if not match or uid not in match.games: raise ValueError('请先进入决斗王联机对局')
        match.users[uid] = user; match.sessions[uid] = session
        if command == 90126:
            for player in match.users.values(): _state(player).update(state=0, match_id='', game=None, opponent={}); _save(player)
            _MATCHES.pop(match.match_id, None); state.update(state=0, match_id='', game=None, opponent={})
            return {'result': 0}, [], [OnlineEvent(other, 90007, {'can_reload': False}) for other_uid, other in match.sessions.items() if other_uid != uid and other]
        if command == 90108:
            match.ready.add(uid)
            if len(match.ready) == 2: return {'result': 0}, [], _finish_prepare(match)
            state['state'] = 2; return {'result': 0}, [], []
        if command == 90140:
            return {'result': 0}, [], [OnlineEvent(other, 90143, {'user_id': uid, 'pic_id': int(request.get('pic_id', 0))}) for other_uid, other in match.sessions.items() if other_uid != uid and other]
        if command == 90144:
            return {'result': 0, 'export_info': {'round': match.round, 'chess_list': [], 'user_buff': []}}, [], []
        if command not in _ACTION_MAP: raise ValueError('未知决斗王联机请求')
        if int(request.get('round', match.round)) != match.round: raise ValueError('决斗王回合已变化')
        games = auto_state(user)['games']; games['4'] = match.games[uid]
        converted = dict(request); converted['game_type'] = 4
        response, _ = auto_request(user, _ACTION_MAP[command], converted)
        match.games[uid] = games.pop('4'); state['game'] = deepcopy(match.games[uid])
        if command == 90104: response['chess_list'] = converted.get('chess_list', [])
        return response, [(90101, {'player_info': _player_info(match.games[uid])})], []
