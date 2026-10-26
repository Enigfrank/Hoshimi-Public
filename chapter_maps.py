"""章节事件地图初始化与客户端移动、阅读和回溯请求的持久化。"""

from copy import deepcopy
from functools import lru_cache
import random

from gameplay_protocol import read_client_data

CHAPTER_MAP_COMMANDS = {24030, 24032, 24034, 24036, 24038, 24062, 24064, 24066, 24068, 56002, 12100, 52014}
CHAPTER_ONEWAY_COMMANDS = {24034, 24036, 24038, 56002}


@lru_cache(maxsize=1)
def map_catalog():
    """读取真实地图、位置及事件 ID，避免接受任意编号。"""
    return read_client_data('''(function()
        local r={maps={},locations={},events={},legacy={},legacy_locations={},conditions={},chapters={}}
        for name,key in pairs({ChapterV2MapCfg='maps',ChapterV2MapLocationCfg='locations',ChapterV2MapEventCfg='events',
            ChapterMapCfg='legacy',ChapterLocationCfg='legacy_locations',ConditionCfg='conditions',ChapterCfg='chapters'}) do
            local c=require(name);for _,id in ipairs(c.all) do r[key][#r[key]+1]=c[id] end
        end
        r.scan_stage=require('GameSetting').chapter19_scan_unlock.value[1]
        r.detector_stage=require('GameSetting').chapter19_activate.value[1]
        return r
    end)()''')


def chapter_map_pushes(user):
    """恢复事件列表、当前地图位置、扫描记录及已处理红点。"""
    catalog = map_catalog()
    default = catalog['maps'][0]['default_location']
    return [(24061, deepcopy(user.get('chapter_v2', {'now_location': default, 'event_list': [], 'backtrack_list': []}))),
            (24029, deepcopy(user.get('chapter_map', {'map_info': [], 'detector_info': []}))),
            (56001, {'red_dot': user.get('read_red_dots', []), 'client_finished_red_dot': []})]


def chapter_state(user):
    """恢复地图事件与回溯状态，保持已有历史完成记录。"""
    return user.setdefault('chapter_v2', {'now_location': map_catalog()['maps'][0]['default_location'],
        'event_list': [], 'backtrack_list': []})


def cleared_stage(user, stage):
    """读取实际剧情通关事实，普通账号入口解锁不算完成事件。"""
    return user.get('battle_progress', {}).get(f'1:{stage}', {}).get('clear_times', 0) > 0


def event_map(event):
    """通过事件位置取得地图和所属章节。"""
    location = next(c for c in map_catalog()['locations'] if c['id'] == event['location'])
    return next(c for c in map_catalog()['maps'] if c['id'] == location['map'])


def event_completed(user, event, historical=False):
    """区分历史完成和当前回溯时间线，跨地图按同一章节恢复快照。"""
    state = chapter_state(user)
    if historical:
        return event in state['event_list']
    cfg = next(c for c in map_catalog()['events'] if c['id'] == event)
    chapter = event_map(cfg)['chapter_client_id']
    track = next((r for r in state['backtrack_list']
        if next(m for m in map_catalog()['maps'] if m['id'] == r['map_id'])['chapter_client_id'] == chapter), None)
    if not track:
        return event in state['event_list']
    start = next(c for c in map_catalog()['events'] if c['id'] == track['start_event'])
    return event in track['backtrack_event_list'] or event in state['event_list'] and cfg['time_stamp'] < start['time_stamp']


def chapter_condition(user, condition):
    """按客户端条件配置检查事件全部完成、任一完成及历史完成。"""
    if condition == 0:
        return True
    cfg = next((c for c in map_catalog()['conditions'] if c['id'] == condition), None)
    if not cfg:
        raise ValueError('章节条件不存在')
    kind, params = cfg['type'], cfg['params']
    if kind == 12602:
        return all(event_completed(user, event) for event in params)
    if kind == 12604:
        return any(event_completed(user, event) for event in params)
    if kind == 12606:
        return all(event_completed(user, event, True) for event in params)
    if kind == 12603:
        # 此条件对应客户端 IsUnlockChapter 的排期判断，前置事件另由地图条件限制。
        from activity_data import build_available_activities
        chapters = map_catalog()['chapters']
        chapter = next((c for c in chapters if c['id'] == params[0]), None)
        return chapter is not None and (chapter['unlock_activity_id'] == 0 or
            any(c['activity_id'] == chapter['unlock_activity_id'] for c in build_available_activities()))
    raise ValueError('章节条件类型尚未支持')


def require_event(user, cfg):
    """禁止越过显示和解锁条件直接完成事件。"""
    map_cfg = event_map(cfg)
    if not all(chapter_condition(user, c) for c in map_cfg['unlock_condition']):
        raise ValueError('章节地图尚未解锁')
    if not all(chapter_condition(user, c) for c in list(cfg['display_condition']) + list(cfg['unlock_condition'])):
        raise ValueError('章节事件前置条件不足')


