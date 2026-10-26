"""皮肤资源可用性及大厅场景试用的配置期限、拥有判断和持久化。"""

from functools import lru_cache
import json
import time

from config import STREAMING_ASSETS_DIR
from gameplay_protocol import read_client_data
from server_clock import business_day


@lru_cache(maxsize=1)
def usable_skin_catalog() -> dict[int, int]:
    """仅开放当前清单包含大厅、展示及界面模型的皮肤；未下载的已发布包仍可用。"""
    manifest = json.loads((STREAMING_ASSETS_DIR / 'AssetHash_Info.bytes').read_bytes())
    bundles = {row.split('|')[0].lower() for row in manifest['assetHashList']}
    skins = read_client_data('''(function()
        local c=require('SkinCfg'); local result={}
        for _,id in ipairs(c.all) do
            local s=c[id]
            result[#result+1]={id=id,hero=s.hero,
                models={s.main_model_id,s.display_model_id,s.ui_modelId,s.modelId}}
        end
        return result
    end)()''')
    return {skin['id']: skin['hero'] for skin in skins
            if all(model and 'comchar/char/' + model.lower() + '.ys' in bundles
                   for model in skin['models'])}


def normalize_skin_selections(user: dict) -> None:
    """移除旧存档中缺少界面资源的皮肤，仅将无效选择回退到所属角色原皮。"""
    catalog = usable_skin_catalog()
    for hero in user.get('heroes', []):
        hero['skins'] = [skin for skin in hero['skins'] if catalog.get(skin) == hero['id']]
        for field in ('skin_id', 'battle_skin_id'):
            if hero.get(field) and catalog.get(hero[field]) != hero['id']:
                hero[field] = hero['id']
    if 'owned_skins' in user:
        user['owned_skins'] = [skin for skin in user['owned_skins'] if skin in catalog]
    if 'skin_trials' in user:
        user['skin_trials'] = {skin: expiry for skin, expiry in user['skin_trials'].items()
                               if int(skin) in catalog}
    for info in user.get('profile', {}).get('random_info', []):
        if info['random_type'] == 1 and 'random_list' in info:
            info['random_list'] = [skin for skin in info['random_list'] if skin in catalog]


def item_trial_expiry(cfg: dict, timestamp: int) -> int:
    """按客户端相对天数与北京时间刷新时刻计算期限，不采用客户端提供的期限。"""
    rule = cfg.get('time')
    if not rule or rule[0] != 4:
        raise ValueError('试用物品期限配置无效')
    days, clock = rule[1]
    return ((business_day(timestamp) + 1 + days) * 86400 - 8 * 3600
            + clock[0] * 3600 + clock[1] * 60 + clock[2])


def active_trials(user: dict, field: str) -> dict:
    """仅返回尚未到期的试用记录；查询不延长期限也不修改存档。"""
    now = int(time.time())
    return {int(item): expiry for item, expiry in user.get(field, {}).items() if expiry > now}


def skin_available(user: dict, hero: dict, skin: int) -> bool:
    """校验皮肤界面资源、角色归属及永久或有效试用的拥有状态。"""
    if usable_skin_catalog().get(skin) != hero['id']:
        return False
    if skin in hero['skins']:
        return True
    return (skin in active_trials(user, 'skin_trials')
            and read_client_data(f"require('SkinCfg')[{int(skin)}].hero") == hero['id'])


def grant_trial_item(user: dict, cfg: dict, timestamp: int) -> int:
    """将试用物品映射到实际皮肤或场景，保留较晚期限，不混入永久拥有列表。"""
    target = cfg['param'][0]
    expiry = item_trial_expiry(cfg, timestamp)
    field = 'skin_trials' if cfg['type'] == 8 else 'scene_trials'
    trials = user.setdefault(field, {})
    trials[str(target)] = max(trials.get(str(target), 0), expiry)
    return trials[str(target)]
