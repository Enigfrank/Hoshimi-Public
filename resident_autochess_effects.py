"""决斗王战斗快照的护盾、离场召唤、加属性及伤害效果。"""

from copy import deepcopy

from resident_catalog import resident_config
from resident_autochess_buffs import buff_record
from resident_autochess_round import chess_stat, chess_set_stat


def combat_action(ctx: dict, kind: int, key: str, data: dict) -> None:
    """按执行顺序保存真实动作，动作编号连续且只属于当前战报。"""
    ctx['actions'].append({'action_id': len(ctx['actions'])+1, 'action_type': kind, key: data})
    sample_bodies(ctx)


def sample_bodies(ctx: dict) -> None:
    """只在需要徽章统计时读取真实当前棋子属性峰值，不推定满血或最大养成。"""
    if not ctx.get('track_metrics'):return
    for side in (0,1):
        value=max([chess_stat(r,1)+chess_stat(r,2) for r in ctx['teams'][side] if chess_stat(r,2)>0] or [0])
        ctx['metrics'][side]['max_body']=max(ctx['metrics'][side]['max_body'],value)


def effect_targets(ctx: dict, side: int, owner: dict, target: int, event: dict | None = None) -> list:
    """按配置描述解析双方、前后邻、护盾筛选和稳定抽选目标。"""
    own = [r for r in ctx['teams'][side] if chess_stat(r, 2) > 0]
    enemy = [r for r in ctx['teams'][1-side] if chess_stat(r, 2) > 0]
    if target == 13:
        return [event] if event is not None and event in own else []
    if target == 5:
        return [r for r in own+enemy if r['unique_id'] != owner['unique_id']]
    if target == 111:
        return [owner]
    if target == 112:
        return own
    if target == 113:
        return [r for r in own if r['unique_id'] != owner['unique_id']]
    if target in (114, 117, 118, 153, 154, 155, 156):
        pool = own
        if target in (117, 118):
            pool = [r for r in pool if r['unique_id'] != owner['unique_id']]
        if target in (153, 154, 155, 156):
            pool = [r for r in pool if any(resident_config('AutoChessBuffCfg', b['buff_id'])['action_type'] == 7
                for b in r['buff_list']) == (target != 156)]
        count = 2 if target == 118 else target-152 if target in (153,154,155) else 1
        start = (len(ctx['actions'])+owner['unique_id']) % len(pool) if pool else 0
        return (pool[start:]+pool[:start])[:count]
    if target == 120:
        return [r for r in own if r['index']-owner['index'] in (-1,1)]
    if target == 124:
        return own[:1]
    if target in (127, 128, 129):
        return [r for r in own if r['index'] > owner['index'] or owner not in ctx['teams'][side] and r['index'] == owner['index']][:target-126]
    if target == 121:
        rows = [r for r in own if r['index'] < owner['index']]
        return rows[-1:] if rows else []
    if target == 212:
        return enemy
    if target in (214, 215, 216):
        if not enemy:
            return []
        start = (len(ctx['actions'])+owner['unique_id']) % len(enemy)
        return (enemy[start:]+enemy[:start])[:target-213]
    return []


def event_matches(owner: dict, event: dict | None, enum: int) -> bool:
    """事件编号只按本地可确定的自身、同队、其他同队和前邻关系匹配。"""
    if event is None:
        return True
    if enum == 111:
        return owner['unique_id'] == event['unique_id']
    if enum == 113:
        return owner['unique_id'] != event['unique_id']
    if enum in (121,122,123):
        return 1 <= owner['index']-event['index'] <= enum-120
    if enum == 133:
        return resident_config('AutoChessCfg',event['chess_id'])['type'] in (1,3)
    return enum in (0, 1, 112)


def damage_amount(ctx: dict, attacker: dict, target: dict, damage: int) -> int:
    """正伤害先判护盾，瓦解仅在首次实际伤害时消耗并消灭目标。"""
    if damage <= 0 or consume_shield(ctx,target,damage):
        return 0
    venom = next((b for b in attacker['buff_list'] if resident_config('AutoChessBuffCfg',b['buff_id'])['action_type'] == 33),None)
    if venom is not None:
        attacker['buff_list'].remove(venom)
        combat_action(ctx,7,'remove_effect_info',{'effect_enum':33,'buff_uid':venom['unique_id'],'target_list':[],'attr_list':[]})
        return max(damage,chess_stat(target,2))
    return damage