def finish_map_event(user, cfg):
    """完成事件并追加当前回溯事实，保留历史事件供图鉴和任务使用。"""
    state = chapter_state(user)
    if cfg['id'] not in state['event_list']:
        state['event_list'].append(cfg['id'])
    chapter = event_map(cfg)['chapter_client_id']
    for track in state['backtrack_list']:
        if next(c for c in map_catalog()['maps'] if c['id'] == track['map_id'])['chapter_client_id'] == chapter:
            if cfg['id'] not in track['backtrack_event_list']:
                track['backtrack_event_list'].append(cfg['id'])
            start = next(c for c in map_catalog()['events'] if c['id'] == track['start_event'])
            if cfg['time_stamp'] >= start['time_stamp']:
                following = sorted((c for c in map_catalog()['events'] if c['type'] in (1, 2)
                    and event_map(c)['chapter_client_id'] == chapter and c['time_stamp'] > cfg['time_stamp']),
                    key=lambda c: c['time_stamp'])
                next_event = next((c for c in following if all(chapter_condition(user, condition)
                    for condition in list(c['display_condition']) + list(c['unlock_condition']))), None)
                if next_event:
                    # 客户端按 start_event 的时间戳重建临时完成表，不读取 replay 列表。
                    track['start_event'] = next_event['id']
                else:
                    state['backtrack_list'] = [r for r in state['backtrack_list'] if r is not track]


def complete_chapter_stage(user, stage):
    """真实剧情或战斗结算推进主支线事件，禁止依靠入口解锁预发任务。"""
    for cfg in map_catalog()['events']:
        if cfg['type'] in (1, 2) and cfg['params'] and cfg['params'][0] == stage:
            finish_map_event(user, cfg)
    # 十九章战斗线索仅在相应支线关卡获胜后写入。
    for cfg in map_catalog()['legacy_locations']:
        if cfg['clue'] and stage in cfg['sub_stage_list']:
            save_legacy_clue(user, cfg, cfg['clue'][0])


def validate_chapter_stage(user, stage):
    """检查复杂地图关卡的事件前置；重播仍以当前时间线判断。"""
    for cfg in map_catalog()['events']:
        if cfg['type'] in (1, 2) and cfg['params'] and cfg['params'][0] == stage:
            require_event(user, cfg)


def update_chapter_tasks(user):
    """专用任务只取真实战斗记录，保存领奖标志并允许重启恢复。"""
    from gameplay_data import client_catalog
    for cfg in client_catalog()['tasks']:
        if cfg['type'] not in (801, 802, 803, 804) or cfg['condition'] != 401:
            continue
        stage = cfg['additional_parameter'][0]
        value = user.get('battle_progress', {}).get(f'1:{stage}', {}).get('clear_times', 0)
        row = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        row['progress'] = max(row['progress'], min(cfg['need'], value))


def legacy_row(user, map_id):
    """按地图恢复十九章扫描、事件和线索列表。"""
    state = user.setdefault('chapter_map', {'map_info': [], 'detector_info': []})
    row = next((r for r in state['map_info'] if r['map_id'] == map_id), None)
    if row is None:
        row = {'map_id': map_id, 'scan_location_list': [], 'clue_location_list': [], 'event_list': [], 'clue_list': []}
        state['map_info'].append(row)
    return row


def save_legacy_clue(user, cfg, clue):
    """保存配置内的线索和地点关系，随机线索只抽取一次。"""
    map_cfg = next(c for c in map_catalog()['legacy'] if cfg['id'] in c['location_list'])
    row = legacy_row(user, map_cfg['id'])
    known = next((c for c in row['clue_location_list'] if c['location_id'] == cfg['id']), None)
    if known is None:
        known = {'location_id': cfg['id'], 'clue_id': clue}
        row['clue_location_list'].append(known)
    if known['clue_id'] not in row['clue_list']:
        row['clue_list'].append(known['clue_id'])
    return map_cfg['id'], known


