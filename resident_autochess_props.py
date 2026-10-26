"""决斗王道具购买与本地配置效果，使用原始89102和目标槽位。"""

from copy import deepcopy

from resident_catalog import resident_config
from resident_autochess_buffs import buff_record, sync_chess_buffs, chess_exp_thresholds, chess_prop_count, sync_prop_growth
from resident_autochess_round import chess_stat, chess_set_stat
from resident_autochess_catalog import ordinary_chess_pool


def add_prop_buff(game: dict, target: dict, buff_id: int, item_id: int) -> None:
    """保存道具来源与唯一技能实例，不能与原生等级技能混淆。"""
    game.setdefault('next_buff_uid', 1000000)
    buff = buff_record(game['next_buff_uid'], buff_id, target)
    buff.update(source_type=1, source_cfg_id=item_id)
    target['buff_list'].append(buff)
    game['next_buff_uid'] += 1


def add_body(target: dict, atk: int, hp: int) -> None:
    """更新准备状态的真实攻击和生命，购买后及下回合保持。"""
    chess_set_stat(target, 1, chess_stat(target, 1)+atk)
    chess_set_stat(target, 2, chess_stat(target, 2)+hp)


def validate_prop_target(game: dict, cfg: dict, slot: int, repeated_effect: bool = False) -> dict | None:
    """遵循原始CheckUseProp的目标、容量、互斥及经验上限校验。"""
    target = next((r for r in game['chess_list'] if r['index'] == slot), None)
    kind = cfg['relevant_type']
    if kind in (6, 15):
        if target is None or kind == 15 and resident_config('AutoChessCfg', target['chess_id'])['type'] in (1, 3):
            raise ValueError('道具目标为空或不能用于管理员')
    elif slot != 0:
        raise ValueError('全局道具必须使用零目标槽位')
    if kind == 10 and not any(r['shop_type'] in (0, 2) for r in game['shop_items']):
        raise ValueError('商店没有道具所需的棋子')
    for buff_id in cfg['buff_list']:
        buff = resident_config('AutoChessBuffCfg', buff_id)
        effects = target['buff_list'] if target is not None else game.get('global_buffs', [])
        group = buff['repeated'] or []
        if not repeated_effect and group and any(b['buff_id'] in group for b in effects):
            raise ValueError('道具效果不能重复叠加')
        if buff['action_type'] == 7 and any(resident_config('AutoChessBuffCfg', b['buff_id'])['action_type'] == 7 for b in effects):
            raise ValueError('目标已拥有护盾')
        if buff['action_type'] == 9:
            if len(game['chess_list'])+buff['param'][0] > game['attrs'][4] or not any(r['shop_type'] == 0 for r in game['shop_items']):
                raise ValueError('偷取棋子缺少空槽或商店棋子')
        if buff['action_type'] == 30 and len(game['chess_list']) >= game['attrs'][4]:
            raise ValueError('召唤棋子缺少空槽')
        if buff['action_type'] == 10 and target is not None:
            maximum = chess_exp_thresholds(target,game.get('exp_discount',0))[-1]
            if chess_stat(target, 3)+buff['param'][0] > maximum:
                raise ValueError('道具经验超过棋子上限')
        if buff['action_type'] == 16:
            star = resident_config('AutoChessCfg', target['chess_id'])['star']+buff['param'][0]
            if not ordinary_chess_pool(game, star):
                raise ValueError('没有合法更高星级的棋子')
        if buff['action_type'] == 17 and len(game['chess_list']) <= 1:
            raise ValueError('移除棋子后没有可转移属性的目标')
        if buff['action_type'] == 15:
            if not ordinary_chess_pool(game, game['attrs'][11]+buff['param'][1]):
                raise ValueError('当前商店没有更高星级的配置')
            if not any(r['shop_type'] == 0 and not r['is_lock'] for r in game['shop_items']):
                raise ValueError('请先解冻一个商店棋子')
    return target


