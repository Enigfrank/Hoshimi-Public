"""本机资源中的大厅任务与常规作战初始化数据。"""

from functools import lru_cache
import time

from activity_data import build_available_activities
from gameplay_protocol import encode_message, read_client_data, lua_value
from challenge_data import challenge_pushes
from reserve_data import reserve_data
from battle_progress import stage_progress, hero_teaching
from daily_progress import daily_pushes
from mult_heart_demon import mult_heart_demon_pushes
from passport_progress import passport_rewards


@lru_cache(maxsize=1)
def client_catalog() -> dict:
    """读取当前客户端配置，按玩法保留初始化需要的字段。"""
    return read_client_data('''(function()
        local result = {tasks={}, resources={}, equip_groups={}, teach={}, hero_teach={}, equip_suit_ids={}}
        local activities={}
        for _,id in ipairs(__ACTIVITY_IDS__) do activities[id]=true end
        local cfg = require('AssignmentCfg')
        for _, id in ipairs(cfg.all) do
            local c = cfg[id] or require('AssignmentCfg2')[id]
            if c.type == 3 or c.type == 5 or c.type == 6 or c.type == 7 or c.type == 8 or c.type == 9
                or c.type == 603 or c.type == 604 or c.type == 605 or c.type == 606
                or c.type == 801 or c.type == 802 or c.type == 803 or c.type == 804 or c.type == 3007
                or c.type == 901 or c.type == 902 or c.type == 903
                or activities[c.activity_id] then
                result.tasks[#result.tasks+1] = {id=id, type=c.type, phase=c.phase, need=c.need,
                    activity_id=c.activity_id, reward=c.reward, condition=c.condition,
                    additional_parameter=c.additional_parameter}
            end
        end
        cfg = require('BattlePassListCfg')
        for _, id in ipairs(cfg.all) do
            if cfg[id].activity_id == 4410001 then result.passport_id = id end
        end
        cfg = require('BattleDailyStageCfg')
        for _, id in ipairs(cfg.all) do result.resources[#result.resources+1] = id end
        cfg = require('StageGroupCfg')
        for _, id in ipairs(cfg.all) do
            if require('BattleEquipStageCfg')[cfg[id].stage_list[1]] then
                result.equip_groups[#result.equip_groups+1] = id
            end
        end
        cfg = require('BattleBaseTeachStageCfg')
        for _, id in ipairs(cfg.all) do result.teach[#result.teach+1] = id end
        cfg = require('BattleHeroTeachStageCfg')
        for _, id in ipairs(cfg.all) do result.hero_teach[#result.hero_teach+1] = id end
        result.equip_suit_ids = require('EquipSuitCfg').all
        return result
    end)()'''.replace('__ACTIVITY_IDS__', lua_value(
        [cfg['activity_id'] for cfg in build_available_activities() if cfg['state'] == 1])))


def passport_data(user: dict) -> dict:
    """初始化本机主题的对策协议，保持未购买状态及有效开放时间。"""
    now = int(time.time())
    return {"battlepass_list_id": client_catalog()["passport_id"], "pay_level": user.get('passport_pay_level', 0), "is_start": 1,
            "next_refresh_timestamp": now + 7 * 86400, "weekly_gain_exp": user.get('passport_weekly_exp', 0),
            "start_timestamp": now - 86400, "end_timestamp": now + 365 * 86400,
            "receive_info": passport_rewards(user)}


def task_data(user: dict) -> dict:
    """下发有效任务及真实存档进度，剧情任务从第一阶段开始。"""
    if 'resident_rogue' in user:
        from resident_tasks import update_rogue_tasks
        update_rogue_tasks(user)
    if 'resident' in user:
        from resident_tasks import update_resident_tasks
        update_resident_tasks(user)
    if 'resident_board' in user:
        from resident_tasks import update_board_tasks
        update_board_tasks(user)
    if 'backhome' in user:
        from backhome_tasks import update_backhome_tasks
        update_backhome_tasks(user)
    from chapter_maps import update_chapter_tasks
    update_chapter_tasks(user)
    if 'mult_heart_demon' in user:
        from mult_heart_demon import update_demon_tasks
        update_demon_tasks(user)
    saved = user.get("tasks", {})
    tasks = []
    activities = {cfg['activity_id'] for cfg in build_available_activities() if cfg['state'] == 1}
    for cfg in client_catalog()["tasks"]:
        if cfg["activity_id"] and cfg["activity_id"] not in activities:
            continue
        if cfg["type"] == 3 and cfg["phase"] != 1:
            continue
        entry = saved.get(str(cfg["id"]), {})
        tasks.append({"id": cfg["id"], "progress": entry.get("progress", 0),
                      "complete_flag": entry.get("complete_flag", 0)})
    return {"send_type": 0, "newbie_phase": 1, "assignment_phase": 1, "assignment_list": tasks}


def task_points(user: dict) -> dict:
    """补齐日、周和新手活跃度，防止任务奖励控件索引空数据。"""
    saved = user.get("task_points", {})
    return {"pt_list": [{"activity_pt_id": id, "active_point": saved.get(str(id), {}).get("point", 0),
                         "get_id_list": saved.get(str(id), {}).get("claimed", [])} for id in (1, 2, 3)]}


def equip_battle_data(user: dict) -> dict:
    """选择有效刻印关卡组和默认套装，避免客户端刷新界面索引空配置。"""
    suit = user.get("equip_up_suit")
    if suit not in equip_suit_ids():
        suit = equip_suit_ids()[0]
    return {"stage_base_id": client_catalog()["equip_groups"][0], "equip_suit_id": suit,
            "next_refresh_time": int(time.time()) + 86400,
            "insure_list": [{"difficulty": i, "insure_times": 0} for i in range(1, 7)]}


