"""决斗王准备阶段的批量购买与PVE失败逆转。"""

from copy import deepcopy
from resident_catalog import resident_config
from resident_autochess_round import chess_setting, chess_stat, chess_set_stat
from resident_autochess_buffs import sync_chess_buffs, chess_exp_thresholds, chess_prop_count, sync_prop_growth


def merge_chess(target: dict, source: dict, adjustment: int = 0) -> None:
    """遵循原始拖拽限制合并同名非满级棋子，累计实际属性并同步等级技能。"""
    if target['unique_id'] == source['unique_id'] or target['chess_id'] != source['chess_id']:
        raise ValueError('合并棋子重复或编号不匹配')
    cfg = resident_config('AutoChessCfg', target['chess_id'])
    if cfg['type'] != 0:
        raise ValueError('只能合并同种普通棋子')
    limits = chess_exp_thresholds(target,adjustment)
    if limits and any(chess_stat(row,3) >= limits[-1] for row in (target, source)):
        raise ValueError('满级棋子不能继续合并')
    props = max(chess_prop_count(target),chess_prop_count(source))
    for key in (1, 2, 3):
        chess_set_stat(target, key, chess_stat(target, key)+chess_stat(source, key))
    sync_chess_buffs(target,adjustment)
    if props:sync_prop_growth(target,props)


def purchase_chess(game: dict, item: dict, slot: int) -> None:
    """从服务端商品属性购买，允许合法同名合并，全部校验完成才扣费并移除商品。"""
    if not 1 <= slot <= game['attrs'][4]:
        raise ValueError('棋盘位置无效')
    cfg = resident_config('AutoChessCfg', item['id'])
    free = item['shop_type'] == 0 and game['attrs'].get(18,0)>0
    cost = 0 if item['shop_type'] == 2 or free else cfg['shop_price']
    if cost > game['attrs'][1]:
        raise ValueError('决斗王金币不足')
    from resident_autochess import chess_row
    incoming = chess_row(game['next_uid'], item['id'], slot)
    incoming['chess_attr_list'] = deepcopy(item['chess_attr_list'])
    bonus = game.get('team_bonus', [0, 0])
    chess_set_stat(incoming, 1, chess_stat(incoming, 1)+bonus[0])
    chess_set_stat(incoming, 2, chess_stat(incoming, 2)+bonus[1])
    sync_chess_buffs(incoming,game.get('exp_discount',0))
    target = next((r for r in game['chess_list'] if r['index'] == slot), None)
    owners=deepcopy(game['chess_list'])
    if target is not None:
        merge_chess(target,incoming,game.get('exp_discount',0))
    else:
        game['chess_list'].append(incoming)
    game['attrs'][1] -= cost
    if free:game['attrs'][18] -= 1
    game['next_uid'] += 1
    game['shop_items'].remove(item)
    from resident_autochess_transactions import prepare_effect
    for buff in incoming['buff_list']:
        effect=resident_config('AutoChessBuffCfg',buff['buff_id'])
        if effect['moment']==1:prepare_effect(game,target if target is not None else incoming,effect)
    if item['shop_type'] == 2:
        game['reward_queue'] = [r for r in game.get('reward_queue',[]) if r['shop_unique_id'] != item['shop_unique_id']]
        from resident_autochess_scene import fill_reward_shop
        fill_reward_shop(game)
    if item['shop_type']==0:
        from resident_autochess_transactions import prepare_event
        prepare_event(game,3,target if target is not None else incoming,owners)


def buy_all_chess(game: dict) -> None:
    """只购买合法空槽可容纳且金币足够的棋子，按当前商品顺序决定批量购买结果。"""
    bought = False
    for item in list(game['shop_items']):
        if item['shop_type'] not in (0, 2):
            continue
        cfg = resident_config('AutoChessCfg', item['id'])
        if cfg['type'] != 0:
            continue
        slot = next((i for i in range(1, game['attrs'][4]+1)
            if all(r['index'] != i for r in game['chess_list'])), 0)
        if not slot:
            break
        if item['shop_type'] != 2 and not game['attrs'].get(18,0) and cfg['shop_price'] > game['attrs'][1]:
            continue
        purchase_chess(game, item, slot)
        bought = True
    if not bought:
        raise ValueError('当前没有可购买的棋子或空槽')


def retry_failed_round(game: dict) -> None:
    """PVE最后一次失败尚未确认时继续当轮，保留编队和胜场，旧战报不再用于结算。"""
    pending = game.get('pending_round')
    if game['game_type'] != 0 or game['state'] != 5 or not pending or pending['replay']['result'] != 2:
        raise ValueError('当前对局不能逆转')
    if game['restart_times'] >= chess_setting('auto_chess_pve_restart_times'):
        raise ValueError('本局逆转次数已用完')
    game['restart_times'] += 1
    if 'prepare_chess' in pending:
        game['chess_list'] = deepcopy(pending['prepare_chess'])
    game['attrs'][1] += chess_setting('auto_chess_pve_restart_add_coin')
    game['player']['battle_uid'] = game['next_battle_uid']
    game.pop('pending_round')
    game.pop('last_settle_pushes', None)
    game['state'] = 1