def prop_is_legal(game: dict, cfg: dict, slot: int, repeated_effect: bool = False) -> bool:
    """自动施法和额外生效只跳过明确的目标限制，其他异常仍交给事务回滚。"""
    try:
        validate_prop_target(game,cfg,slot,repeated_effect)
    except ValueError:
        return False
    return True


def apply_prop_effect(game: dict, item_id: int, target: dict | None, buff_id: int) -> None:
    """以当前配置执行道具效果，抽选采用存档编号的固定顺序。"""
    buff = resident_config('AutoChessBuffCfg', buff_id)
    kind, params = buff['action_type'], buff['param']
    from resident_autochess import chess_row, shop_item, refill_shop
    if buff['moment'] == 11:
        owner = next(r for r in game['chess_list'] if resident_config('AutoChessCfg', r['chess_id'])['type'] == 1)
        add_prop_buff(game, owner, buff_id, item_id)
        game.setdefault('battle_props', []).append(buff_id)
        game.setdefault('global_buffs', []).append(deepcopy(owner['buff_list'][-1]))
        return
    if kind == 1:
        targets = game['chess_list'] if target is None else [target]
        for row in targets:
            add_body(row, params[0][1], params[1][1])
    elif kind == 9:
        stolen = next(r for r in game['shop_items'] if r['shop_type'] == 0)
        slot = next(i for i in range(1, game['attrs'][4]+1) if all(r['index'] != i for r in game['chess_list']))
        row = chess_row(game['next_uid'], stolen['id'], slot)
        row['chess_attr_list'] = deepcopy(stolen['chess_attr_list'])
        add_body(row, 1, 1)
        bonus = game.get('team_bonus', [0,0]);add_body(row, bonus[0], bonus[1])
        sync_chess_buffs(row)
        game['next_uid'] += 1
        game['chess_list'].append(row)
        game['shop_items'].remove(stolen)
    elif kind == 6:
        if buff_id == 201:
            game['coin_limit'] = game.get('coin_limit', 10)+params[2]
        elif buff_id == 299:
            game['coin_reset'] = game.get('coin_reset', 10)+params[2]
        else:
            game.setdefault('next_round_gold', 0)
            game['next_round_gold'] += params[2]
    elif kind == 24:
        bonus = game.setdefault('shop_bonus', [0, 0])
        bonus[0] += params[1][0]; bonus[1] += params[1][1]
        for row in game['shop_items']:
            if row['shop_type'] == 0:
                holder = {'chess_attr_list': row['chess_attr_list']}
                add_body(holder, params[1][0], params[1][1])
    elif kind == 15:
        refill_shop(game)
        rows = ordinary_chess_pool(game, game['attrs'][11]+params[1])
        if rows:
            item = next(r for r in game['shop_items'] if r['shop_type'] == 0 and not r['is_lock'])
            replacement = shop_item(item['shop_unique_id'],rows[0]['id'],item['index'],0,game)
            bonus = game.get('shop_bonus',[0,0]);add_body({'chess_attr_list':replacement['chess_attr_list']},bonus[0],bonus[1])
            item.update(replacement)
    elif kind == 5:
        bonus = game.setdefault('team_bonus', [0, 0])
        bonus[0] += params[2][0]; bonus[1] += params[2][1]
        for row in game['chess_list']:
            add_body(row, params[2][0], params[2][1])
    elif kind == 10:
        if target is None:
            chosen = next(r for r in game['shop_items'] if r['shop_type'] in (0, 2))
            holder = {'chess_attr_list': chosen['chess_attr_list']}
            row = {'chess_id':chosen['id'],'chess_attr_list':chosen['chess_attr_list']}
            chess_set_stat(holder,3,chess_exp_thresholds(row,game.get('exp_discount',0))[-1])
        else:
            chess_set_stat(target, 3, chess_stat(target, 3)+params[0])
            sync_chess_buffs(target,game.get('exp_discount',0))
    elif kind == 16:
        old = resident_config('AutoChessCfg', target['chess_id'])
        chosen = ordinary_chess_pool(game, old['star']+params[0])[0]
        native_ids = {i for row in old['level_buffs'] for i in row}
        target['buff_list'] = [b for b in target['buff_list'] if b['buff_id'] not in native_ids]
        add_body(target, chosen['body'][0]-old['body'][0], chosen['body'][1]-old['body'][1])
        target['chess_id'] = chosen['id']
        from resident_autochess_global import apply_catalog_body
        apply_catalog_body(game,target,chosen['id'])
        sync_chess_buffs(target,game.get('exp_discount',0))
    elif kind == 17:
        recipient = next(r for r in game['chess_list'] if r['unique_id'] != target['unique_id'])
        add_body(recipient, chess_stat(target, 1), chess_stat(target, 2))
        game['chess_list'].remove(target)
    elif kind == 30:
        pool = ordinary_chess_pool(game, params[0])
        if not pool:
            raise ValueError('当前活动没有合法召唤棋子')
        slot = next(i for i in range(1, game['attrs'][4]+1) if all(r['index'] != i for r in game['chess_list']))
        row = chess_row(game['next_uid'], pool[0]['id'], slot)
        from resident_autochess_global import apply_catalog_body
        apply_catalog_body(game,row,pool[0]['id'])
        bonus = game.get('team_bonus', [0,0]);add_body(row, bonus[0], bonus[1])
        game['chess_list'].append(row)
        game['next_uid'] += 1
    elif kind in (7, 41):
        add_prop_buff(game, target, buff_id, item_id)
        if kind == 41:
            saved = next(b for b in target['buff_list'] if b['buff_id'] == buff_id)
            saved['other_param'] = [{'key': 1, 'value': 1}, {'key': 2, 'value': 1}]
    else:
        raise ValueError('道具效果尚未确定')


