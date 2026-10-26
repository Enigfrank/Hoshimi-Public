"""角色神识超越：按本机阶段表扣除角色碎片和金币。"""

from battle_rewards import inventory_pushes, spend_items
from gameplay_protocol import read_client_data
from hero_preferences import hero_info_data


def promote_hero(user: dict, hero_id: int) -> dict:
    """从当前阶段前进一次，先校验全部成本；碎片取独立角色碎片库存。"""
    hero = next((h for h in user.get('heroes', []) if h['id'] == hero_id and h.get('unlock', 1)), None)
    if hero is None:
        raise ValueError('角色未拥有')
    stars = read_client_data("(function()local c=require('HeroStarCfg');local r=setmetatable({},{json_array=true});for _,id in ipairs(c.all)do r[#r+1]=c[id]end return r end)()")
    index = next((i for i, row in enumerate(stars) if row['id'] == hero['star']), None)
    if index is None:
        raise ValueError('角色阶位无效')
    if index == len(stars)-1:
        raise ValueError('角色已达到最高阶位')
    cost = stars[index]
    pieces = user.get('hero_pieces', {}).get(str(hero_id), 0)
    if pieces < cost['star_up']:
        raise ValueError('角色碎片不足')
    spend_items(user, [{'id': 2, 'num': cost['gold_cost']}])
    user.setdefault('hero_pieces', {})[str(hero_id)] = pieces-cost['star_up']
    hero['star'] = stars[index+1]['id']
    return {'result': 0}


def promote_pushes(user: dict, hero_id: int) -> list:
    """成功回调之后同步碎片和权威角色，避免客户端回调再次推进已更新阶段。"""
    hero = next(h for h in hero_info_data(user)['hero_info_list'] if h['hero_base_info']['id'] == hero_id)
    return [(14019, {'piece_list': [{'id': hero_id, 'num': user['hero_pieces'][str(hero_id)]}]}),
            (14007, {'hero': hero}), inventory_pushes(user)[0]]