def legacy_request(user, command, request):
    """处理十九章扫描、取线索、阅读及探测日期，单向消息不生成响应。"""
    catalog = map_catalog()
    if command == 24036:
        info = request['detector_info']
        if info['status'] != 1 or not any(c['chapter_id'] == info['chapter_id'] and c['day'] == info['day'] for c in catalog['legacy']):
            raise ValueError('章节探测日期或状态无效')
        if not cleared_stage(user, catalog['detector_stage']):
            raise ValueError('章节探测器尚未解锁')
        rows = user.setdefault('chapter_map', {'map_info': [], 'detector_info': []})['detector_info']
        if info not in rows:
            rows.append(deepcopy(info))
        return {'result': 0}
    if command == 24030:
        cfg = next((c for c in catalog['legacy'] if c['id'] == request['map_id']), None)
        if not cfg or not cleared_stage(user, catalog['scan_stage']):
            raise ValueError('章节扫描地图无效或探测器尚未解锁')
        row = legacy_row(user, cfg['id'])
        for location in cfg['location_list']:
            loc = next(c for c in catalog['legacy_locations'] if c['id'] == location)
            if loc['need_scan'] == 1 and location not in row['scan_location_list']:
                row['scan_location_list'].append(location)
        return {'result': 0, 'location_id_list': row['scan_location_list']}
    cfg = next((c for c in catalog['legacy_locations'] if
        command == 24032 and c['id'] == request['location_id'] or
        command == 24034 and request['event_id'] in c['event_list'] or
        command == 24038 and request['clue_id'] in c['clue']), None)
    if not cfg:
        raise ValueError('章节地点、事件或线索不存在')
    map_cfg = next(c for c in catalog['legacy'] if cfg['id'] in c['location_list'])
    row = legacy_row(user, map_cfg['id'])
    if cfg['unlock_stage_id'] and not cleared_stage(user, cfg['unlock_stage_id']):
        raise ValueError('章节地点前置关卡未完成')
    if cfg['need_scan'] == 1 and cfg['id'] not in row['scan_location_list']:
        raise ValueError('章节线索地点尚未扫描')
    if command == 24034:
        if request['event_id'] not in row['event_list']:
            row['event_list'].append(request['event_id'])
        return {'result': 0}
    if not cfg['clue'] or cfg['type'] == 3 and not all(cleared_stage(user, s) for s in cfg['sub_stage_list']):
        raise ValueError('章节线索战斗尚未完成')
    if command == 24038 and cfg['need_scan'] == 1:
        known = next((r for r in row['clue_location_list'] if r['location_id'] == cfg['id']), None)
        if not known or known['clue_id'] != request['clue_id']:
            raise ValueError('章节随机线索未取得')
    clue = random.choice(cfg['clue']) if command == 24032 else request['clue_id']
    map_id, clue_row = save_legacy_clue(user, cfg, clue)
    return {'result': 0, 'map_id': map_id, 'clue_location': clue_row} if command == 24032 else {'result': 0}


def chapter_map_request(user, command, request):
    """校验真实地图编号并保存请求产生的实际状态。"""
    if command == 12100:
        if not request['language'].strip() or len(request['language']) > 32:
            raise ValueError('语言选项无效')
        user['language'] = request['language']
        return {'result': 0}
    if command == 52014:
        from account_unlocks import read_illustrated
        return read_illustrated(user, request)
    if command == 56002:
        value = request['red_dot']
        if value <= 0:
            raise ValueError('红点编号无效')
        rows = user.setdefault('read_red_dots', [])
        if value not in rows:
            rows.append(value)
        return {'result': 0}
    catalog = map_catalog()
    if command in (24030, 24032, 24034, 24036, 24038):
        return legacy_request(user, command, request)
    state = chapter_state(user)
    if command == 24064:
        location = request['new_location']
        cfg = next((c for c in catalog['locations'] if c['id'] == location), None)
        if not cfg:
            raise ValueError('章节位置不存在')
        map_cfg = next(c for c in catalog['maps'] if c['id'] == cfg['map'])
        if not all(chapter_condition(user, c) for c in list(map_cfg['unlock_condition']) + list(cfg['first_unlock_condition'])):
            raise ValueError('章节地图或地点前置条件不足')
        state['now_location'] = location
    elif command in (24062, 24066):
        event = request['event_id']
        cfg = next((c for c in catalog['events'] if c['id'] == event), None)
        if not cfg:
            raise ValueError('章节事件不存在')
        if command == 24062:
            require_event(user, cfg)
            if cfg['type'] in (1, 2) and not cleared_stage(user, cfg['params'][0]):
                raise ValueError('章节战斗事件须通过真实结算完成')
            finish_map_event(user, cfg)
        else:
            if event not in state['event_list']:
                raise ValueError('事件尚未完成，不能回溯')
            location = next(c for c in catalog['locations'] if c['id'] == cfg['location'])
            map_cfg = event_map(cfg)
            if not chapter_condition(user, map_cfg['timeline_unlock_condition']):
                raise ValueError('章节时间线尚未解锁')
            state['backtrack_list'] = [r for r in state['backtrack_list'] if
                next(m for m in catalog['maps'] if m['id'] == r['map_id'])['chapter_client_id'] != map_cfg['chapter_client_id']]
            state['backtrack_list'].append({'map_id': location['map'], 'start_event': event, 'backtrack_event_list': []})
            state['now_location'] = cfg['location']
    else:
        if request['map_id'] not in {c['id'] for c in catalog['maps']}:
            raise ValueError('章节地图不存在')
        chapter = next(m for m in catalog['maps'] if m['id'] == request['map_id'])['chapter_client_id']
        state['backtrack_list'] = [r for r in state['backtrack_list'] if
            next(m for m in catalog['maps'] if m['id'] == r['map_id'])['chapter_client_id'] != chapter]
    return {'result': 0}
