"""本机账号的公开资料查询，不向社交协议泄露认证字段。"""

from user_storage import get_user_manager


def local_users(user: dict) -> list[dict]:
    """只遍历本机实际账号，用请求中的最新账号替代其磁盘副本。"""
    manager = get_user_manager()
    result = []
    for uid in sorted(set(manager.get_meta().get('accounts', {}).values())):
        row = user if uid == user['uid'] else manager.get_user_by_uid(uid)
        if row:
            result.append(row)
    return result


def local_user(user: dict, uid: int) -> dict:
    """精确查询存在的本机玩家，不把未知UID回退成Developer。"""
    target = user if uid == user['uid'] else get_user_manager().get_user_by_uid(uid)
    if target is None:
        raise ValueError('目标玩家不存在')
    return target


def public_profile(user: dict) -> dict:
    """生成客户端允许展示的昵称、头像和头像框。"""
    return {'nick': user.get('nick', ''), 'icon': user.get('portrait', 2110841),
            'icon_frame': user.get('icon_frame', 2001)}


def public_user(user: dict) -> dict:
    """生成房间与编舞列表使用的公开名片。"""
    return {'user_id': user['uid'], 'profile_base': public_profile(user), 'level': user.get('level', 1),
            'sign': user.get('profile', {}).get('sign', ''),
            'info_background': user.get('profile', {}).get('information_background_id', 7001)}


def recommend_friends(user: dict, request: dict) -> dict:
    """按UID稳定分页推荐真实本机玩家，过滤本人、好友和黑名单。"""
    if request['cmd'] not in (1, 2):
        raise ValueError('好友推荐操作无效')
    excluded = {user['uid']} | set(user.get('friends', [])) | set(user.get('blacklist', []))
    candidates = [u for u in local_users(user) if u['uid'] not in excluded and user['uid'] not in u.get('blacklist', [])]
    start = user.get('friend_recommend_page', 0) if request['cmd'] == 2 else 0
    start = start % len(candidates) if candidates else 0
    selected = (candidates + candidates)[start:start + min(10, len(candidates))] if candidates else []
    if request['cmd'] == 2:
        user['friend_recommend_page'] = (start + 10) % len(candidates) if candidates else 0
    return {'result': 0, 'recommend_list': [
        {'user_id': u['uid'], 'base_info': public_profile(u), 'online_state': 0,
         'sign': u.get('profile', {}).get('sign', ''), 'level': u.get('level', 1),
         'info_background': u.get('profile', {}).get('information_background_id', 7001)} for u in selected]}
