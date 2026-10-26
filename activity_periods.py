"""GM 全局固定周期设置及账号到期重置，不使用查询时滑动的截止时间。"""

import time

import user_storage

PERIOD_NAMES = {'dream': '梦境再构', 'core': '迭代校验',
                'polyhedron': '多维变量', 'neural': '神经矩阵·并发序列'}
DEFAULT_ANCHOR = 334800  # 1970-01-05 北京时间 05:00，周一业务日。
PERIOD_ACTIVITIES = {'polyhedron': (4407301, 4407302), 'neural': (111, 4443304, 4443404, 4443504)}


def period_settings(manager=None) -> dict:
    """读取全服设置；未配置时四类玩法按周一北京时间 05:00 刷新。"""
    manager = manager or user_storage.get_user_manager()
    saved = manager._load_json(user_storage.DATA_DIR / 'gm' / 'activity-periods.json', {})
    return {key: saved.get(key, {'hours': 168, 'anchor': DEFAULT_ANCHOR}) for key in PERIOD_NAMES}


def period_window(activity: str, settings=None, now=None) -> tuple[int, int]:
    """同一周期内起止时间恒定；重启和重复查询不会延长截止时间。"""
    cfg = (settings or period_settings())[activity]
    now = int(time.time()) if now is None else now
    duration = cfg['hours'] * 3600
    start = cfg['anchor'] + max(0, (now - cfg['anchor']) // duration) * duration
    return start, start + duration


def period_options(manager=None) -> dict:
    """返回 GM 编辑值及下一次刷新时刻，不包含账号或敏感配置。"""
    settings = period_settings(manager)
    return {key: {'name': name, **settings[key], 'next_refresh': period_window(key, settings)[1]}
            for key, name in PERIOD_NAMES.items()}


def save_period_settings(manager, values: dict) -> None:
    """全部校验后原子保存；仅更改的玩法从保存时起计时，不立即清档。"""
    if not isinstance(values, dict) or set(values) != set(PERIOD_NAMES):
        raise ValueError('请提供四类玩法的完整刷新周期')
    if any(type(hours) is not int or not 1 <= hours <= 8760 for hours in values.values()):
        raise ValueError('刷新周期须为 1 至 8760 的整数小时')
    with manager._lock:
        settings, now = period_settings(manager), int(time.time())
        for key, hours in values.items():
            if settings[key]['hours'] != hours:
                settings[key] = {'hours': hours, 'anchor': now}
        path = user_storage.DATA_DIR / 'gm' / 'activity-periods.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        manager._save_json(path, settings)


def reset_period(user: dict, activity: str) -> None:
    """只清除当期进度及领奖事实，永久养成、库存与其他玩法不变。"""
    from gm_activity_reset import _reset_core, _reset_polyhedron
    kinds = {'dream': (10, 100), 'core': (67,), 'polyhedron': (52,), 'neural': (93,)}[activity]
    records = user.get('battle_progress', {})
    for key in list(records):
        if key.split(':', 1)[0] in {str(kind) for kind in kinds}:
            del records[key]
    if activity == 'dream':
        user.pop('boss_star_rewards', None)
    elif activity == 'core':
        _reset_core(user)
    elif activity == 'polyhedron':
        if user.get('polyhedron'):
            _reset_polyhedron(user)
            user['polyhedron']['decision']['apply_id_list'] = []
    else:
        user.pop('neural_matrix', None)
    battle = user.get('battle_session')
    if battle and battle['common']['type'] in kinds:
        battle['period_expired'] = True


def refresh_periods(user: dict, manager=None, now=None) -> bool:
    """补齐周期标记并到期重置；首次升级保留旧进度，离线跨多期只重置一次。"""
    settings = period_settings(manager)
    now = int(time.time()) if now is None else now
    markers = user.setdefault('activity_periods', {})
    changed = False
    for key, cfg in settings.items():
        start, end = period_window(key, settings, now)
        marker = {**cfg, 'next': end}
        previous = markers.get(key)
        if previous == marker:
            continue
        if previous:
            same = previous['anchor'] == cfg['anchor'] and previous['hours'] == cfg['hours']
            expired = (same and previous['next'] <= now or not same and (
                previous['next'] <= cfg['anchor'] or now >= cfg['anchor'] + cfg['hours'] * 3600))
            if expired:
                reset_period(user, key)
        markers[key] = marker
        changed = True
    return changed


def period_pushes(user: dict, previous: dict) -> list[tuple]:
    """仅下发标记改变的周期活动，避免重发账号或邮箱初始化。"""
    from gm_activity_reset import activity_pushes
    current = user.get('activity_periods', {})
    before = previous.get('activity_periods', {})
    changed = [key for key in PERIOD_NAMES if current.get(key) != before.get(key)]
    return [*period_activity_pushes(changed),
            *(push for key in changed for push in activity_pushes(user, key))]


def period_activity_pushes(activities) -> list[tuple]:
    """增量更新周期活动倒计时，避免全量 11001 清空客户端换装缓存。"""
    from activity_data import build_available_activities
    ids = {aid for key in activities for aid in PERIOD_ACTIVITIES.get(key, ())}
    if not ids:
        return []
    return [(11003, {'activity': row}) for row in build_available_activities() if row['activity_id'] in ids]
