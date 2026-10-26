"""决斗王指定棋子种类的全局永久属性，确认战报后统一提交。"""

from resident_autochess_round import chess_stat, chess_set_stat


def add_catalog_body(holder: dict, amount: list) -> None:
    """给棋子或商品的真实属性增加一次已确认增量，不改经验与技能。"""
    for key in (1,2):chess_set_stat(holder,key,chess_stat(holder,key)+amount[key-1])


def apply_catalog_body(game: dict, holder: dict, chess_id: int) -> None:
    """只在新生成实体／商品时应用历史种类加成，加载已有存档不重复增加。"""
    add_catalog_body(holder,game.get('catalog_bonuses',{}).get(str(chess_id),[0,0]))


def confirm_catalog_growth(game: dict, changes: dict) -> None:
    """同次确认更新编队、普通商店和全部奖励队列，展示副本按商品编号去重。"""
    if not changes:return
    ledger=game.setdefault('catalog_bonuses',{})
    for key,amount in changes.items():
        saved=ledger.setdefault(str(key),[0,0])
        saved[0]+=amount[0];saved[1]+=amount[1]
        for row in game['chess_list']:
            if str(row['chess_id'])==str(key):add_catalog_body(row,amount)
        seen=[]
        for row in game.get('reward_queue',[])+game['shop_items']:
            if row['shop_type']==1 or str(row['id'])!=str(key):continue
            if row['shop_unique_id'] in seen:continue
            seen.append(row['shop_unique_id']);add_catalog_body({'chess_attr_list':row['chess_attr_list']},amount)
        # 旧JSON恢复后的奖励队列与展示项不是同一对象，统一使用队列权威描述。
        for row in game['shop_items']:
            if row['shop_type']==2:
                saved=next((r for r in game.get('reward_queue',[]) if r['shop_unique_id']==row['shop_unique_id']),None)
                if saved is not None:row['chess_attr_list']=[dict(v) for v in saved['chess_attr_list']]