def equip_suit_ids() -> list[int]:
    """读取客户端有效刻印套装 ID，用于校验 GM 存档中的旧值。"""
    return client_catalog()["equip_suit_ids"]


def build_gameplay_pushes(user: dict) -> list[tuple]:
    """在进入大厅前初始化入口与作战页面依赖的数据。"""
    from activity_pt_scroll import activity_pt_scroll_pushes
    from resident_data import resident_pushes
    from hero_training_progress import training_initial_pushes
    from backhome_requests import backhome_initial_pushes
    from admin_cat_explore import explore_data
    from neural_matrix import neural_data
    now = int(time.time())
    catalog = client_catalog()
    data = [
        (34031, passport_data(user)),
        (67001, explore_data(user)),
        (89801, neural_data(user)),
        *daily_pushes(user),
        (28001, task_data(user)),
        (28019, task_points(user)),
        (25009, {"daily_battle_list": [{"id": v['id'], "clear_times": v['clear_times']}
                                      for v in stage_progress(user, 2, catalog["resources"], user.get('account_template') != 'normal')]}),
        (43001, equip_battle_data(user)),
        (47001, {"base_stage_list": [{"id": v['id'], "clear_times": v['clear_times']}
                                     for v in stage_progress(user, 12, catalog["teach"])]}),
        (47003, hero_teaching(user)),
        (63005, reserve_data(user)),
        *challenge_pushes(user),
        *activity_pt_scroll_pushes(user),
        *mult_heart_demon_pushes(user),
        *resident_pushes(user),
        *training_initial_pushes(user),
        *backhome_initial_pushes(user),
    ]
    return [(cmd, encode_message(cmd, values), 0, 0.0) for cmd, values in data]


def battle_progress_pushes(user: dict, stage_type: int) -> list[tuple]:
    """结算只同步当前玩法进度，避免重新初始化任务、排期和编队。"""
    from battle_progress import story_progress
    from equip_exploration import exploration_pushes
    from hero_training_progress import training_battle_pushes
    if stage_type == 61:
        from activity_pt_scroll import activity_pt_scroll_pushes
        data = activity_pt_scroll_pushes(user)
        data += training_battle_pushes(user)
        return [(cmd, encode_message(cmd, values), 0, 0.0) for cmd, values in data]
    catalog = client_catalog()
    if stage_type in (1, 601):
        from chapter_maps import chapter_map_pushes
        data = [(24009, story_progress(user))] + [(c, v) for c, v in chapter_map_pushes(user) if c in (24029, 24061)]
    elif stage_type == 82:
        data = [(75025, {'score': user.get('mult_heart_demon_score', 0)})]
    elif stage_type == 93:
        from neural_matrix import neural_data, neural_state
        data = [(89801, neural_data(user)), (89811, {'score': neural_state(user)['last_score']})]
    elif stage_type == 2:
        data = [(25009, {'daily_battle_list': [{'id': v['id'], 'clear_times': v['clear_times']}
                 for v in stage_progress(user, 2, catalog['resources'], True)]})]
    elif stage_type == 12:
        data = [(47001, {'base_stage_list': [{'id': v['id'], 'clear_times': v['clear_times']}
                 for v in stage_progress(user, 12, catalog['teach'])]})]
    elif stage_type == 13:
        data = [(47003, hero_teaching(user))]
    elif stage_type == 40:
        data = exploration_pushes(user)
    elif stage_type in (602, 603, 71, 72, 73, 74, 84, 85, 86, 87, 89, 92, 75):
        from resident_data import resident_pushes
        commands = {602: {24051}, 603: {89401}, 74: {83125}, 84: set(), 85: set(), 86: {68171}, 87: {89011},
                    89: {68181}, 92: {89421}, 71: set(), 72: set(), 73: set(), 75: set()}[stage_type]
        data = [(c, v) for c, v in resident_pushes(user) if c in commands]
        if stage_type == 74:
            resident = user.setdefault('resident', {})
            state = resident.setdefault('music', {})
            stage_id = resident.get('music_last_stage', 0)
            row = next((r for r in state.get('challenge_info', []) if r['id'] == stage_id), None)
            if row:
                data.append((83125, {'rhythm_stage_info': {'id': row['id'],
                    'star_id_list': [1] if row.get('sign') else [], 'use_seconds': row.get('seconds', 0)}}))
        if stage_type in (84, 85):
            from resident_world import world_data, world_sea_data
            data.append((28803, world_data(user)))
            data.append((86035, world_sea_data(user)))
        if stage_type in (86, 89):
            from resident_combat import combat_point_data
            state = user['resident']
            key = 'osiris' if stage_type == 86 else 'ash'
            current = state.get(key+'_current') or next((r for r in state[key]['pass_stages'] if r['stage_id'] == state.get(key+'_last_stage')), None)
            if current:
                data.append((68177 if stage_type == 86 else 68183, {'pass_stage': current}))
            data.append((60201, combat_point_data(user, 323801 if stage_type == 86 else 3639701)))
    else:
        commands = {7: {41001}, 10: {45001}, 100: {45101}, 11: {44009},
                    35: {44023}, 1003: {55001}, 67: {75009}}.get(stage_type, set())
        data = [(cmd, value) for cmd, value in challenge_pushes(user) if cmd in commands]
    data += training_battle_pushes(user)
    return [(cmd, encode_message(cmd, values), 0, 0.0) for cmd, values in data]
