"""本机可加载的主题活动、主线解锁排期与常驻入口。"""

from functools import lru_cache
import time

RESOURCE_ACTIVITY_THEME = 44

# 保留活动记录，结束旧换装图标及未采用的古远期次，避免重复同名入口。
HIDDEN_ACTIVITY_ENTRIES = {3700413, 4435002, 4435003, 4435004, 4435005,
                         4443301, 4443302, 4443303, 4443401, 4443402, 4443403, 4443501, 4443502, 4443503}

# activity_id: (theme, template, sub_activity_ids)
RESOURCE_ACTIVITIES = {
    111: (0, 74, (4443301, 4443302, 4443303, 4443304)),
    4400101: (44, 1, ()),
    4400301: (44, 3, ()),
    4400303: (44, 3, ()),
    4405601: (44, 56, (4418201,)),
    4407301: (44, 73, ()),
    4407302: (44, 73, ()),
    4410001: (44, 100, (4435001, 4435002, 4435003, 4435004, 4435005)),
    4412001: (44, 120, (4405601,)),
    4418201: (44, 182, ()),
    4435001: (44, 350, ()),
    4435002: (44, 350, ()),
    4435003: (44, 350, ()),
    4435004: (44, 350, ()),
    4435005: (44, 350, ()),
    4443301: (44, 433, (4443401, 4443501)),
    4443302: (44, 433, (4443402, 4443502)),
    4443303: (44, 433, (4443403, 4443503)),
    4443304: (44, 433, (4443404, 4443504)),
    4443401: (44, 434, ()),
    4443402: (44, 434, ()),
    4443403: (44, 434, ()),
    4443404: (44, 434, ()),
    4443501: (44, 435, ()),
    4443502: (44, 435, ()),
    4443503: (44, 435, ()),
    4443504: (44, 435, ()),
}

# 常驻玩法在原主题活动结束后开放，父记录必须使用已结束的排期。
RESIDENT_ACTIVITY_MAINS = {
    281001: (28, 100, (280001, 281201, 271073, 282981, 281891, 282491, 281002, 282431, 281311, 282911, 282261, 282131, 282432, 281892, 282132, 283181, 271312, 272702, 281071, 281521, 282701, 282702, 282703, 282704, 282705)),
    321001: (32, 100, (321201, 323641, 323401, 321311, 321051, 322921, 323601, 321891, 321071, 322431, 322121, 322122, 323501, 322141, 321202)),
    331001: (33, 100, (332261, 333501, 333231, 332431, 331201, 331891, 323801, 331071, 331161, 332131, 331311)),
    3510001: (35, 100, (3539101, 3522101, 3530901, 3512001, 3524501, 3500301, 3500302, 3500303, 3500304, 3500305, 3500101, 3510701, 3513101, 3518901, 3524301, 3535001, 3521301)),
    3610001: (36, 100, (3600101, 3612001, 3618901, 3635001, 3639901, 3613101, 3639701, 3624301, 3600301, 3600302, 3600303, 3600304, 3600305, 3621301, 3610701, 3621401)),
    3710001: (37, 100, (3740601, 3721301, 3721302, 3733801, 3735001, 3734001, 3721601, 3718901, 3724301, 3712001, 3710701, 3713101, 3729101)),
    3810001: (38, 100, (3840801, 3840901, 3814801, 3822601, 3825501, 3818901, 3812001, 3821001, 3821002, 3811601, 3810701, 3835001, 3824301, 3813101, 3821301, 3838701)),
    3910001: (39, 100, (3900301, 3900302, 3900303, 3900304, 3900305, 3941101, 3913101, 3910701, 3938701, 3934205, 3912001, 3918901, 3921301, 3924301, 3924501, 3935001)),
    4010001: (40, 100, (4041301, 4038701, 4042101, 4038702, 4022101, 4042501, 4043001, 4043010, 4035001, 4034001, 4018901, 4038703, 4021301, 4010701, 4013101, 4012001, 4024301, 4036501, 4032301, 4042301, 4042901)),
    4110001: (41, 100, (4142701, 4138701, 4121401, 4121301, 4121302, 4118901, 4129201, 4134001, 4135001, 4110701, 4124301, 4113101, 4138501, 4112002, 4112001, 4143201, 4121101)),
    4210001: (42, 100, (4243101, 4218901, 4214801, 4212001, 4210701, 4213101, 4221301, 4224301, 4235001, 4222601)),
    4310001: (43, 100, (4312001, 4343601, 4318901, 4310701, 4321301, 4335001, 4300102, 4324301, 4322101)),
}

@lru_cache(maxsize=1)
def chapter_activity_catalog() -> list[dict]:
    """只读取主线解锁依赖及已核对页面资源的常驻玩法，补齐其子活动。"""
    from gameplay_protocol import read_client_data
    return read_client_data('''(function()
        local result={};local seen={};local activities=require('ActivityCfg')
        local function add(id)
            if id==0 or seen[id] then return end
            local c=assert(activities[id], '活动配置缺失: '..id)
            seen[id]=true
            result[#result+1]={activity_id=id,theme=c.activity_theme,
                template=c.activity_template,sub_activity_id_list=c.sub_activity_list}
            for _,sub in ipairs(c.sub_activity_list) do add(sub) end
        end
        local chapters=require('ChapterCfg')
        for _,id in ipairs(chapters.all) do
            if chapters[id].type==1 then
                add(chapters[id].activity_id);add(chapters[id].unlock_activity_id)
            end
        end
        local maps=require('ChapterMapCfg')
        for _,id in ipairs(maps.all) do
            if chapters[maps[id].chapter_id] and chapters[maps[id].chapter_id].type==1 then
                add(maps[id].activity_id)
            end
        end
        local clients=require('ChapterClientCfg')
        for _,id in ipairs(clients.get_id_list_by_toggle[8]) do add(clients[id].activity_id) end
        add(require('ActivityConst').SIGN)
        return result
    end)()''')


def build_available_activities() -> list[dict]:
    """开放主线及常驻子活动，原主题父记录保持结束以解锁常驻页签。"""
    now = int(time.time())
    result = {}
    for aid, (theme, template, subs) in RESOURCE_ACTIVITIES.items():
        result[aid] = {"activity_id": aid, "theme": theme, "template": template,
                       "state": 1, "sub_activity_id_list": list(subs)}
    for cfg in chapter_activity_catalog():
        result[cfg['activity_id']] = {**cfg, 'state': 1}
    for aid in HIDDEN_ACTIVITY_ENTRIES:
        if aid in result:
            result[aid] = {**result[aid], 'state': 0, 'start_time': now - 30 * 86400,
                'stop_time': now - 86400}
    for aid, (theme, template, subs) in RESIDENT_ACTIVITY_MAINS.items():
        result[aid] = {"activity_id": aid, "theme": theme, "template": template,
                       "state": 0, "start_time": now - 30 * 86400, "stop_time": now - 86400,
                       "sub_activity_id_list": list(subs)}
    from gacha_catalog import draw_activities
    for cfg in draw_activities():
        result[cfg['activity_id']] = cfg
    from skin_draw_catalog import skin_activities
    for cfg in skin_activities():
        result[cfg['activity_id']] = cfg
    from activity_periods import PERIOD_ACTIVITIES, period_settings, period_window
    settings = period_settings()
    for key, ids in PERIOD_ACTIVITIES.items():
        start, end = period_window(key, settings, now)
        for aid in ids:
            result[aid].update(start_time=start, stop_time=end)
    return list(result.values())