def damage_events(ctx: dict, side: int, attacker: dict, target: dict) -> None:
    """实际对敌伤害记录击杀者，并通知受伤和同队对敌伤害技能。"""
    target_side = 0 if target in ctx['teams'][0] else 1
    if target_side != side:
        if chess_stat(target,2) <= 0:
            ctx.setdefault('killers',{})[str(target['unique_id'])] = [side,attacker]
        dispatch_effects(ctx,side,23,attacker)
    dispatch_effects(ctx,target_side,16,target)


def combat_effect(ctx: dict, side: int, owner: dict, buff: dict, cfg: dict, event: dict | None = None) -> bool:
    """按配置处理召唤、加技能、属性与伤害；复杂特殊类型仍需逐项补齐。"""
    kind, params = cfg['action_type'], cfg['param']
    if kind==25:
        key=str(params[0]);amount=params[2]
        saved=ctx['catalog_changes'][side].setdefault(key,[0,0])
        saved[0]+=amount[0];saved[1]+=amount[1]
        changes=[]
        for row in ctx['teams'][side]:
            if row['chess_id']!=params[0] or chess_stat(row,2)<=0:continue
            for attr in (1,2):chess_set_stat(row,attr,chess_stat(row,attr)+amount[attr-1])
            changes.append({'id':row['unique_id'],'temp_id':row['chess_id'],
                'update_list':[{'key':attr,'value':amount[attr-1]} for attr in (1,2)]})
        if changes:combat_action(ctx,3,'action_effect_info',{'effect_enum':1,'buff_uid':buff['unique_id'],'target_list':changes})
        return True
    if kind == 39:
        killer = ctx.get('death_killers',{}).get(str(owner['unique_id']))
        if killer is None:
            return False
        row = killer[1]
        if row in ctx['teams'][1-side] and chess_stat(row,2) > 0:
            damage = chess_stat(row,2)
            chess_set_stat(row,2,0)
            combat_action(ctx,3,'action_effect_info',{'effect_enum':2,'buff_uid':buff['unique_id'],
                'target_list':[{'id':row['unique_id'],'temp_id':row['chess_id'],'update_list':[{'key':2,'value':-damage}]}]})
            ctx.setdefault('killers',{})[str(row['unique_id'])] = [side,owner]
        if not any(resident_config('AutoChessBuffCfg',b['buff_id'])['action_type'] == 40 for b in owner['buff_list']):
            combat_effect(ctx,side,owner,buff,resident_config('AutoChessBuffCfg',710),event)
        return True
    if kind == 28:
        killer = ctx.get('death_killers',{}).get(str(event['unique_id'])) if event is not None else None
        if killer is None or killer[1]['unique_id'] != owner['unique_id'] or resident_config('AutoChessCfg',event['chess_id'])['type'] != 0:
            return False
        rows = ctx['removed'][1-side]
        if event['unique_id'] not in rows:rows.append(event['unique_id'])
        return True
    if kind == 26:
        if event is None or event['unique_id'] == owner['unique_id']:
            return False
        source = ctx['initial'].get(str(event['unique_id']),event)
        amount = [chess_stat(source,key)*params[1] for key in (1,2)]
        for key in (1,2):chess_set_stat(owner,key,chess_stat(owner,key)+amount[key-1])
        saved = ctx['permanent'].setdefault(str(owner['unique_id']),[0,0])
        saved[0] += amount[0];saved[1] += amount[1]
        combat_action(ctx,3,'action_effect_info',{'effect_enum':1,'buff_uid':buff['unique_id'],
            'target_list':[{'id':owner['unique_id'],'temp_id':owner['chess_id'],
                'update_list':[{'key':key,'value':amount[key-1]} for key in (1,2)]}]})
        return True
    if kind == 8:
        targets = effect_targets(ctx, side, owner, params[0], event)
        for row in targets:
            dispatch_effects(ctx, side, params[1], row, params[3] if len(params) > 3 else [])
        return bool(targets)
    if kind in (3, 22, 23, 29,31,35,37,40):
        from resident_autochess import chess_row
        rows = []
        if kind == 29:
            removed = list(ctx['teams'][side])
            changes = [{'id':r['unique_id'],'temp_id':r['chess_id'],
                'update_list':[{'key':2,'value':-chess_stat(r,2)}]} for r in removed]
            for row in removed:chess_set_stat(row,2,0)
            combat_action(ctx,3,'action_effect_info',{'effect_enum':2,'buff_uid':buff['unique_id'],'target_list':changes})
            combat_action(ctx,5,'action_dead_info',{'uid_list':[r['unique_id'] for r in removed]})
            ctx['teams'][side] = []
            row = chess_row(ctx['next_chess_uid'],params[0],1)
            ctx['next_chess_uid'] += 1
            chess_set_stat(row,3,params[1])
            from resident_autochess_buffs import sync_chess_buffs
            sync_chess_buffs(row)
            rows.append(row)
        elif kind in (22,31,35):
            if kind == 31:
                victim = ctx.get('victim')
                if victim is None:return False
                chosen = ctx['initial'].get(str(victim['unique_id']),victim)
            else:
                chosen = ctx['initial'].get(str(owner['unique_id']), owner) if kind == 22 else max(ctx['teams'][side], key=lambda r: chess_stat(r, 2))
            row = deepcopy(chosen)
            row['unique_id'] = ctx['next_chess_uid']
            ctx['next_chess_uid'] += 1
            row['buff_list'] = [b for b in row['buff_list'] if b['buff_id'] not in (403,407)]
            for buff_row in row['buff_list']:
                buff_row['unique_id'] = ctx['next_buff_uid'];ctx['next_buff_uid'] += 1
                buff_row['source_uid'] = row['unique_id']
            if len(ctx['teams'][side]) < ctx['capacity']:rows.append(row)
        elif kind == 40:
            killer = ctx.get('death_killers',{}).get(str(owner['unique_id']))
            if killer is None or len(ctx['teams'][side]) >= ctx['capacity']:return False
            source = ctx['initial'].get(str(killer[1]['unique_id']),killer[1])
            row = chess_row(ctx['next_chess_uid'],params[1],owner['index'])
            ctx['next_chess_uid'] += 1
            for key in (1,2):chess_set_stat(row,key,chess_stat(source,key)*params[2])
            rows.append(row)
        elif kind == 37:
            calls = ctx.setdefault('special_summons',[0,0])
            if calls[side] >= 8 or len(ctx['teams'][side]) >= ctx['capacity']:
                return False
            for source in params[1]:
                if calls[side] >= 8 or len(ctx['teams'][side])+len(rows) >= ctx['capacity']:
                    break
                row = chess_row(ctx['next_chess_uid'],source[0],1)
                from resident_autochess_buffs import sync_chess_buffs
                chess_set_stat(row,3,source[1]);sync_chess_buffs(row)
                if side == 0:
                    for key in (1,2):chess_set_stat(row,key,chess_stat(row,key)+ctx['team_bonus'][key-1])
                ctx['next_chess_uid'] += 1
                calls[side] += 1
                rows.append(row)
        elif kind == 23:
            if len(ctx['teams'][side]) < ctx['capacity']:
                row = chess_row(ctx['next_chess_uid'],params[0],owner['index'])
                ctx['next_chess_uid'] += 1
                deaths = ctx.get('death_counts',[0,0])[side]
                for key in (1,2):
                    bonus = ctx['team_bonus'][key-1] if side == 0 else 0
                    chess_set_stat(row,key,chess_stat(row,key)+deaths*params[2][key-1]+bonus)
                rows.append(row)
        else:
            _, pool = params
            for i,chess_id in enumerate(pool):
                if len(ctx['teams'][side])+len(rows) >= ctx['capacity']:
                    break
                row = chess_row(ctx['next_chess_uid'], chess_id, owner['index']+i)
                if side == 0:
                    bonus = ctx['team_bonus']
                    chess_set_stat(row, 1, chess_stat(row, 1)+bonus[0]);chess_set_stat(row, 2, chess_stat(row, 2)+bonus[1])
                ctx['next_chess_uid'] += 1
                rows.append(row)
        if not rows:
            return False
        if kind!=35:
            for row in rows:
                key=str(row['chess_id'])
                base=[0,0] if kind==22 else ctx['catalog_base'][side].get(key,[0,0])
                current=ctx['catalog_changes'][side].get(key,[0,0])
                for attr in (1,2):chess_set_stat(row,attr,chess_stat(row,attr)+base[attr-1]+current[attr-1])
        for row in rows:
            ctx['next_buff_uid'] = max(ctx['next_buff_uid'],max([b['unique_id'] for b in row['buff_list']] or [0])+1)
        front=kind in (29,37) or kind==3 and params[0]==2
        offset = 0 if front else sum(row['index'] < owner['index'] for row in ctx['teams'][side])
        for i, row in enumerate(ctx['teams'][side]):
            position = i+1+(len(rows) if i >= offset else 0)
            if row['index'] != position:
                row['index'] = position
                combat_action(ctx, 1, 'action_move_info', {'action_list': [{'key': row['unique_id'], 'value': row['index']}]})
        for i, row in enumerate(rows):
            row['index'] = offset+i+1
        combat_action(ctx, 3, 'action_effect_info', {'effect_enum': 3, 'buff_uid': buff['unique_id'],
            'call_info': {'user_id': side, 'chess_list': deepcopy(rows)}})
        ctx['teams'][side].extend(rows)
        ctx['teams'][side] = sorted(ctx['teams'][side], key=lambda r: r['index'])
        if ctx.get('track_metrics'):
            ctx['metrics'][side]['summons']+=len(rows)
            sample_bodies(ctx)
        for row in rows:
            ctx['initial'][str(row['unique_id'])] = deepcopy(row)
            dispatch_effects(ctx, side, 18, row)
            dispatch_effects(ctx, side, 19, row)
        return True
    if kind == 4:
        if params[0] == 2:
            # 金固全局附属链只转换成实际召唤动作，隐藏计数技能不伪装成棋子技能。
            call = next((resident_config('AutoChessBuffCfg',i) for i in params[1]
                if resident_config('AutoChessBuffCfg',i)['action_type'] in (25,37)),None)
            return combat_effect(ctx,side,owner,buff,call,event) if call is not None else False
        targets = effect_targets(ctx, side, owner, params[0], event)
        additions = []
        for row in targets:
            for buff_id in params[1]:
                group = resident_config('AutoChessBuffCfg', buff_id)['repeated'] or [buff_id]
                if any(r['buff_id'] in group for r in row['buff_list']):
                    continue
                added = buff_record(ctx['next_buff_uid'], buff_id, row)
                ctx['next_buff_uid'] += 1
                row['buff_list'].append(added)
                additions.append({'target_type': 0, 'target_uid': row['unique_id'], 'buff_info': deepcopy(added)})
        if additions:
            combat_action(ctx, 3, 'action_effect_info', {'effect_enum': 4, 'buff_uid': buff['unique_id'],
                'add_buff_info': additions})
            return True
    if kind in (5,36):
        targets = effect_targets(ctx, side, owner, params[0], event)
        changes = []
        for row in targets:
            amount = list(params[2])
            if kind == 36:
                amount = [amount[i]+next((r['value'] for r in buff['other_param'] if r['key'] == i+1),0) for i in range(2)]
            if params[1] == 7:
                amount = [amount[i]-chess_stat(row, i+1) if amount[i] else 0 for i in range(2)]
            for key in (1, 2):
                chess_set_stat(row, key, chess_stat(row, key)+amount[key-1])
            if params[1] == 2:
                saved = ctx['permanent'].setdefault(str(row['unique_id']), [0, 0])
                saved[0] += amount[0]; saved[1] += amount[1]
            changes.append({'id': row['unique_id'], 'temp_id': row['chess_id'],
                'update_list': [{'key': 1, 'value': amount[0]}, {'key': 2, 'value': amount[1]}, {'key': 3, 'value': 0}]})
        if changes:
            combat_action(ctx, 3, 'action_effect_info', {'effect_enum': 1, 'buff_uid': buff['unique_id'],
                'target_list': changes})
            return True
    if kind == 34:
        victim = ctx.get('victim')
        if victim is None:
            return False
        source = ctx['initial'].get(str(victim['unique_id']),victim)
        targets = [r for r in effect_targets(ctx,side,owner,params[1],event)
            if not any(b['buff_id'] in resident_config('AutoChessBuffCfg',params[2])['repeated'] for b in r['buff_list'])]
        changes = []
        for row in targets:
            amount = [chess_stat(source,key)*params[3][key-1] for key in (1,2)]
            for key in (1,2):chess_set_stat(row,key,chess_stat(row,key)+amount[key-1])
            changes.append({'id':row['unique_id'],'temp_id':row['chess_id'],
                'update_list':[{'key':key,'value':amount[key-1]} for key in (1,2)]})
        if changes:
            combat_action(ctx,3,'action_effect_info',{'effect_enum':1,'buff_uid':buff['unique_id'],'target_list':changes})
            return True
    if kind in (2,18,38):
        targets = effect_targets(ctx, side, owner, params[0], event)
        changes = []
        hit = []
        for row in targets:
            power = ctx.get('attack_damage',{}).get(str(owner['unique_id']),0) if kind == 18 else params[1]
            damage = min(chess_stat(row,2),damage_amount(ctx,owner,row,power))
            if damage <= 0:
                continue
            chess_set_stat(row, 2, chess_stat(row, 2)-damage)
            hit.append(row)
            changes.append({'id': row['unique_id'], 'temp_id': row['chess_id'], 'update_list': [{'key': 2, 'value': -damage}]})
        if changes:
            combat_action(ctx, 3, 'action_effect_info', {'effect_enum': 2, 'buff_uid': buff['unique_id'], 'target_list': changes})
            for row in hit:
                damage_events(ctx,side,owner,row)
            return True
    return False


