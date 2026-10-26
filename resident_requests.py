"""常驻接口路由与操作后的定向同步。"""

from copy import deepcopy

from resident_catalog import append_unique, resident_config
from resident_data import resident_state, resident_pushes
from resident_minigames import MINIGAME_COMMANDS, minigame_request, require_activity
from resident_rogue import ROGUE_COMMANDS, rogue_request, rogue_data
from resident_world import WORLD_COMMANDS, world_request
from resident_board import BOARD_COMMANDS, board_request
from resident_autochess import AUTOCHESS_COMMANDS, auto_request
from resident_card import CARD_COMMANDS, card_request
from resident_sidegames import SIDE_COMMANDS, side_request
from resident_match import ONLINE_COMMANDS
from resident_hodur import HODUR_COMMANDS, hodur_request

RESIDENT_COMMANDS = ROGUE_COMMANDS | MINIGAME_COMMANDS | WORLD_COMMANDS | BOARD_COMMANDS | AUTOCHESS_COMMANDS | CARD_COMMANDS | SIDE_COMMANDS | ONLINE_COMMANDS | {79602, 89422, 89424, 89426,
    90072, 90080, 89206, 12100, 85106, 28834}


def resident_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """分发到明确实现的业务；未知请求仍由服务端原有失败分支处理。"""
    if command in (69004, 69006):
        from resident_hodur_rank import hodur_rank_request
        return hodur_rank_request(user, command, request), []
    if command in BOARD_COMMANDS:
        return board_request(user, command, request)
    if command in AUTOCHESS_COMMANDS:
        return auto_request(user, command, request)
    if command in CARD_COMMANDS:
        return card_request(user, command, request)
    if command in SIDE_COMMANDS:
        return side_request(user, command, request)
    if command in WORLD_COMMANDS:
        return world_request(user, command, request)
    if command in ROGUE_COMMANDS:
        result = rogue_request(user, command, request)
        from resident_tasks import update_rogue_tasks
        update_rogue_tasks(user)
        return result, [] if command in (89620, 89622, 89626, 89628) else [(89601, rogue_data(user))]
    if command in HODUR_COMMANDS:
        return hodur_request(user, command, request)
    state = resident_state(user)
    result = {'result': 0}
    commands = set()
    if command in MINIGAME_COMMANDS:
        result = minigame_request(user, command, request)
        commands = {24052: {24051}, 24054: {24051}, 12120: {12119},
            60054: {60107, 85101, 24051, 60201}, 61048: {61047}, 61050: {60107, 85101},
            84332: {84331}, 84334: {84331}, 89002: set(), 89004: set(),
            89006: set(), 89008: set(), 89402: {89401}, 89404: {89401},
            89702: {89701}, 89704: {89701}, 89706: {89701}, 89708: {89701}}[command]
    elif command == 79602:
        require_activity(request, 323641)
        rows = state['world'].setdefault(str(request['activity_id']), {})
        for row in request['stage_list']:
            cfg = resident_config('SandPlayMapCfg', row['stage_id'])
            if cfg['activity_id'] != request['activity_id']:
                raise ValueError('地图不属于当前活动')
            rows[str(row['stage_id'])] = deepcopy(row)
    elif command == 90072:
        result['user_wish_list'] = []
    elif command in (90080, 89206):
        # 没有参与过对局的账号应返回空记录，而不是内部错误。
        if command==89206:
            from resident_autochess_pvp import classic_history
            return classic_history(state['autochess']),[]
        result['record_list']=state['autochess'].get('records',[])
    elif command == 12100:
        if not request['language'] or len(request['language']) > 32:
            raise ValueError('语言设置无效')
        user['language'] = request['language']
    elif command == 28834:
        append_unique(user.setdefault('qworld_unlocked_functions', []), request['sys_id'])
    elif command == 85106:
        rows = state['unlock_states']
        row = next((r for r in rows if r['id'] == request['id']), None)
        if row is None:
            row = {'id': request['id'], 'opts': {}}
            rows.append(row)
        row['opts'][str(request['opt']['key'])] = request['opt']['value']
    else:
        raise ValueError('常驻请求尚未实现')
    return result, [(c, v) for c, v in resident_pushes(user) if c in commands] if commands else []
