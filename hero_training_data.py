"""修正者培养共用的客户端配置、拥有校验与经验换算。"""

from functools import lru_cache

from battle_rewards import grant_rewards
from gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def training_catalog() -> dict:
    """读取培养所需字段，所有成本和条件以当前客户端配置为准。"""
    return read_client_data('''(function()
        local r={settings={},heroes={},items={}}
        for _,name in ipairs({'GameLevelSetting','HeroBreakCfg','SkillCfg','HeroSkillElementCfg',
            'HeroAstrolabeCfg','WeaponModuleCfg','EquipMaterialCfg','ConditionCfg','HeroRecordCfg',
            'HeroStarUpTemplateCfg','HeroStarSkillCfg','ComboSkillCfg','ComboSkillLevelCfg',
            'HeroTrustCfg','HeroTrustLevelCfg','HeroRelationNetCfg','HeroRelationUpgradeCfg',
            'HeroRelationUpgradeConditionCfg','HeroRelationCfg','HeroAnedoteCfg'}) do
            local c=require(name);local rows={}
            for _,id in ipairs(c.all) do
                local v=c[id]
                if name=='WeaponModuleCfg' then rows[tostring(id)]={cost=v.cost,condition=v.condition,source=v.source}
                elseif name=='HeroRecordCfg' then rows[tostring(id)]={hero_id=v.hero_id,gift_like_id1=v.gift_like_id1}
                elseif name=='HeroAnedoteCfg' then rows[tostring(id)]={id=v.id,reward=v.reward}
                else rows[tostring(id)]=v end
            end
            r[name]=rows
        end
        local c=require('GameSetting')
        for _,k in ipairs({'hero_exp_props_id_list','hero_skill_level_max','hero_skill_attr_level_max',
            'weapon_exp_limit','weapon_user_limit','weapon_strengthen_gold_cost','weapon_break_cost',
            'base_exp_weapon_servant','exclusive_skill_talent_num_max','exclusive_skill_use_num_max',
            'exclusive_skill_talent_strengthen_cost','hero_trust_exchange_need',
            'hero_trust_favor_gift_add_exp','hero_trust_exchange_up_limit',
            'weapon_module_break_return','unlock_hero_need','gift_value',
            'hero_anecdote_unlock_condition'}) do r.settings[k]=c[k].value end
        r.settings.love_level_max=require('GameDisplayCfg').hero_love_level_max.value[1]
        local h=require('HeroCfg')
        for _,id in ipairs(h.all) do local v=h[id]
            r.heroes[tostring(id)]={race=v.race,rare=v.rare,astrolabe=v.astrolabe,
                skills=v.skills,hero_equip_skill=v.hero_equip_skill,study_stage=v.study_stage,
                star_up_skill_template=v.star_up_skill_template}
        end
        local i=require('ItemCfg')
        for _,id in ipairs(i.all) do local v=i[id] or require('ItemCfg2')[id]
            r.items[tostring(id)]={type=v.type,sub_type=v.sub_type,param=v.param}
        end
        return r
    end)()''')


def owned_hero(user: dict, hero_id: int) -> dict:
    """培养只允许实际拥有的角色，皮肤占位角色不能参与。"""
    hero = next((h for h in user.get('heroes', []) if h['id'] == hero_id and h.get('unlock', 1)), None)
    if hero is None:
        raise ValueError('角色未拥有')
    return hero


def item_cost(rows: list) -> list[dict]:
    """将配置二元组转换为扣款条目，忽略配置中的零费用。"""
    return [{'id': i, 'num': n} for i, n in (rows or []) if n > 0]


def exp_at(level: int, weapon: bool = False) -> int:
    """返回等级的累计经验下限。"""
    field = 'weapon_lv_exp_sum' if weapon else 'hero_lv_exp_sum'
    return training_catalog()['GameLevelSetting'][str(level)][field]


def exp_level(amount: int, maximum: int, weapon: bool = False) -> int:
    """在当前突破上限内按累计经验换算等级。"""
    return max(i for i in range(1, maximum + 1) if exp_at(i, weapon) <= amount)


def hero_break_config(hero: dict, stage: int) -> dict:
    """按角色神系与突破次数取配置，拒绝超出最高阶段。"""
    race = training_catalog()['heroes'][str(hero['id'])]['race']
    row = next((r for r in training_catalog()['HeroBreakCfg'].values()
                if r['race'] == race and r['break_times'] == stage), None)
    if row is None:
        raise ValueError('突破阶段无效')
    return row


