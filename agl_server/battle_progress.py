"""将实际通关记录转换为各常规玩法的初始化数据。"""

from functools import lru_cache

from .gameplay_protocol import read_client_data


def progress(user: dict, stage_type: int, stage_id: int) -> dict:
    """读取指定玩法的实际次数、星数和最短耗时。"""
    return user.get('battle_progress', {}).get(f'{stage_type}:{stage_id}', {})


def stage_progress(user: dict, stage_type: int, stage_ids: list, unlocked: bool = False) -> list:
    """保留本地服原有解锁种子，并叠加后续真实战斗记录。"""
    result = []
    for stage_id in stage_ids:
        saved = progress(user, stage_type, stage_id)
        result.append({'id': stage_id, 'clear_times': max(int(unlocked), saved.get('clear_times', 0)),
                       'star_list': [int(i in saved.get('stars', [])) for i in (1, 2, 3)]})
    return result


@lru_cache(maxsize=1)
def progress_catalog() -> dict:
    """读取角色归属、历战章节和主线各关的有效星级条件。"""
    return read_client_data('''(function()
        local r={hero_teach={},tower={},story={},trial_heroes={}}
        local c=require('BattleHeroTeachStageCfg')
        for _,id in ipairs(c.all) do r.hero_teach[#r.hero_teach+1]=c[id] end
        -- 试用关与角色研习关的归属，供 3-3 等入口的“已体验”判断使用。
        for kind,name in pairs({[20]='BattleHeroTrialStageCfg',[76]='BattleResidentHeroTrialStageCfg'}) do
            c=require(name)
            for _,id in ipairs(c.all) do
                local heroes={}
                for _,hero in ipairs(type(c[id].hero_list)=='table' and c[id].hero_list or {}) do
                    if hero[1] and hero[1]>0 then heroes[#heroes+1]=hero[1] end
                end
                r.trial_heroes[kind..':'..id]=heroes
            end
        end
        c=require('ChapterCfg')
        local stages=require('BattleChapterStageCfg')
        local seen={}
        for _,id in ipairs(c.all) do
            if c[id].type==7 then r.tower[#r.tower+1]=c[id] end
            if c[id].type==1 then
                for _,stage in ipairs(c[id].section_id_list) do
                    if not seen[stage] then
                        seen[stage]=true
                        r.story[#r.story+1]={id=stage,star_count=#stages[stage].three_star_need,
                                             cost=stages[stage].cost or 0}
                    end
                end
            end
        end
        return r
    end)()''')


def story_progress(user: dict) -> dict:
    """恢复剧情星级及活动、联合特勤真实通关记录，供客户端判断扫荡资格。

    客户端按 ``clear_times`` 决定主线关卡显示的体力消耗：未通关显示配置消耗，
    通关后显示 0。普通账号只给无体力消耗的剧情节点保留入口解锁种子，战斗关卡
    回传真实首通状态，避免显示 0 却实际扣体力；模板账号维持全解锁。
    """
    stages = {cfg['id']: cfg for cfg in progress_catalog()['story']}
    initial = user.get('account_template') == 'normal'
    rows = stage_progress(user, 1, list(stages))
    for row in rows:
        cfg = stages[row['id']]
        cost = cfg['cost']
        seed = int((cost[1] if isinstance(cost, list) else cost) == 0) if initial else 1
        row['clear_times'] = max(seed, row['clear_times'])
        row['star_list'] = [int(i <= cfg['star_count'] and (not initial or row['star_list'][i - 1]))
                            for i in (1, 2, 3)]
    for key, saved in user.get('battle_progress', {}).items():
        kind, stage_id = map(int, key.split(':'))
        # 601 与联合特勤 8 需要回传真实通关次数，客户端据此解锁扫荡。
        if kind in (8, 601):
            rows.append({'id': stage_id, 'clear_times': saved['clear_times'],
                         'star_list': [int(i in saved.get('stars', [])) for i in (1, 2, 3)]})
    return {'user_chapter_list': rows}


def hero_teaching(user: dict) -> dict:
    """按配置的教学角色下发通关记录。

    客户端判断“是否体验过某角色”时读取的是角色研习记录
    （``BattleTeachData:GetHeroTeachInfo``），而不是试用关本身的通关行。
    因此这里把已通关的试用关按角色折算为对应研习关已通关，避免主线入口
    反复弹出试用提示；不改写存档，只在初始化数据中补一条派生记录。
    """
    catalog = progress_catalog()
    groups = {}
    recorded = user.get('battle_progress', {})
    trial_heroes = {hero for key, heroes in catalog['trial_heroes'].items()
                    if recorded.get(key, {}).get('clear_times', 0) > 0 for hero in heroes}
    for cfg in catalog['hero_teach']:
        hero = cfg['hero_list'][0][0]
        saved = progress(user, 13, cfg['id'])
        clear_times = saved.get('clear_times', 0)
        if clear_times or hero in trial_heroes:
            groups.setdefault(hero, []).append({'id': cfg['id'], 'clear_times': clear_times or 1,
                                                'star_list': len(saved.get('stars', []))})
    return {'hero_teaching_list': [{'hero_id': h, 'stage_list': stages} for h, stages in groups.items()]}


def tower_progress(user: dict) -> dict:
    """历战轮回按章节保存当前已通关的最远关卡。"""
    result = []
    for cfg in progress_catalog()['tower']:
        cleared = [s for s in cfg['section_id_list'] if progress(user, 7, s).get('clear_times')]
        if cleared:
            result.append({'area': cfg['id'], 'stage': cleared[-1]})
    return {'info_list': result}
