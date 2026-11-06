"""安装包缺少官服随机池与事件跳转表时使用的本地事件路由。"""

OPTIONS = r'''
-- 按当前层的房间配置选择事件战斗，关卡编号仍以安装包为准。
local function event_stage(plan,kind)
    local room=cfg('RogueTeamRoomCfg',kind*1000+r.floor)
    plan.stage_id=pick(room.params);plan.room_id=room.id
end
-- 在事件出现时固定随机结果；重连、重复选项查询不重新抽取。
option_plan=function(id)
    local c=cfg('RogueTeamEventOptionCfg',id);local p=c.params
    local plan={items=arr()}
    local nextid=math.floor(id/10)+id%10
    if require('RogueTeamEventCfg')[nextid] then plan.next_id=nextid end
    if type(p)=='table' and p[1]==2 then
        local code=math.floor(p[2][1]/1000)
        local ids=effect_pool(code)
        if code==310 then ids=has(r.items,1310004) and {1310004} or {} end
        if code==312 then ids={1330001} end
        if code==317 then
            ids={1310055};plan.room_id=11001
            plan.stage_id=require('GameSetting').rogue_team_demon_select_stage.value[1]
        end
        if #ids>0 then plan.items[1]={id=pick(ids),rare=1};plan.remove=code==305 end
    end
    local battles={
        [201612]={1,2},[300111]={1,1},[300241]={2,0},[300321]={2,0},
        [300501]={1,1},[300511]={2,1,1},[300601]={2,2,1},[300602]={2,1,1},
        [300603]={1,1},[300701]={1,1},[300841]={2,1,1},[400401]={1,0},
        [600411]={1,1},[600511]={1,1}}
    local battle=battles[id]
    if battle then event_stage(plan,battle[1]);plan.treasure_count=battle[2];plan.relic_count=battle[3] or 0;plan.next_id=nil end
    if id>=300911 and id<=301052 then
        local camps={1,2,3,4,5,6};local pairs={}
        for i=1,5 do for j=i+1,6 do pairs[#pairs+1]={camps[i],camps[j]} end end
        local pair=pairs[math.floor((math.floor(id/10)-30091)) + 1]
        if pair then event_stage(plan,2);plan.camp=pair[id%10];plan.treasure_count=2;plan.next_id=nil end
    end
    if id==101201 then plan.next_id=10120+random(6)
    elseif id==300901 then plan.next_id=30090+random(15)
    elseif id==400501 then plan.next_id=40050+random(6)
    elseif id==200501 then plan.cost={1,30};plan.next_id=20051
    elseif id==200502 then plan.cost={6,120};plan.next_id=20052
    elseif id==500102 then plan.recruit=true;plan.next_id=nil
    elseif id==100401 then plan.coin=random(4)==1 and 200 or 50
    elseif id==100802 then
        local ids=pool(3,0,{1,2});if #ids>0 then plan.items[1]={id=pick(ids),rare=1};plan.random_remove=random(2)==1 end
    elseif id==100803 then
        if random(4)<=3 then local ids=pool(3,0,{2});if #ids>0 then plan.items[1]={id=pick(ids),rare=1} end end
    elseif id==300301 then
        if random(2)==1 then local ids=pool(3);if #ids>0 then plan.items[1]={id=pick(ids),rare=1} end
        else plan.next_id=30032 end
    elseif id==300801 then
        if random(2)==1 then plan.relic_count=1 else plan.hp=-2000 end
    elseif id==300802 then
        if random(2)==1 then plan.treasure_count=1;plan.rare=2 else event_stage(plan,2);plan.relic_count=1 end
    elseif id==300803 then
        if random(4)==1 then local ids=pool(3,0,{2});if #ids>0 then plan.items[1]={id=pick(ids),rare=1} end end
    end
    return plan
end
-- 执行服务器已固定的事件分支，战斗奖励只在真实胜利后发放。
choose_option=function(id,plan)
    if plan.cost then change(plan.cost[1],-plan.cost[2]) end
    unique(r.options,id)
    if plan.stage_id then r.event_battle=copy(plan);r.event_battle.opt_id=id;return end
    local c=cfg('RogueTeamEventOptionCfg',id);local params=c.params
    if type(params)=='table' and params[1]==1 then effects(params[2],1,0) end
    if plan.coin then change(6,plan.coin) end
    if plan.hp then heal(plan.hp) end
    if plan.recruit then assert(attr(8)>0,'招募次数不足');offer(3,{},1) end
    for _,item in ipairs(plan.items) do
        if plan.remove then remove_item(item.id) else obtain(item.id,item.rare,0) end
    end
    if plan.random_remove then
        local ids=effect_pool(305);if #ids>0 then remove_item(pick(ids)) end
    end
    for i=1,plan.treasure_count or 0 do offer(1,choices(pool(4,plan.camp),3),plan.rare or 1) end
    for i=1,plan.relic_count or 0 do offer(2,choices(pool(3),3),1) end
end
'''