def dispatch_effects(ctx: dict, side: int, timing: int, event: dict | None = None, excluded: list | None = None, event_side: int | None = None) -> None:
    """按实例限制生效次数、连续触发与事件阵营，嵌套效果不能重入同一技能。"""
    used = ctx.setdefault('effect_uses', {})
    active = ctx.setdefault('active_effects', [])
    seen = ctx.setdefault('effect_events',{})
    owners = list(ctx['teams'][side])
    if event is not None and timing == 17 and event not in owners and event_side in (None,side):
        owners.append(event)
    for owner in owners:
        if chess_stat(owner, 2) <= 0 and (timing not in (16, 17) or owner is not event):
            continue
        for buff in list(owner['buff_list']):
            cfg = resident_config('AutoChessBuffCfg', buff['buff_id'])
            if buff['buff_id'] == 530:
                cfg = resident_config('AutoChessBuffCfg',713)
            elif buff['buff_id'] == 541:
                cfg = deepcopy(resident_config('AutoChessBuffCfg',542))
                cfg['enum'] = 113
            if buff['buff_id'] in (excluded or []) or cfg['moment'] != timing or cfg['action_type'] == 14:
                continue
            if event_side is not None and event_side != side:
                if cfg['enum'] != 212:
                    continue
            elif cfg['enum'] == 212 or not event_matches(owner, event, cfg['enum']):
                continue
            key = str(buff['unique_id'])
            if key in active or cfg['effective_time'] and used.get(key,0) >= cfg['effective_time']:
                continue
            seen[key] = seen.get(key,0)+1
            if cfg['gap'] and seen[key] % cfg['gap']:
                continue
            repeats = 1+cfg['trigger_time']
            if cfg['action_type'] == 38:
                acquired = next((r['value'] for r in buff['other_param'] if r['key'] == 3),0)
                repeats = cfg['trigger_time']+(ctx['round']-acquired+1 if acquired else 1)
            if timing == 17 and cfg['keyword_type'] == 2:
                repeats += sum(resident_config('AutoChessBuffCfg', b['buff_id'])['param'][1]
                    for row in ctx['teams'][side] if chess_stat(row,2) > 0 for b in row['buff_list']
                    if resident_config('AutoChessBuffCfg',b['buff_id'])['action_type'] == 14
                    and resident_config('AutoChessBuffCfg',b['buff_id'])['moment'] == 17 and b['buff_id'] not in (excluded or []))
            active.append(key)
            for _ in range(repeats):
                if cfg['effective_time'] and used.get(key,0) >= cfg['effective_time']:
                    break
                used[key] = used.get(key,0)+1
                if combat_effect(ctx, side, owner, buff, cfg, event):
                    if timing==16 and ctx.get('track_metrics'):ctx['metrics'][side]['hurt_effects']+=1
                    if timing == 17 and cfg['keyword_type'] == 2:
                        dispatch_effects(ctx, side, 21, owner)
                else:
                    used[key] -= 1
            active.remove(key)


