"""个人名片、看板、剧情阅读、大厅设置与角色语音校验。"""

from copy import deepcopy
from datetime import date
import time

from gameplay_protocol import decode_message, read_client_data
from proto_bridge import get_bridge
from skin_scene_data import active_trials, skin_available, usable_skin_catalog

PROFILE_FIELDS = {
    32012: ('sign', 'sign'), 32014: ('heroes', 'heroes'),
    32016: ('poster_girl', 'poster_girl'), 32032: ('icon_id', 'icon'),
    32034: ('iconframe_id', 'icon_frame'), 32056: ('page_id', 'sticker_background'),
    32114: ('id', 'information_background_id'), 32116: ('tags', 'used_tag_list'),
    32120: ('chat_bubble', 'chat_bubble'), 32068: ('info', 'table_setting'),
    32070: ('module', 'table_setting_module'),
    32108: ('poster_background_id', 'poster_background_id'),
}
PROFILE_COMMANDS = set(PROFILE_FIELDS) | {
    12002, 12026, 12030, 23012, 32038, 32044, 32122, 32124, 32126, 32128, 32130, 32132, 32134, 32052, 32058,
}


def poster_girl_terms(user: dict) -> list[dict]:
    """读取看板角色任期记录，查询本身不修改账号。"""
    return [row for row in (user.get('poster_girl_terms') or [])
            if isinstance(row, dict) and int(row.get('hero_id', 0)) > 0]


def switch_poster_girl(user: dict, hero_id: int, previous: int | None = None) -> None:
    """看板角色真正变化时关闭旧任期并开启新任期，供助理任期累计使用。

    重复提交同一个角色不算变化，既不会关闭也不会新建任期，避免
    ``32016`` 与 ``32132`` 反复切换同一角色刷出任期天数。
    ``previous`` 是切换前的看板角色，缺省时以存档里的名片为准。
    """
    current = int((user.get('profile') or {}).get('poster_girl', 0)) if previous is None else int(previous)
    if not hero_id or hero_id == current:
        return
    now = int(time.time())
    for row in poster_girl_terms(user):
        if int(row.get('hero_id', 0)) == current and not row.get('end_time'):
            row['end_time'] = now
    user.setdefault('poster_girl_terms', []).append(
        {'hero_id': hero_id, 'start_time': now, 'end_time': 0})


def profile_data(user: dict) -> dict:
    """以客户端有效默认值补齐名片，再恢复已保存的选择和布局。"""
    result = decode_message(32009, get_bridge().encode_sc_32009(), 'sc')
    result.update(deepcopy(user.get('profile', {})))
    result['poster_background_list'] = [row for row in result['poster_background_list']
                                      if not row['lasted_time'] or row['lasted_time'] > int(time.time())]
    result['icon'] = user.get('portrait', result['icon'])
    result['icon_frame'] = user.get('icon_frame', result['icon_frame'])
    frames = {entry['id'] for entry in result['icon_frame_list']}
    for item in user.get('unlocked_frames', []):
        if item not in frames:
            result['icon_frame_list'].append({'id': item, 'lasted_time': 0})
    for field, items in user.get('unlocked_decorations', {}).items():
        rows = result[field]
        if field in ('all_sticker_list', 'all_background_list', 'all_foreground_list'):
            rows.extend(item for item in items if item not in rows)
        else:
            owned = {row['id'] for row in rows}
            rows.extend({'id': item, 'lasted_time': 0, 'obtain_time': 0} for item in items if item not in owned)
            if field == 'poster_background_list':
                for row in rows:
                    if row['id'] in items:
                        row['lasted_time'] = 0
    scenes = {row['id']: row for row in result['poster_background_list']}
    # 存档 JSON 的插入顺序在 Go 侧 dictionary 中不可恢复，两侧统一按场景编号
    # 升序追加，避免同一账号下 poster_background_list 的 repeated 字段顺序漂移。
    for scene, expiry in sorted(active_trials(user, 'scene_trials').items()):
        if scene not in scenes:
            result['poster_background_list'].append({'id': scene, 'lasted_time': expiry, 'obtain_time': 0})
    if result['poster_background_id'] not in {row['id'] for row in result['poster_background_list']}:
        result['poster_background_id'] = 6000
    return result