def cast_prop(game: dict, cfg: dict, slot: int) -> None:
    """一次合法使用触发道具倍率和技能，额外效果遵守目标、容量与经验限制。"""
    validate_prop_target(game,cfg,slot)
    owners=deepcopy(game['chess_list'])
    repeats=1
    for row in owners:
        for buff in row['buff_list']:
            effect=resident_config('AutoChessBuffCfg',buff['buff_id'])
            if effect['action_type']!=13:continue
            uses=game.setdefault('prop_double_uses',{})
            key=str(row['unique_id'])
            record=uses.setdefault(key,{'round':game['round'],'count':0})
            if record['round']!=game['round']:record.update(round=game['round'],count=0)
            if record['count']<effect['effective_time']:
                record['count']+=1;repeats+=effect['param'][0]
    for index in range(repeats):
        if index>0 and not prop_is_legal(game,cfg,slot,True):break
        target=validate_prop_target(game,cfg,slot,index>0)
        for buff_id in cfg['buff_list']:
            if buff_id!=199:apply_prop_effect(game,cfg['id'],target,buff_id)
    game['props_used'] = game.get('props_used', 0)+1
    growth={r['unique_id'] for r in owners if any(resident_config('AutoChessBuffCfg',b['buff_id'])['action_type']==36 for b in r['buff_list'])}
    for row in game['chess_list']:
        if row['unique_id'] in growth:sync_prop_growth(row,chess_prop_count(row)+1)
    from resident_autochess_transactions import prepare_event
    prepare_event(game,4,owners=owners)


def purchase_prop(game: dict, item: dict, slot: int) -> None:
    """原始成本只扣一次，额外生效不额外收费，重复购买和非法目标先拒绝。"""
    cfg=resident_config('AutoChessItemCfg',item['id'])
    validate_prop_target(game,cfg,slot)
    free=game['attrs'].get(17,0)
    cost=0 if free else cfg['shop_price']
    if game['attrs'][1]<cost:raise ValueError('决斗王金币不足')
    cast_prop(game,cfg,slot)
    if item in game['shop_items']:game['shop_items'].remove(item)
    game['attrs'][1]-=cost
    if free:game['attrs'][17]-=1
