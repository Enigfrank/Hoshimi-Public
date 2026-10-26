"""游园街全部本地协议入口与客户端增量同步。"""

from copy import deepcopy

from backhome_catalog import backhome_catalog
from backhome_canteen import CANTEEN_COMMANDS, canteen_request, tick_canteen
from backhome_dance import DANCE_COMMANDS, dance_request, sequence_statistics
from backhome_data import backhome_detail, backhome_simple, backhome_state, enter_backhome, tick_fatigue, update_home_suits
from backhome_idol import IDOL_COMMANDS, idol_info, idol_pve_data, idol_request
from backhome_rooms import ROOM_COMMANDS, room_request
from backhome_tasks import home_event
from backhome_visits import VISIT_COMMANDS, visit_request, visit_status
from battle_rewards import inventory_pushes
from currency_data import build_currency_balances

BACKHOME_COMMANDS = {58002} | CANTEEN_COMMANDS | DANCE_COMMANDS | IDOL_COMMANDS | ROOM_COMMANDS | VISIT_COMMANDS
BACKHOME_ONEWAY_COMMANDS = {58196}


def backhome_initial_pushes(user: dict) -> list[tuple]:
    """登录初始化游园街、访客、偶像竞技、编舞和图鉴，列表只包含实际状态。"""
    state = backhome_state(user)
    shared = deepcopy(state['idol']['shared'])
    for row in shared:
        row['statistics'] = sequence_statistics(user, row['uid'])
    simple = backhome_simple(user)
    visits = visit_status(user)
    simple['received_be_visited_gift_num'] = visits['received_be_visited_gift_num']
    return [(58001, simple), (58065, visits), (58169, idol_info(user)), (58189, idol_pve_data(user)),
            (58191, {'get_id_list': list(state['idol']['get_id_list']), 'point': state['idol']['point']}),
            (58195, {'sequence_list': deepcopy(state['idol']['sequences']), 'shared_sequence_list': shared}),
            (58203, {'collection_list': [{'type': int(i), 'viewed_id_list': list(ids)} for i, ids in state['collections'].items()]})]


def backhome_request(user: dict, command: int, request: dict) -> tuple:
    """全部状态在请求克隆中变更，外层完成真实协议编码后才写盘。"""
    state = backhome_state(user)
    previous = deepcopy(state['canteen'])
    changed_npc = tick_fatigue(user)
    if command == 58002 and user.get('level', 1) < backhome_catalog()['settings']['backhome_unlock_level'][0]:
        raise ValueError('管理员等级不足以进入游园街')
    auto = tick_canteen(user)
    auto['npc_change'] = changed_npc
    settled = state['idol'].get('pending', {}).get('settled', False)
    feed = sum(h['total_feed_times'] for h in state['heroes'].values())
    balance = build_currency_balances(user)[39]
    if command == 58002:
        enter_backhome(user)
        response = backhome_detail(user)
    elif command in ROOM_COMMANDS:
        response = room_request(user, command, request)
    elif command in CANTEEN_COMMANDS:
        response = canteen_request(user, command, request)
    elif command in VISIT_COMMANDS:
        response = visit_request(user, command, request)
    elif command in DANCE_COMMANDS:
        response = dance_request(user, command, request)
    elif command in IDOL_COMMANDS:
        response = idol_request(user, command, request)
    else:
        raise ValueError('游园街指令未支持')
    if command in BACKHOME_ONEWAY_COMMANDS:
        return response, []
    pushes = []
    if command in (58002, 58130, 58136):
        previous_suits = list(state['backhome_suit_id_list'])
        update_home_suits(user)
        if previous_suits != state['backhome_suit_id_list']:
            pushes.append((58157, {'suit_id_list': state['backhome_suit_id_list']}))
    if command == 58166:
        home_event(user, 2009)
        pushes.append((58171, {'exercise_times_info': idol_info(user)['exercise_times_info']}))
    elif command == 58164 and not settled:
        if request['battle_type'] != 3:
            home_event(user, 2011)
            if response['battle_result'] == 1:
                home_event(user, 2010)
        pushes.append((58189, idol_pve_data(user)))
    elif command == 58106 and response['earnings']:
        home_event(user, 2020)
        home_event(user, 2021, response['earnings'])
    elif command == 58120:
        home_event(user, 2020)
        home_event(user, 2021, build_currency_balances(user)[39] - balance)
    elif command == 58138:
        home_event(user, 2003, sum(h['total_feed_times'] for h in state['heroes'].values()) - feed)
    elif command == 58154:
        home_event(user, 2008)
    elif command == 58018:
        home_event(user, 2002, len(request['pos']))
    if command == 58002:
        # 首次进入的配置物品先同步，58003回调才据此进入场景。
        pushes.extend(inventory_pushes(user)[:2])
    elif command not in (58050, 58054, 58058, 58148, 58180, 58182, 58184, 58214, 58216):
        pushes.extend(inventory_pushes(user)[:2])
    if previous != state['canteen'] and command != 58002:
        auto['earnings'] = state['canteen']['business']['earnings']
        auto['signature_dish'] = deepcopy(state['canteen']['signature_dish'])
        auto['food'] = deepcopy(list(state['food'].values()))
        pushes.append((58023, auto))
    if command in (58060, 58056):
        pushes.append((58065, visit_status(user)))
    if command in (58166, 58164, 58106, 58120, 58138, 58154, 58018):
        from gameplay_data import task_data
        pushes.append((28001, task_data(user)))
    if command == 58192:
        pushes.append((58191, {'get_id_list': list(state['idol']['get_id_list']), 'point': state['idol']['point']}))
    if command not in (58002, 58058, 58050, 58054, 58148, 58180, 58182, 58184, 58212, 58214, 58216):
        pushes.append((58027, {'fatigue_list': [
            {'archives_id': h['archives_id'], 'fatigue': h['fatigue']} for h in state['heroes'].values()]}))
    return response, pushes
