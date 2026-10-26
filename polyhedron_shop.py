"""多维本地路线的商店库存、刷新、扣费与恢复。"""

import random


def shop_attributes(data: dict) -> dict:
    """读取本地基础次数并叠加客户端配置的商店天赋效果。"""
    from polyhedron_game import config_row
    progress = data['game']['progress']
    values = {15: config_row('settings', 8)['value'][0],
              16: len(config_row('settings', 14)['value']), 17: 0, 18: 0, 29: 0}
    for row in progress['effect_list']:
        effect = config_row('effects', row['id'])
        if effect['action'] == 1:
            for key, amount in effect['params']:
                if key in values:
                    values[key] += amount
    # 只写入本次商店需要的属性；其他已保存的局内属性保持原值。
    existing = {row['id']: row['value'] for row in progress['attribute_list']}
    existing.update(values)
    progress['attribute_list'] = [{'id': key, 'value': value} for key, value in sorted(existing.items())]
    return values


def shop_stock(data: dict) -> list:
    """从尚可升级的真实珍宝及恢复效果抽取库存，结果随路线保存。"""
    from polyhedron_game import polyhedron_catalog, config_row
    progress = data['game']['progress']
    owned = {row['id']: row['level'] for row in progress['artifact_list']}
    heroes = {row['hero_id'] for row in progress['hero_list']}
    items = [{'class': 6, 'params': [row['id']]} for row in polyhedron_catalog()['artifacts']
        if owned.get(row['id'], 0) < row['max_level'] and
        (not row['exclusive_hero_id'] or row['exclusive_hero_id'] in heroes)]
    recover = config_row('settings', 15)['value'][0]
    items.append({'class': 7, 'params': [recover]})
    attributes = shop_attributes(data)
    prices = dict(config_row('settings', 13)['value'])
    stock = []
    for index, item in enumerate(random.sample(items, min(len(items), max(0, attributes[15])))):
        price = prices.get(item['class'], prices[4])
        if index == 0:
            price = max(0, price*(1000+attributes[29])//1000)
        stock.append({'item': item, 'price': price, 'is_available': 1})
    return stock


def open_shop(data: dict) -> None:
    """首次进入商店生成库存；已有库存不因查询或重启重新抽取。"""
    stage = data['game']['progress']['stage']
    if 'shop' not in stage:
        stage['shop'] = {'item_list': shop_stock(data), 'refresh_times': 0, 'recover_times': 0}


def shop_request(data: dict, command: int, request: dict) -> None:
    """执行一次购买、按递增成本刷新或天赋提供的免费回血。"""
    from polyhedron_game import coin_change, apply_item, config_row
    progress = data['game']['progress']
    stage = progress['stage']
    if progress['event']['reward_type'] != 5 or stage['save_point'] != 3 or 'shop' not in stage:
        raise ValueError('当前多维节点不是已开放的商店')
    shop = stage['shop']
    attributes = shop_attributes(data)
    if command == 18022:
        index = request['index']
        if not 1 <= index <= len(shop['item_list']):
            raise ValueError('多维商店商品编号无效')
        item = shop['item_list'][index-1]
        if item['is_available'] != 1:
            raise ValueError('多维商店商品已购买')
        coin_change(progress, -item['price'])
        apply_item(data, item['item'])
        item['is_available'] = 0
    elif command == 18024:
        count = shop['refresh_times']
        if count >= attributes[16]:
            raise ValueError('多维商店刷新次数已用尽')
        if count >= attributes[18]:
            costs = config_row('settings', 14)['value']
            number = count-attributes[18]+1
            cost = next((amount for step, amount in costs if step == number), costs[-1][1])
            coin_change(progress, -cost)
        shop['item_list'] = shop_stock(data)
        shop['refresh_times'] += 1
    elif command == 18026:
        if shop['recover_times'] >= attributes[17]:
            raise ValueError('多维商店恢复次数已用尽')
        if not any(0 < hero['health'] < hero['max_health'] for hero in progress['hero_list']):
            raise ValueError('多维队伍没有需要恢复的存活角色')
        apply_item(data, {'class': 7, 'params': [config_row('settings', 15)['value'][0]]})
        shop['recover_times'] += 1
    else:
        raise ValueError('多维商店操作无效')
