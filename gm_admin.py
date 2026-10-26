"""仅本机访问的 GM 网页接口，复用游戏的发放与存档逻辑。"""

import csv
from io import StringIO
import ipaddress
import json
from pathlib import Path
import time
from urllib.parse import urlsplit, parse_qs

from account_unlocks import unlock_developer, illustrated_data
from communications import send_mail, announcement_store, save_announcement
from currency_data import materialize_currencies, build_currency_balances
from gm_activity_reset import activity_options, activity_pushes, reset_activity
from item_catalog import item_catalog
import protocol_coverage
import user_storage

WEB_DIR = Path(__file__).resolve().parent / 'gm_web'


def backup_dir():
    """返回 GM 存档备份目录，按调用时的 DATA_DIR 解析以便测试隔离。"""
    return user_storage.DATA_DIR / 'gm' / 'backups'


def item_csv():
    """生成可供 Excel 打开的 UTF-8 BOM 物品查阅表。"""
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['物品ID', '名称', '类型', '子类型', '说明'])
    for item, row in sorted(item_catalog().items()):
        fields = [item, row['name'], row['type_name'], row['sub_type'], row['description']]
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in fields])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def account_view(user, online):
    """只返回管理所需资料，排除登录令牌及私密连接字段。"""
    fields = ('uid', 'account', 'nick', 'level', 'gold', 'diamond', 'stamina', 'flower', 'flower_ios', 'flower_free',
              'currencies', 'materials', 'heroes', 'servants', 'reserve', 'reserve_teams', 'battle_progress',
              'tasks', 'task_points', 'profile', 'unlocked_frames', 'unlocked_decorations', 'scene_views',
              'hero_preferences', 'equip_proposals', 'chips', 'shop_purchases', 'shop_stock', 'mails', 'sign', 'chapter_v2')
    data = {key: user[key] for key in fields if key in user}
    data['online'] = user['uid'] in online
    data['currency_rows'] = [{'id': item, 'name': item_catalog().get(item, {}).get('name', str(item)), 'num': amount}
                             for item, amount in build_currency_balances(user).items()]
    data['counts'] = {'heroes': len(user.get('heroes', [])), 'skins': sum(len(h['skins']) for h in user.get('heroes', [])),
                      'servants': len(illustrated_data(user)['servant_info']), 'mails': len(user.get('mails', []))}
    return data


def audit(manager, action, uid, details):
    """记录成功的管理操作，避免记录令牌和完整邮件正文。"""
    directory = user_storage.DATA_DIR / 'gm'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'audit.json'
    with manager._lock:
        rows = manager._load_json(path, [])
        rows.append({'time': int(time.time()), 'action': action, 'uid': uid, 'details': details})
        manager._save_json(path, rows[-1000:])


def backup_rows(uid: int) -> list:
    """列出指定账号的存档备份，按时间倒序返回摘要。"""
    directory = backup_dir()
    if not directory.is_dir():
        return []
    rows = []
    for path in directory.glob(f'{uid}-*.json'):
        payload = _read_backup(path)
        if payload is None:
            continue
        stat = path.stat()
        user = payload.get('user', {})
        rows.append({'name': path.name, 'time': int(stat.st_mtime),
                     'size': stat.st_size, 'nick': user.get('nick', ''),
                     'level': user.get('level', 0), 'note': payload.get('note', '')})
    rows.sort(key=lambda row: row['time'], reverse=True)
    return rows


def _read_backup(path):
    """读取并解析单个备份文件，损坏或缺失时返回 None 以便跳过。"""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def create_backup(manager, uid: int, note: str = '') -> dict:
    """备份单个账号当前存档，写入带时间戳的独立文件。

    同一秒内连续备份（例如回滚前自动备份紧跟用户手动备份）会撞文件名，
    因此追加纳秒后缀，避免后一次覆盖前一次导致存档无法找回。
    """
    with manager._lock:
        user = manager.get_user_by_uid(uid)
        if not user:
            raise ValueError('账号不存在')
        directory = backup_dir()
        directory.mkdir(parents=True, exist_ok=True)
        name = f'{uid}-{time.strftime("%Y%m%d-%H%M%S", time.localtime())}-{time.time_ns() % 1000000:06d}.json'
        manager._save_json(directory / name, {'time': int(time.time()), 'note': note, 'user': user})
    return {'name': name}