def consume_shield(ctx: dict, chess: dict, damage: int) -> bool:
    """正伤害消耗一次配置护盾；被挡住的攻击不触发受伤。"""
    if damage <= 0:
        return False
    shield = next((b for b in chess['buff_list'] if resident_config('AutoChessBuffCfg', b['buff_id'])['action_type'] == 7), None)
    if shield is None:
        return False
    chess['buff_list'].remove(shield)
    combat_action(ctx, 7, 'remove_effect_info', {'effect_enum': 7, 'buff_uid': shield['unique_id'], 'target_list': [], 'attr_list': []})
    side = 0 if chess in ctx['teams'][0] else 1
    if ctx.get('track_metrics'):ctx['metrics'][side]['shields']+=1
    dispatch_effects(ctx, side, 20, chess)
    return True


def resolve_deaths(ctx: dict) -> None:
    """同时移除本次已死亡单位，再按稳定队伍顺序执行离场与召唤连锁。"""
    while True:
        dead = [(side, row) for side in (0, 1) for row in ctx['teams'][side] if chess_stat(row, 2) <= 0]
        if not dead:
            return
        for _,row in dead:
            killer = ctx.get('killers',{}).pop(str(row['unique_id']),None)
            if killer is not None:
                ctx.setdefault('death_killers',{})[str(row['unique_id'])] = killer
                ctx['victim'] = row
                dispatch_effects(ctx,killer[0],22,killer[1])
                ctx.pop('victim',None)
        combat_action(ctx, 5, 'action_dead_info', {'uid_list': [row['unique_id'] for _, row in dead]})
        deaths = ctx.setdefault('death_counts',[0,0])
        for side, row in dead:
            ctx['teams'][side].remove(row)
            deaths[side] += 1
        for side, row in dead:
            dispatch_effects(ctx, side, 17, row)
            dispatch_effects(ctx, 1-side, 17, row, event_side=side)


