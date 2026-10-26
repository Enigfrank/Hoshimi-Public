"""决斗王准备阶段的买卖、道具与回合技能；计数保存于实际对局。"""

from copy import deepcopy

from resident_catalog import resident_config
from resident_autochess_round import chess_stat, chess_set_stat


def prepare_targets(game: dict, owner: dict, target: int, seed: int, event: dict | None = None) -> list:
    """以实际槽位解析邻位、锁定商品、护盾和稳定抽选目标。"""
    rows=sorted(game['chess_list'],key=lambda r:r['index'])
    if target==111:return [owner] if owner in rows else []
    if target==112:return rows
    if target==113:return [r for r in rows if r['unique_id']!=owner['unique_id']]
    if target==13:return [event] if event is not None and event in rows else []
    if target==14:return [r for r in game['shop_items'] if r['shop_type']==0 and r['is_lock']]
    if target==120:return [r for r in rows if r['index']-owner['index'] in (-1,1)]
    if target==124:return rows[:1]
    if target in (127,128,129):return [r for r in rows if r['index']>owner['index']][:target-126]
    if target==151:
        return [r for r in rows if any(resident_config('AutoChessBuffCfg',b['buff_id'])['action_type']==7 for b in r['buff_list'])]
    if target in (114,116,117):
        pool=[r for r in rows if r['unique_id']!=owner['unique_id']] if target==117 else rows
        offset=seed%len(pool) if pool else 0
        return (pool[offset:]+pool[:offset])[:3 if target==116 else 1]
    return []


def prepare_effect(game: dict, owner: dict, cfg: dict, event: dict | None = None) -> bool:
    """执行本机能确定的准备效果；免费施法不扣商店金币或消费店内商品。"""
    kind,params=cfg['action_type'],cfg['param']
    seed=game['round']+cfg['id']+(event['unique_id'] if event is not None else 0)
    if kind in (5,41):
        targets=prepare_targets(game,owner,params[0],seed,event)
        for row in targets:
            for key in (1,2):chess_set_stat(row,key,chess_stat(row,key)+params[2][key-1])
        return bool(targets)
    if kind==6 and params[1] in (1,16):
        game['attrs'][params[1]]=game['attrs'].get(params[1],0)+params[2]
        return True
    if kind==4:
        if params[0]==2:
            applied=False
            for buff_id in params[1]:
                if prepare_effect(game,owner,resident_config('AutoChessBuffCfg',buff_id),event):applied=True
            return applied
        targets=prepare_targets(game,owner,params[0],seed,event)
        applied=False
        for row in targets:
            for buff_id in params[1]:
                child=resident_config('AutoChessBuffCfg',buff_id)
                if child['action_type']==10:
                    from resident_autochess_buffs import chess_exp_thresholds, sync_chess_buffs
                    limits=chess_exp_thresholds(row,game.get('exp_discount',0))
                    if limits and chess_stat(row,3)<limits[-1]:
                        chess_set_stat(row,3,min(limits[-1],chess_stat(row,3)+child['param'][0]))
                        sync_chess_buffs(row,game.get('exp_discount',0));applied=True
        return applied
    if kind==19 and event is not None and owner in game['chess_list']:
        amount=[chess_stat(event,key) for key in (1,2)]
        saved=game.setdefault('prepare_temporary',{}).setdefault(str(owner['unique_id']),[0,0])
        for key in (1,2):
            chess_set_stat(owner,key,chess_stat(owner,key)+amount[key-1]);saved[key-1]+=amount[key-1]
        return True
    if kind==24:
        bonus=game.setdefault('shop_bonus',[0,0])
        for key in (1,2):bonus[key-1]+=params[1][key-1]
        for row in game['shop_items']:
            if row['shop_type']==0:
                holder={'chess_attr_list':row['chess_attr_list']}
                for key in (1,2):chess_set_stat(holder,key,chess_stat(holder,key)+params[1][key-1])
        return True
    if kind==30:
        from resident_autochess_catalog import ordinary_chess_pool
        from resident_autochess import chess_row
        pool=ordinary_chess_pool(game,params[0]);applied=False
        for i in range(params[1]):
            slot=next((n for n in range(1,game['attrs'][4]+1) if all(r['index']!=n for r in game['chess_list'])),0)
            if not slot or not pool:break
            row=chess_row(game['next_uid'],pool[(seed+i)%len(pool)]['id'],slot)
            from resident_autochess_global import apply_catalog_body
            apply_catalog_body(game,row,row['chess_id'])
            bonus=game.get('team_bonus',[0,0])
            for key in (1,2):chess_set_stat(row,key,chess_stat(row,key)+bonus[key-1])
            game['next_uid']+=1;game['chess_list'].append(row);applied=True
        return applied
    if kind==11:
        from resident_autochess_props import prop_is_legal, cast_prop
        pool=params[1];start=seed%len(pool)
        for item_id in pool[start:]+pool[:start]:
            item=resident_config('AutoChessItemCfg',item_id)
            slots=[r['index'] for r in sorted(game['chess_list'],key=lambda r:r['index'])] if item['relevant_type'] in (6,15) else [0]
            for slot in slots:
                if not prop_is_legal(game,item,slot):continue
                cast_prop(game,item,slot);return True
        return False
    return False


def prepare_event(game: dict, timing: int, event: dict | None = None, owners: list | None = None) -> None:
    """只由实际事务发出事件，技能快照、购买间隔与回合加倍可跨重登恢复。"""
    sources=deepcopy(game['chess_list']) if owners is None else owners
    if timing==9:game['attrs'][16]=0
    extra=sum(resident_config('AutoChessBuffCfg',b['buff_id'])['param'][0] for r in sources for b in r['buff_list']
        if resident_config('AutoChessBuffCfg',b['buff_id'])['action_type']==14
        and resident_config('AutoChessBuffCfg',b['buff_id'])['moment']==10) if timing==10 else 0
    for source in sources:
        owner=next((r for r in game['chess_list'] if r['unique_id']==source['unique_id']),None)
        if owner is None:
            if timing!=5 or event is None or source['unique_id']!=event['unique_id']:continue
            owner=source
        for buff in source['buff_list']:
            cfg=resident_config('AutoChessBuffCfg',buff['buff_id'])
            if cfg['moment']!=timing or cfg['action_type'] in (13,14):continue
            if event is not None and (cfg['enum']==111 and owner['unique_id']!=event['unique_id']
                or cfg['enum']==113 and owner['unique_id']==event['unique_id']):continue
            if cfg['gap']:
                key=f"{owner['unique_id']}:{cfg['id']//10}"
                counts=game.setdefault('prepare_counters',{});counts[key]=counts.get(key,0)+1
                if counts[key]%cfg['gap']:continue
            for _ in range(1+cfg['trigger_time']+extra):prepare_effect(game,owner,cfg,event)


def clear_prepare_temporary(game: dict) -> None:
    """战报确认后清除当轮临时属性，永久成长保留；逆转与重复读取不提前清除。"""
    temporary=game.pop('prepare_temporary',{})
    for row in game['chess_list']:
        amount=temporary.get(str(row['unique_id']),[0,0])
        for key in (1,2):chess_set_stat(row,key,chess_stat(row,key)-amount[key-1])
