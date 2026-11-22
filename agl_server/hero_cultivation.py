"""角色神识超越：按本机阶段表扣除角色碎片和金币。"""

from functools import lru_cache

from .battle_rewards import grant_rewards, inventory_pushes, spend_items
from .gameplay_protocol import read_client_data
from .hero_preferences import hero_info_data


@lru_cache(maxsize=1)
def fragment_exchange_rules() -> tuple[dict, dict]:
    """读取各阶位剩余升阶成本，以及客户端按碎片稀有度配置的徽章和倍率。"""
    data = read_client_data('''(function()
        local stars, costs = require('HeroStarCfg'), {}
        local reserve = 0
        for i = #stars.all, 1, -1 do
            local id = stars.all[i]
            if i < #stars.all then reserve = reserve + stars[id].star_up end
            costs[tostring(id)] = reserve
        end
        local rates = require('GameSetting').fragment_break_num.value
        local badges = {}
        for _, name in ipairs({'ItemCfg', 'ItemCfg2'}) do
            local items = require(name)
            for _, id in ipairs(items.all) do
                local item = items[id]
                if item and item.type == 3 then
                    badges[tostring(item.icon)] = rates[item.rare - 2]
                end
            end
        end
        return {costs=costs, badges=badges}
    end)()''')
    return data['costs'], data['badges']


def exchange_surplus_fragments(user: dict) -> dict:
    """按账号实际库存兑换所有已解锁角色的多余情报，保留升至满阶所需碎片。"""
    reserves, rates = fragment_exchange_rules()
    pieces = user.get('hero_pieces', {})
    remaining, rewards = {}, {}
    for hero in user.get('heroes', []):
        key = str(hero['id'])
        count = pieces.get(key, 0)
        if hero.get('unlock', 1) != 1 or count <= 0:
            continue
        reserve = reserves.get(str(hero['star']))
        if reserve is None:
            raise ValueError('角色阶位无效')
        if count <= reserve:
            continue
        if key not in rates:
            raise ValueError('角色情报兑换配置缺失')
        badge, rate = rates[key]
        remaining[key] = reserve
        rewards[badge] = rewards.get(badge, 0) + (count - reserve) * rate
    if not rewards:
        raise ValueError('没有可兑换的多余情报')
    grant_rewards(user, [{'id': badge, 'num': amount} for badge, amount in rewards.items()])
    pieces.update(remaining)
    return {'result': 0}


def surplus_fragment_pushes(user: dict) -> list[tuple]:
    """先同步碎片余额（含零值），再同步货币和材料。"""
    pieces = [{'id': int(hero_id), 'num': count} for hero_id, count in user['hero_pieces'].items()]
    return [(14019, {'piece_list': pieces}), *inventory_pushes(user)[:2]]


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