def run_combat(left: list, right: list, capacity: int, team_bonus: list | None = None, round_number: int = 1, facts: dict | None = None, catalog_bonuses: dict | None = None, track_metrics: bool = False) -> tuple:
    """在独立战斗快照内执行双方真实攻击、技能和死亡，返回战报与永久属性增量。"""
    teams = [deepcopy(sorted(left, key=lambda r: r['index'])), deepcopy(sorted(right, key=lambda r: r['index']))]
    ctx = {'teams': teams, 'actions': [], 'permanent': {}, 'capacity': capacity, 'team_bonus': team_bonus or [0,0], 'round':round_number,'removed':[[],[]],
        'catalog_base':[catalog_bonuses or {},{}],'catalog_changes':[{},{}],
        'track_metrics':track_metrics,'metrics':[{'max_body':0,'summons':0,'shields':0,'hurt_effects':0} for _ in (0,1)],
        'initial': {str(r['unique_id']): deepcopy(r) for rows in teams for r in rows},
        'next_chess_uid': max(r['unique_id'] for rows in teams for r in rows)+1,
        'next_buff_uid': max([b['unique_id'] for rows in teams for r in rows for b in r['buff_list']] or [1000000])+1}
    sample_bodies(ctx)
    for side in (0, 1):
        dispatch_effects(ctx, side, 11)
    resolve_deaths(ctx)
    while teams[0] and teams[1]:
        a, b = teams[0][0], teams[1][0]
        dispatch_effects(ctx, 0, 12, a); dispatch_effects(ctx, 1, 12, b)
        resolve_deaths(ctx)
        if a not in teams[0] or b not in teams[1]:
            continue
        damage_a, damage_b = max(0, chess_stat(b, 1)), max(0, chess_stat(a, 1))
        if damage_a == 0 and damage_b == 0:
            break
        damage_a = damage_amount(ctx,b,a,damage_a)
        damage_b = damage_amount(ctx,a,b,damage_b)
        ctx['attack_damage'] = {str(a['unique_id']):damage_b,str(b['unique_id']):damage_a}
        combat_action(ctx, 2, 'action_battle_info', {'action_list': [
            {'key': a['unique_id'], 'value': -damage_a}, {'key': b['unique_id'], 'value': -damage_b}]})
        chess_set_stat(a, 2, chess_stat(a, 2)-damage_a); chess_set_stat(b, 2, chess_stat(b, 2)-damage_b)
        if damage_a:
            damage_events(ctx,1,b,a)
        if damage_b:
            damage_events(ctx,0,a,b)
        dispatch_effects(ctx, 0, 13, a); dispatch_effects(ctx, 1, 13, b)
        resolve_deaths(ctx)
    if facts is not None:
        facts['removed']=deepcopy(ctx['removed'])
        if any(ctx['catalog_changes']):facts['catalog_changes']=deepcopy(ctx['catalog_changes'])
        if track_metrics:facts['metrics']=deepcopy(ctx['metrics'])
    return (1 if teams[0] and not teams[1] else 2 if teams[1] and not teams[0] else 0), ctx['actions'], ctx['permanent']