def material_exp(rows: list, subtype: int) -> int:
    """验证材料种类和正数量，累计实际经验；扣款统一处理重复条目。"""
    amount = 0
    items = training_catalog()['items']
    for row in rows:
        cfg = items.get(str(row['id']), {})
        if row['num'] <= 0 or cfg.get('sub_type') != subtype or not cfg.get('param'):
            raise ValueError('培养材料类型或数量无效')
        amount += cfg['param'][0] * row['num']
    return amount


def return_exp(user: dict, amount: int, subtype: int) -> list[dict]:
    """按客户端材料面额返还溢出经验，未足一份的余数不生成物品。"""
    rows = sorted((int(i) for i, c in training_catalog()['items'].items()
                   if c.get('sub_type') == subtype and c.get('param')), reverse=True)
    rewards = []
    for item in rows:
        count, amount = divmod(amount, training_catalog()['items'][str(item)]['param'][0])
        if count:
            rewards.append({'id': item, 'num': count})
    grant_rewards(user, rewards)
    return rewards


def skill_level(hero: dict, skill_id: int) -> int:
    """兼容旧存档的统一技能等级和新存档的单技能等级。"""
    return hero.get('skill_levels', {}).get(str(skill_id), hero.get('skill_level', 1))


def total_skill_level(hero: dict, skill_id: int) -> int:
    """条件计入超越带来的技能加级，不计入跃迁加级，与原始HeroTools一致。"""
    catalog = training_catalog()
    cfg = catalog['heroes'][str(hero['id'])]
    if skill_id not in cfg['skills']:
        return 0
    index = cfg['skills'].index(skill_id) + 1
    extra = 0
    for row in catalog['HeroStarUpTemplateCfg'].values():
        if row['template'] == cfg['star_up_skill_template'] and row['stage'] <= hero['star']:
            effect = catalog['HeroStarSkillCfg'][str(row['skill_id'])]
            if effect['type'] == 2:
                extra += sum(n for i, n in effect['params'] if i == index)
    return skill_level(hero, skill_id) + extra


def stage_cleared(user: dict, stage: int) -> bool:
    """只认可已保存的通关次数，不以客户端入口开放代替战斗完成。"""
    return any(key.split(':')[-1] == str(stage) and row.get('clear_times', 0) > 0
               for key, row in user.get('battle_progress', {}).items())


def training_condition(user: dict, hero: dict, condition: int) -> bool:
    """核对神格、同调和交心使用的实际成长、剧情与通关条件。"""
    if condition == 0:
        return True
    cfg = training_catalog()['ConditionCfg'].get(str(condition))
    if not cfg:
        return False
    kind, params = cfg['type'], cfg['params']
    skills = training_catalog()['heroes'][str(hero['id'])]['skills']
    if kind == 1001:
        return hero['level'] >= params[0]
    if kind == 1002:
        return sum(1 if i % 1000 == 305 else total_skill_level(hero, i) for i in skills) >= params[0]
    if kind == 1003:
        return hero['star'] // 100 >= params[0]
    if kind == 1004:
        return hero.get('clear_times', 0) >= params[0]
    if kind == 1007:
        return exp_level(hero.get('weapon_exp', 0), 60, True) >= params[0]
    if kind == 1008:
        return all(stage_cleared(user, i) for i in training_catalog()['heroes'][str(hero['id'])]['study_stage'])
    if kind == 1009:
        return total_skill_level(hero, skills[4]) >= params[0]
    if kind == 1010:
        return hero['break_level'] >= params[0]
    if kind in (1011, 5002):
        return stage_cleared(user, params[0])
    if kind == 1019:
        return hero.get('trust_level', 1) >= params[0]
    if kind == 5001:
        archive = user.get('archives', {}).get(str(params[0]), {})
        return 5 in archive.get('text_list', [])
    if kind == 5003:
        return user.get('polyhedron_leader_clears', {}).get(str(params[0]), 0) >= params[2]
    if kind == 8023:
        from backhome_catalog import archive_id
        saved = user.get('backhome', {}).get('heroes', {}).get(str(archive_id(hero['id'])), {})
        return saved.get('total_feed_times', hero.get('dine_times', 0)) >= params[0]
    return False
