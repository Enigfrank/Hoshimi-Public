"""游园街当前客户端配置及通用归属、解锁条件查询。"""

from functools import lru_cache

from gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def backhome_catalog() -> dict:
    """读取真实房间、家具、菜谱、委托、角色和偶像配置，保留配置原始编号。"""
    return read_client_data('''(function()
        local r={settings={}}
        for _,name in ipairs({'BackHomeCfg','BackHomeFurniture','BackHomeSuitCfg','BackHomeDormLevel',
            'BackHomeDormLevelRewardCfg','BackHomeHeroCfg','BackHomeHeroSkinCfg','BackHomeNpcCfg',
            'BackHomeCanteenFoodCfg','BackHomeCanteenTaskCfg','BackHomeCanteenFurnitureCfg',
            'BackHomeCanteenFurnitureIDCfg','BackHomeBreakGameCfg','BackHomeDormBirthdayCfg','BackHomeCustomerCfg','HeroRecordCfg',
            'ConditionCfg','IdolTraineeChapterCfg','IdolTraineePveBattleCfg','IdolTraineePvpBattleCfg',
            'IdolPveNpcCfg','IdolTraineeSkillCfg','IdolTraineeSkillEffectCfg','IdolTraineeRewardRankCfg',
            'IdolStageSceneCfg','IdolStageMusicCfg','IdolDanceDIYActionCfg'}) do
            local c=require(name);local rows={}
            for _,id in ipairs(c.all or {}) do rows[tostring(id)]=c[id] end
            if not c.all then for id,v in pairs(c) do if type(id)=='number' then rows[tostring(id)]=v end end end
            r[name]=rows
        end
        for k,v in pairs(require('GameSetting')) do
            if type(k)=='string' and (k:find('^dorm_') or k:find('^canteen_') or k:find('^backhome_')
                or k:find('^idol_')) then r.settings[k]=v.value end
        end
        r.settings.floor_max=require('GameDisplayCfg').dorm_area_layer_num.value[1]
        return r
    end)()''')


def home_config(name: str, item: int) -> dict:
    """读取存在的游园街配置，错误编号明确拒绝。"""
    row = backhome_catalog()[name].get(str(item))
    if row is None:
        raise ValueError('游园街配置不存在：' + name + ':' + str(item))
    return row


def archive_id(hero: int) -> int:
    """不同形态角色共享同一个后宅档案。"""
    return next((int(i) for i, row in backhome_catalog()['HeroRecordCfg'].items() if hero in row['hero_id']), hero)


def home_hero(user: dict, hero: int) -> dict:
    """只允许已拥有且存在后宅模型的修正者。"""
    if str(hero) not in backhome_catalog()['BackHomeHeroCfg'] or not any(
            h['id'] == hero and h.get('unlock', 1) for h in user.get('heroes', [])):
        raise ValueError('游园街角色未拥有')
    return user['backhome']['heroes'][str(archive_id(hero))]


def room_level(room: dict) -> int:
    """按累计赠礼经验换算心居等级，与客户端逐级经验表一致。"""
    remaining, level = room.get('exp', 0), 1
    rows = backhome_catalog()['BackHomeDormLevel']
    while str(level) in rows and level < len(rows) and remaining >= rows[str(level)]['exp']:
        remaining -= rows[str(level)]['exp']
        level += 1
    return level


def home_condition(user: dict, condition: int, hero: int = 0) -> bool:
    """核对房间数量、食堂家具等级、烹饪次数及角色心居条件。"""
    if not condition:
        return True
    cfg = home_config('ConditionCfg', condition)
    kind, args = cfg['type'], cfg['params']
    state = user['backhome']
    if kind == 8101:
        return sum(home_config('BackHomeCfg', r['id'])['type'] == 3 for r in state['rooms']) >= args[0]
    if kind == 8013:
        return next((r['level'] for r in state['canteen_furnitures'] if r['uid'] == args[0]), 0) >= args[1]
    if kind == 8014:
        return state['canteen']['accruing_earnings'] >= args[0]
    if kind == 8017:
        return sum(r['num'] for i, r in state['food'].items()
                   if home_config('BackHomeCanteenFoodCfg', int(i))['cook_type'] == args[0]) >= args[1]
    if kind in (8021, 8022, 8023):
        aid = archive_id(hero)
        room = next((r for r in state['rooms'] if aid in r['archives_id']
                     and home_config('BackHomeCfg', r['id'])['type'] == 3), None)
        value = state['heroes'].get(str(aid), {}).get('total_feed_times', 0) if kind == 8023 else 0
        if room and kind == 8021:
            value = sum(r['num'] for r in room['give_furnitures'])
        if room and kind == 8022:
            value = room_level(room)
        return value >= args[0]
    from hero_training_data import owned_hero, training_condition
    if hero:
        return training_condition(user, owned_hero(user, hero), condition)
    return False