def profile_request(user: dict, command: int, request: dict, rewards: list | None = None) -> dict:
    """处理个人资料与角色语音校验协议；保存结构与登录初始化结构保持一致。

    ``32044`` 只校验角色是否可播放语音，不修改存档。
    ``rewards`` 为可选出参：``sc_12003`` 只有 ``result``，心链剧情首读发放的物品
    无法通过协议字段下发，调用方据此补发库存推送。
    """
    response = {'result': 0}
    if command == 32044:
        # 语音界面只传角色编号，播放前校验角色已拥有且已解锁。
        # 客户端以 result=0 为成功，成功后播放本地语音文件。
        role_id = int(request['role_id'])
        if not any(int(hero.get('id', 0)) == role_id and hero.get('unlock', 1)
                   for hero in user.get('heroes', [])):
            raise ValueError('角色未拥有，无法播放语音')
        return response
    profile = profile_data(user)
    if command in (32122, 32130):
        rows = request['random_info_list'] if command == 32122 else [
            {'random_type': request['type'], 'random_list': request['random_list']}]
        catalog = usable_skin_catalog()
        heroes = {hero['id']: hero for hero in user.get('heroes', [])}
        for row in rows:
            if row['random_type'] == 1:
                for skin in row.get('random_list', []):
                    hero = heroes.get(catalog.get(skin))
                    if not hero or not skin_available(user, hero, skin):
                        raise ValueError('随机看板皮肤未拥有或缺少界面资源')
    if command == 12026:
        music = request['id']
        cfg = read_client_data(f"require('MusicRecordCfg')[{int(music)}] or {{}}") if music else {}
        if music and (not cfg or cfg['ableBGM'] != 1):
            raise ValueError('大厅背景音乐无效或不可设为背景音乐')
        user['bgm_id'] = music
        return response
    if command == 32134:
        row = next((r for r in user.get('scene_views', []) if r['poster_background_id'] == request['poster_background_id']), None)
        if not row or request['sub_poster_background'] not in row['sub_background_list']:
            raise ValueError('场景视角未解锁')
        row['current_sub_background'] = request['sub_poster_background']
        return response
    if command == 32058:
        ids = request['reward_id_list']
        owned = set(profile['all_sticker_list']+profile['all_background_list']+profile['all_foreground_list'])
        owned.update(int(item) for item, num in user.get('materials', {}).items() if num > 0)
        claimed = profile.setdefault('admitted_suit_reaward_list', [])
        rewards = []
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('贴纸套装领奖列表无效')
        for item in ids:
            cfg = read_client_data(f"require('StickerSuitCfg')[{int(item)}] or {{}}")
            if not cfg or item in claimed or not set(cfg['content']) <= owned:
                raise ValueError('贴纸套装未集齐或已领奖')
            rewards.extend({'id': i, 'num': n} for i, n in cfg['reward'])
        from battle_rewards import grant_rewards
        grant_rewards(user, rewards)
        claimed.extend(ids)
        user['profile'] = profile
        return {'result': 0, 'reward_list': rewards}
    if command == 32052:
        skin_id = request['skin_id']
        cfg = read_client_data(f"require('SkinCfg')[{int(skin_id)}] or {{}}")
        hero = next((h for h in user['heroes'] if h['id'] == cfg.get('hero')), None)
        if not hero or skin_id not in hero['skins'] or not cfg.get('gift'):
            raise ValueError('皮肤赠礼不存在或尚未拥有')
        claimed = user.setdefault('skin_gifts', [])
        if skin_id in claimed:
            raise ValueError('皮肤赠礼已领取')
        from battle_rewards import grant_rewards
        grant_rewards(user, [{'id': i, 'num': n} for i, n in cfg['gift']])
        claimed.append(skin_id)
        return response
    if command == 32132:
        selected = request['hero_id']
        cfg = read_client_data(f"require('SkinCfg')[{int(selected)}] or {{}}")
        hero_id = cfg.get('hero', selected)
        hero = next((h for h in user['heroes'] if h['id'] == hero_id), None)
        if not hero or not skin_available(user, hero, selected):
            raise ValueError('看板角色皮肤未拥有')
        background = request['background_id']
        if background not in {b['id'] for b in profile['poster_background_list']}:
            raise ValueError('大厅背景未拥有')
        # 任期记录必须对比切换前的看板角色：profile 已经写上新值，
        # 依赖 user['profile'] 读取会误判成"角色未变化"而丢失任期。
        previous = int(profile.get('poster_girl', 0))
        profile['poster_girl'] = hero_id
        profile['poster_background_id'] = background
        hero['skin_id'] = selected
        user['profile'] = profile
        switch_poster_girl(user, hero_id, previous)
        return response
    if command == 12002:
        story = request['story_id']
        if story <= 0:
            raise ValueError('剧情 ID 无效')
        # 誓约心链剧情首读附带配置奖励，必须在写入已读列表之前判定是否首读。
        from hero_oath import read_story_reward
        granted = read_story_reward(user, story)
        stories = user.setdefault('read_stories', [])
        if story not in stories:
            stories.append(story)
        if rewards is not None:
            rewards.extend(granted)
        return response
    if command == 12030:
        date(2000, request['month'], request['day'])
        user['birthday'] = dict(request)
        return response
    if command == 23012:
        nick = request['nick'].strip()
        if not nick or len(nick) > 32:
            raise ValueError('昵称无效')
        user['nick'] = nick
        user['is_changed_nick'] = 1
        return {**response, 'is_changed_nick': 1,
                'system_change_nick_times': user.get('system_change_nick_times', 0)}
    if command in PROFILE_FIELDS:
        source, target = PROFILE_FIELDS[command]
        value = request[source]
        if target in ('poster_girl', 'heroes'):
            owned = {h['id'] for h in user.get('heroes', [])}
            ids = value if isinstance(value, list) else [value]
            if any(item not in owned for item in ids):
                raise ValueError('展示角色未拥有')
        if target in ('icon', 'icon_frame', 'information_background_id', 'chat_bubble'):
            item = read_client_data(f"require('ItemCfg')[{int(value)}] or require('ItemCfg2')[{int(value)}] or {{}}")
            expected = {'icon': 11, 'icon_frame': 12, 'information_background_id': 23, 'chat_bubble': 26}
            if not item or item['type'] != expected[target]:
                raise ValueError('个人装饰类型无效')
            field = {'icon': 'icon_list', 'icon_frame': 'icon_frame_list',
                     'information_background_id': 'information_background_list', 'chat_bubble': 'chat_bubble_list'}[target]
            if value not in {row['id'] for row in profile[field]}:
                raise ValueError('个人装饰未拥有')
        if target == 'poster_background_id' and value not in {r['id'] for r in profile['poster_background_list']}:
            raise ValueError('大厅背景未拥有')
        profile[target] = deepcopy(value)
        if target == 'poster_girl':
            switch_poster_girl(user, int(value))
        if target == 'icon':
            user['portrait'] = value
        elif target == 'icon_frame':
            user['icon_frame'] = value
    elif command == 32038:
        by_page = {row['page_id']: row for row in profile['sticker_show_info']}
        by_page.update({row['page_id']: deepcopy(row) for row in request['sticker_show_info']})
        profile['sticker_show_info'] = list(by_page.values())
    elif command == 32122:
        by_type = {row['random_type']: row for row in profile['random_info']}
        by_type.update({row['random_type']: deepcopy(row) for row in request['random_info_list']})
        profile['random_info'] = list(by_type.values())
    else:
        kind = request['type']
        if kind not in (1, 2):
            raise ValueError('随机看板类型无效')
        row = next(r for r in profile['random_info'] if r['random_type'] == kind)
        field = {32124: 'random_model', 32126: 'show_hero_dressing_scene',
                 32128: 'routine_hero_dressing_scene', 32130: 'random_list'}[command]
        row[field] = deepcopy(request['model' if command == 32124 else field])
    user['profile'] = profile
    return response
