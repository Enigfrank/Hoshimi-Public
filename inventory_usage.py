"""仓库消耗品和可选礼包的扣除、奖励与持久化。"""

from functools import lru_cache
import random
import time

from battle_rewards import drop_config, roll_drops, grant_rewards, spend_items
from gameplay_protocol import read_client_data
from skin_scene_data import item_trial_expiry, grant_trial_item


@lru_cache(maxsize=256)
def usable_item(item: int) -> dict:
    """取得实际使用规则，不接受客户端提供奖励或到期时间。"""
    return read_client_data(f"require('ItemCfg')[{int(item)}] or require('ItemCfg2')[{int(item)}] or {{}}")


@lru_cache(maxsize=1)
def _gift_box_pool() -> tuple[int, ...]:
    """校验通用礼物，并从两张物品表读取去重排序的专属礼物 ID。"""
    general = usable_item(30014)
    if (general.get('type'), general.get('sub_type'), general.get('rare')) != (10, 2, 4):
        raise ValueError('礼物盒子奖励配置无效')
    pool = read_client_data('''(function()
        local primary, secondary = require('ItemCfg'), require('ItemCfg2')
        local ids, seen = {}, {}
        for _, cfg in ipairs({primary, secondary}) do
            for _, id in ipairs(cfg.all or {}) do
                local row = primary[id] or secondary[id]
                if not seen[id] and row and row.type == 10 and row.sub_type == 1 and row.rare == 4 then
                    seen[id] = true
                    ids[#ids+1] = id
                end
            end
        end
        table.sort(ids)
        return ids
    end)()''')
    if not pool:
        raise ValueError('礼物盒子奖励配置无效')
    return tuple(pool)


def use_items(user: dict, request: dict) -> dict:
    """整批校验后消耗库存，按本机配置发放体力、礼包和自选奖励。"""
    costs, rewards, trials = [], [], []
    has_gift_box = False
    now = int(time.time())
    if not request['use_item_list']:
        raise ValueError('使用物品列表为空')
    for entry in request['use_item_list']:
        item, num = entry['item_info']['id'], entry['item_info']['num']
        cfg = usable_item(item)
        if not cfg or not cfg.get('use') or not 1 <= num <= 9999:
            raise ValueError('物品不可使用或数量无效')
        # 试衣底片页面会为旧永久材料记录按配置推导期限；只接受服务端计算的值。
        deadline = entry['item_info'].get('time_valid', 0)
        if deadline and (cfg['sub_type'] != 407 or deadline != item_trial_expiry(cfg, now)):
            raise ValueError('未持有该临时物品实例')
        subtype, params = cfg['sub_type'], cfg.get('param') or []
        selected = entry['use_list']
        if subtype == 407:
            if selected or num != 1 or not params:
                raise ValueError('试衣底片使用参数无效')
            for target in params:
                trial = usable_item(target)
                if trial.get('type') not in (8, 21) or not trial.get('param'):
                    raise ValueError('试衣底片奖励配置无效')
                item_trial_expiry(trial, now)
                trials.append(trial)
        elif subtype in (401, 403, 501):
            if selected or not all(isinstance(r, list) and len(r) == 2 for r in params):
                raise ValueError('物品使用参数无效')
            rewards.extend({'id': i, 'num': n*num} for i, n in params)
        elif subtype == 502 and item == 30011:
            if selected:
                raise ValueError('礼物盒子使用参数无效')
            pool = _gift_box_pool()
            has_gift_box = True
            rewards.extend({'id': 30014 if random.randrange(20) == 0 else random.choice(pool), 'num': 1}
                           for _ in range(num))
        elif subtype in (504, 506, 515):
            if len(selected) != 1 or not 1 <= selected[0] <= len(params):
                raise ValueError('自选礼包选项无效')
            item_id, amount = params[selected[0]-1]
            rewards.append({'id': item_id, 'num': amount*num})
        elif subtype == 507:
            if selected or not params or not drop_config(params[0]):
                raise ValueError('随机礼包掉落配置无效')
            rewards.extend(roll_drops(drop_config(params[0]), num))
        elif subtype == 408:
            if selected or not params:
                raise ValueError('剧情道具参数无效')
            stories = user.setdefault('read_stories', [])
            if params[0] not in stories:
                stories.append(params[0])
        else:
            raise ValueError('该物品需要尚未实现的专用使用流程')
        costs.append({'id': item, 'num': num})
    totals = {}
    for reward in rewards:
        totals[reward['id']] = totals.get(reward['id'], 0) + reward['num']
    if totals.get(4, 0)+user.get('stamina', 240) > 999:
        raise ValueError('体力超过可持有上限')
    spend_items(user, costs)
    reward_items = sorted(totals.items(), key=lambda pair: str(pair[0])) if has_gift_box else totals.items()
    rewards = [{'id': i, 'num': n} for i, n in reward_items]
    grant_rewards(user, rewards)
    for trial in trials:
        rewards.append({'id': trial['id'], 'num': 1, 'time_valid': grant_trial_item(user, trial, now)})
    return {'result': 0, 'drop_list': rewards}