def restore_backup(manager, uid: int, name: str) -> None:
    """按备份文件恢复账号存档，校验 UID 一致且备份内容可解析。"""
    if '/' in name or '\\' in name or '..' in name:
        raise ValueError('备份名无效')
    path = backup_dir() / name
    if not path.is_file():
        raise ValueError('备份不存在')
    payload = _read_backup(path)
    if payload is None:
        raise ValueError('备份无法解析')
    user = payload.get('user')
    if not isinstance(user, dict) or user.get('uid') != uid:
        raise ValueError('备份与账号不匹配')
    with manager._lock:
        # 覆盖前先留一份当前状态，避免误恢复后无法找回。
        create_backup(manager, uid, '回滚前自动备份')
        manager.save_user(user)


def resident_progress(user: dict) -> dict:
    """汇总常驻活动与主要养成进度，供 GM 后台只读查看。"""
    resident = user.get('resident', {}) or {}
    data = {'activity_modules': sorted(resident.keys())}
    chess = resident.get('autochess', {}) or {}
    if chess:
        data['autochess'] = {key: chess.get(key) for key in
                             ('game_type', 'stage_id', 'wins', 'prepare_max_body',
                              'classic_rules_version', 'state') if key in chess}
        data['autochess']['chess_count'] = len(chess.get('chess_list', []) or [])
    board = resident.get('board', {}) or {}
    if board:
        data['board'] = {key: board.get(key) for key in ('chapter_id', 'pos', 'total_steps') if key in board}
    data['online'] = bool((resident.get('autochess', {}) or {}).get('online'))
    for name, key in (('rogue', 'rogue'), ('skuld', 'skuld'), ('music', 'music'),
                      ('race', 'race'), ('pub', 'pub'), ('hodur', 'hodur')):
        module = resident.get(key)
        if isinstance(module, dict) and module:
            data[name] = {'keys': sorted(module.keys())[:20]}
    hero_count = len(user.get('heroes', []) or [])
    data['summary'] = {'heroes': hero_count,
                       'servants': len(illustrated_data(user)['servant_info']),
                       'tasks_done': sum(1 for t in (user.get('tasks', {}) or {}).values()
                                         if t.get('complete_flag') == 1),
                       'sign_days': len((user.get('sign', {}) or {}).get('days', []) or [])}
    return data


