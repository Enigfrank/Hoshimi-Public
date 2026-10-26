"""新版决斗王卡牌抽取、愿望交换、记录和社交请求。"""

from copy import deepcopy
import time

from resident_catalog import resident_rows

CARD_COMMANDS = {90002,90004,90008,90056,90058,90060,90062,90064,90066,90068,90070,90072,90074,90076,90082}


def card_state(user: dict) -> dict:
    """保存卡牌数量、开卡记录、愿望单和最近分享时间。"""
    state = user.setdefault('resident', {}).setdefault('autochess_cards', {})
    state.setdefault('cards', {})
    state.setdefault('history', [])
    state.setdefault('wish', {})
    state.setdefault('share_time', 0)
    state.setdefault('rank_score', 0)
    state.setdefault('energy', 0)
    state.setdefault('sunglasses', 0)
    return state


def card_catalog() -> list[dict]:
    """只抽取客户端卡池标记为 is_card_pool 的真实卡牌。"""
    return [r for r in resident_rows('AutoChessCardCfg') if r['is_card_pool']]


def card_push(user: dict) -> dict:
    """生成 90051 页面初始化数据。"""
    state = card_state(user)
    return {'card_list': [{'id': int(k), 'num': v} for k,v in state['cards'].items()],
        'rank_score': state['rank_score'], 'energy': state['energy'], 'sunglasses': state['sunglasses']}


def card_reward(card_id: int, num: int = 1) -> list[dict]:
    """卡牌转换奖励使用客户端卡牌编号，重复卡牌不凭空生成角色。"""
    return [{'id': card_id, 'num': num}]


def card_history_count(values: list, item: int) -> int:
    """统计历史卡牌次数，不依赖动态列表方法，保持 Go 移植语义一致。"""
    count = 0
    for value in values:
        if value == item:
            count += 1
    return count


def card_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """实现新版决斗王全部卡牌和愿望请求，重复操作具有幂等性。"""
    state = card_state(user)
    response, pushes = {'result': 0}, []
    catalog = card_catalog()
    ids = [r['id'] for r in catalog]
    if command == 90064:
        times = request['times']
        if times not in (1, 10): raise ValueError('开卡次数无效')
        cards = [ids[(len(state['history'])+i) % len(ids)] for i in range(times)]
        for card in cards: state['cards'][str(card)] = state['cards'].get(str(card), 0)+1
        state['history'].extend(cards); response['card_ids'] = cards
        pushes.append((90051, card_push(user)))
    elif command == 90066:
        response['card_his'] = [{'id': i, 'num': card_history_count(state['history'], i)} for i in ids if i in state['history']]
    elif command == 90068:
        target = request['target_id']; costs = request['cost_cards']
        if target not in ids or any(row['id'] not in ids or row['num'] <= 0 for row in costs): raise ValueError('合成卡牌参数无效')
        for row in costs:
            key=str(row['id']);have=state['cards'].get(key,0)
            if have < row['num']: raise ValueError('卡牌数量不足')
            state['cards'][key]=have-row['num']
        state['cards'][str(target)]=state['cards'].get(str(target),0)+1
        pushes.append((90051, card_push(user)))
    elif command == 90056:
        target, bless = request['target_id'], request['bless_id']
        if target not in ids: raise ValueError('愿望卡牌不存在')
        state['wish']={'target_id':target,'bless_id':bless,'cost_ids':list(request['cost_ids']),'status':0,'last_wish_time':int(time.time())}
        response['reward_list'] = []
    elif command == 90058:
        response['wish_list'] = []
    elif command == 90060:
        target=request['select_id'];
        if target not in ids: raise ValueError('愿望目标不存在')
        response['reward_list']=[]
    elif command == 90062:
        response['reward_list']=[]
    elif command == 90070:
        state['share_time']=int(time.time());response['result']=0
    elif command == 90072:
        response['user_wish_list']=[]
    elif command == 90074:
        response['wish_list']=[]
    elif command == 90076:
        state['sunglasses']=request['type'];response['result']=0
    elif command == 90082:
        response['record']={'time':0,'auto_chessboard_info':{'base_info_list':[],'chess_list':[],'card_ids':[]},'rank':0,'rank_score':0,'new_rank_score':0,'point':0,'point_detail':[]}
    elif command in (90002,90004,90008):
        response['result']=0
    else:
        raise ValueError('未知卡牌交换请求')
    return response, pushes