def gm_response(request_line, body, headers, peer, sync_account, sync_notice, online):
    """验证本机来源和写请求，再分发有限管理接口；返回正文、类型与状态码。"""
    def response(value, status=200):
        """编码统一 JSON 结果。"""
        return json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', status

    method, target, _ = request_line.split(' ', 2)
    url = urlsplit(target)
    try:
        address = ipaddress.ip_address(peer[0])
        host = urlsplit('http://' + headers.get('host', '')).hostname
        if not address.is_loopback or host not in ('localhost', '127.0.0.1', '::1'):
            return response({'error': 'GM 后台仅允许本机访问'}, 403)
        origin = headers.get('origin')
        if origin and origin != 'http://' + headers.get('host', '') and origin != 'https://' + headers.get('host', ''):
            return response({'error': '请求来源无效'}, 403)
        path = url.path
        if path in ('/gm', '/gm/', '/gm/app.js', '/gm/style.css'):
            if method != 'GET':
                return response({'error': '请求方法无效'}, 405)
            filename = {'/gm': 'index.html', '/gm/': 'index.html', '/gm/app.js': 'app.js', '/gm/style.css': 'style.css'}[path]
            kind = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[filename]
            return (WEB_DIR / filename).read_bytes(), kind + '; charset=utf-8', 200
        manager = user_storage.get_user_manager()
        query = parse_qs(url.query)
        if method == 'GET':
            if path == '/gm/api/items.csv':
                return item_csv(), 'text/csv; charset=utf-8', 200
            if path == '/gm/api/items':
                search = query.get('q', [''])[0].casefold()
                page = max(0, int(query.get('page', ['0'])[0]))
                rows = [v for v in item_catalog().values() if search in str(v['id']) or search in v['name'].casefold() or search in v['type_name']]
                return response({'total': len(rows), 'items': rows[page*100:(page+1)*100]})
            if path == '/gm/api/state':
                users = [manager.get_user_by_uid(int(p.stem)) for p in user_storage.USERS_DIR.glob('*.json') if p.stem.isdigit()]
                return response({'users': [account_view(u, online) for u in users if u],
                    'announcements': announcement_store(manager)['items'],
                    'audit': manager._load_json(user_storage.DATA_DIR / 'gm' / 'audit.json', [])[-100:],
                    'item_count': len(item_catalog())})
            if path == '/gm/api/backups':
                target = int(query.get('uid', ['10001'])[0])
                return response({'backups': backup_rows(target)})
            if path == '/gm/api/progress':
                target = int(query.get('uid', ['10001'])[0])
                user = manager.get_user_by_uid(target)
                if not user:
                    raise ValueError('账号不存在')
                return response({'progress': resident_progress(user)})
            if path == '/gm/api/activity-options':
                target = int(query.get('uid', ['10001'])[0])
                user = manager.get_user_by_uid(target)
                if not user:
                    raise ValueError('账号不存在')
                return response({'activities': activity_options(user)})
            if path == '/gm/api/protocols':
                summary = protocol_coverage.fallback_summary()
                return response({'fallback': summary,
                    'report': protocol_coverage.build_report().splitlines()[:24],
                    'implemented': len(protocol_coverage.implemented_commands())})
            if path == '/gm/api/activity-periods':
                from activity_periods import period_options
                return response({'periods': period_options(manager)})
            return response({'error': '接口不存在'}, 404)
        if method != 'POST' or path != '/gm/api/action':
            return response({'error': '请求方法或接口无效'}, 405)
        if headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            return response({'error': '写请求须使用 JSON'}, 415)
        values = json.loads(body)
        if not isinstance(values, dict):
            raise ValueError('请求体须为对象')
        action, uid = values.get('action'), values.get('uid', 10001)
        if type(uid) is not int or uid < 0:
            raise ValueError('UID 无效')
        if action == 'activity_periods':
            from activity_periods import PERIOD_NAMES, period_activity_pushes, save_period_settings
            save_period_settings(manager, values.get('hours'))
            for target in online:
                user = manager.get_user_by_uid(target)
                pushes = [*period_activity_pushes(PERIOD_NAMES),
                          *(push for key in PERIOD_NAMES
                            for push in activity_pushes(user, key))]
                sync_account(target, pushes)
            message = '刷新周期已保存；更改的玩法从现在开始计时，不立即清空进度'
            details = '；'.join(f'{PERIOD_NAMES[key]} {hours} 小时' for key, hours in values['hours'].items())
        elif action == 'command':
            command = values.get('command')
            if not isinstance(command, str):
                raise ValueError('指令须为文本')
            ok, message = manager.apply_gm_command(uid, command)
            if not ok:
                raise ValueError(message)
            sync_account(uid)
            details = command
        elif action == 'unlock':
            with manager._lock:
                user = manager.get_user_by_uid(uid)
                if not user or (uid != 10001 and user.get('account') != 'Developer'):
                    raise ValueError('仅可补齐 Developer 账号')
                user.pop('unlock_version', None)
                materialize_currencies(user)
                unlock_developer(user)
                manager.save_user(user)
            sync_account(uid)
            message = details = '已补齐角色、皮肤、钥从与装饰；保留现有场景'
        elif action == 'mail':
            recipients = send_mail(manager, uid, values)
            for recipient in recipients:
                sync_account(recipient)
            message = f'已向 {len(recipients)} 个账号发送邮件'
            details = values['title']
        elif action in ('announcement', 'delete_announcement'):
            save_announcement(manager, values, delete=action == 'delete_announcement')
            sync_notice()
            message = details = '公告已保存' if action == 'announcement' else '公告已删除'
        elif action == 'backup':
            created = create_backup(manager, uid, str(values.get('note', '')))
            message = details = f'已备份到 {created["name"]}'
        elif action == 'restore':
            restore_backup(manager, uid, str(values['name']))
            sync_account(uid)
            message = details = f'已恢复备份 {values["name"]}'
        elif action == 'reset_activity':
            with manager._lock:
                user = manager.get_user_by_uid(uid)
                if not user:
                    raise ValueError('账号不存在')
                activity = values.get('activity')
                name, pushes = reset_activity(
                    user, activity,
                    area_id=values.get('area_id'), group_id=values.get('group_id'))
                manager.save_user(user)
            sync_account(uid, pushes)
            message = f'{name}已切换' if activity == 'dream' else f'{name}已重置'
            details = name
        else:
            raise ValueError('不支持的管理操作')
        audit(manager, action, uid, details)
        return response({'message': message})
    except (ValueError, KeyError, TypeError) as error:
        return response({'error': str(error)}, 400)
