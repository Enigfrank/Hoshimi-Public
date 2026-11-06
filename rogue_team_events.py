"""虚构推演事件、局内物品与效果规则，供 Python 和 Go 复用。"""

EVENTS = r'''
local effects,obtain,offer,trigger,condition,option_plan,choose_option,remove_item
-- 枚举本模板允许的局内物品，并排除已经拥有的唯一物品。
local function pool(ty,camp,subtype)
    local list={}
    for _,item in ipairs(rows('RogueTeamItemCfg')) do
        if item.item_temp==template.item_temp and item.type==ty and
           (not camp or camp==0 or has(item.camp,camp)) and not has(r.items,item.id) and
           (not subtype or has(subtype,item.sub_type)) and
           (ty~=3 or subtype or item.sub_type==1) and
           (not condition or condition(item.condition)) then list[#list+1]=item.id end
    end
    return list
end
-- 查询背包中的藏品条目。
local function treasure(id) for _,v in ipairs(r.treasures) do if v.id==id then return v end end end
-- 排队等待玩家选择，后续效果不会覆盖尚未处理的选择。
offer=function(ty,ids,rare)
    local list=arr()
    for index,id in ipairs(ids) do list[#list+1]={index=index,param=id,buy_times=0,rare=rare or 1,is_new=1,discount=1000} end
    local event={event_type=ty,param_list=list,drop_type=0,shop_refresh_times=0}
    r.queue[#r.queue+1]=event
    if not r.pending then r.pending=table.remove(r.queue,1) end
end
-- 将抽取池限制为不同候选，避免同一窗口显示重复奖励。
local function choices(list,count)
    local bag=copy(list);local result={}
    for i=1,math.min(count,#bag) do result[#result+1]=table.remove(bag,random(#bag)) end
    return result
end
-- 记录物品变动，客户端同时恢复背包和获取动画。
local function item_push(item,rare,operation)
    local ty=cfg('RogueTeamItemCfg',item).type
    emit(88021,{item_list=arr({{opt=operation,treasure_list=ty==4 and arr({{id=item,rare=rare}}) or arr(),other_list=ty~=4 and arr({item}) or arr()}})})
    emit(88205,{update_list=arr({{operate=operation,id=item,rare=rare,source_rare=rare,is_new=1,source_type=0,source_item_id=0}})})
end
-- 添加局内藏品或遗物；重复藏品按配置上限升级。
obtain=function(id,rare,depth)
    assert((depth or 0)<12,'物品效果循环过深')
    local item=cfg('RogueTeamItemCfg',id);rare=rare or 1
    assert(item.item_temp==template.item_temp,'物品不属于当前模板')
    if item.type==4 then
        local old=treasure(id)
        if old then old.rare=math.min(4,math.max(old.rare,rare)+1);rare=old.rare
        else r.treasures[#r.treasures+1]={id=id,rare=rare};stat(3,1) end
        collect(1,id);item_push(id,rare,old and 3 or 1)
        local counts={}
        for _,t in ipairs(r.treasures) do for _,camp in ipairs(cfg('RogueTeamItemCfg',t.id).camp) do counts[camp]=(counts[camp] or 0)+1 end end
        for _,suit in ipairs(rows('RogueTeamItemCfg')) do if suit.type==5 and suit.item_temp==template.item_temp and (counts[suit.camp[1]] or 0)>=6 then
            r.suit_id=suit.camp[1];r.suit_value=r.suit_value or 0;emit(88211,{id=r.suit_id,value=r.suit_value})
        end end
    else
        if has(r.items,id) then return end
        r.items[#r.items+1]=id;r.effect_ticks[tostring(id)]=0
        if item.type==2 or item.type==3 then stat(item.type==2 and 5 or 4,1) end
        if item.type==3 then collect(3,id) elseif item.type==2 then collect(4,id) end
        item_push(id,rare,1)
        effects(item.effect_id_list,1,depth or 0,id,0)
    end
end
-- 修改真实保存的生命比例，下一场战斗继续使用同一队伍生命。
local function heal(rate)
    for _,h in ipairs(r.heroes) do h.hp_ratio=math.min(10000,math.max(0,h.hp_ratio+rate)) end
end
-- 评估事件与科技条件；不允许客户端越过资源和持有物检查。
condition=function(id)
    if not id or id==0 then return true end
    local c=cfg('RogueTeamConditionCfg',id);local p=c.params;local ty=c.type
    if ty==106 then return attr(p[1])>=p[2]
    elseif ty==108 then for _,item in ipairs(p) do if not has(r.items,item) and not treasure(item) then return false end end return true
    elseif ty==109 then for _,item in ipairs(p) do if treasure(item) or has(r.items,item) then return true end end return false
    elseif ty==102 then return has(r.options,p[1])
    elseif ty==103 then return has(r.visits,p[1])
    elseif ty==104 then return (r.stats[tostring(p[1]==1 and 6 or p[1]==2 and 7 or 8)] or 0)>=p[2]
    elseif ty==101 or ty==105 then
        local n=0;for _,t in ipairs(r.treasures) do if has(cfg('RogueTeamItemCfg',t.id).camp,p[1]) then n=n+1 end end return n>=p[2]
    elseif ty==107 then local n=0;for _,item in ipairs(r.items) do if p[1]==99 or cfg('RogueTeamItemCfg',item).sub_type==p[1] then n=n+1 end end return n>=p[2]
    elseif ty==110 then return attr(p[1])<attr(p[2])
    elseif ty==111 or ty==112 then for _,t in ipairs(r.treasures) do if t.rare<4 then return true end end return false
    elseif ty==113 then for _,list in ipairs(p) do local found=false;for _,id in ipairs(list) do if treasure(id) then found=true end end if not found then return false end end return true
    elseif ty==115 then for _,h in ipairs(r.heroes) do if has(p[2],h.hero_id) and h.hp_ratio>=p[1] then return true end end return false
    elseif ty==201 or ty==202 then
        local list=ty==201 and s.his_difficult or s.his_avg
        for _,row in ipairs(list) do if row.key==p[1] and row.value>=p[2] then return true end end return false
    elseif ty==203 then return true
    elseif ty==204 then return has(s.tree_list,p[1])
    elseif ty==205 then return attr(p[1])>=p[2]
    elseif ty==114 then return #r.treasures>=p[1]
    end
    return false
end
-- 为需要删减或升级藏品的事件生成可验证候选。
local function treasure_offer(ty,p)
    local ids={}
    for _,t in ipairs(r.treasures) do if (ty~=8 or t.rare<4) and (not p[1] or t.rare>=p[1]) and (not p[2] or t.rare<=p[2]) then ids[#ids+1]=t.id end end
    offer(ty,choices(ids,3),1)
end
-- 读取按阵营与品质划分的本地兼容池；安装包未包含官服随机池表。
local function effect_pool(code)
    if code>=401 and code<=406 then return pool(4,code-400) end
    if code==301 then return pool(3,0,{1}) end
    if code==302 then return pool(3,0,{2}) end
    if code==305 then
        local ids={};for _,id in ipairs(r.items) do if cfg('RogueTeamItemCfg',id).type==3 and cfg('RogueTeamItemCfg',id).sub_type~=3 then ids[#ids+1]=id end end
        return ids
    end
    if code>=300 and code<400 then return pool(3,0,{1,2}) end
    return pool(4)
end
-- 删除物品及其持续效果，避免客户端读取已经失效的效果来源。
remove_item=function(id)
    local t=treasure(id)
    local list=t and r.treasures or r.items
    local found=false
    for i,v in ipairs(list) do if (t and v.id or v)==id then table.remove(list,i);found=true;break end end
    assert(found,'物品未持有');item_push(id,t and t.rare or 1,2)
    for i=#r.effects,1,-1 do if r.effects[i].source_type==0 and r.effects[i].source_id==id then
        local e=table.remove(r.effects,i)
        if e.attr_id then r.attrs[tostring(e.attr_id)]=math.max(0,attr(e.attr_id)-(e.attr_delta or 0)) end
        e.left_time=0;emit(88017,{effect_list=wire_effects({e})})
    end end
end
-- 记录物品或科技来源，客户端战斗词缀与属性界面依赖正确的 source_id。
local function effect_record(id,source,source_type)
    for _,v in ipairs(r.effects) do if v.effect_id==id then return v end end
    local v={effect_id=id,source_type=source_type or 0,source_id=source,calc_value=0,moment_time=0,left_time=1,trigger_cd=0}
    r.effects[#r.effects+1]=v;return v
end
-- 执行配置中的即时和指定时机效果，其他时机保留至对应事件触发。
effects=function(ids,moment,depth,source,source_type)
    assert((depth or 0)<12,'效果循环过深')
    for _,id in ipairs(type(ids)=='table' and ids or {}) do
        local e=cfg('RogueTeamEffectCfg',id);local p=e.params;local action=e.action
        local record=source and effect_record(id,source,source_type)
        local run=e.moment==moment or moment==1 and e.moment==2
        if e.moment==2 and record and record.applied then run=false end
        if run and record then
            record.moment_time=record.moment_time+1
            if e.gap>0 and record.moment_time%e.gap~=0 then run=false end
        end
        if run then
            if e.moment==2 and record then record.applied=true end
            local a=action>=1000 and action-1000 or action
            if a==1 then
                local delta=p[4];if p[3]==1 then delta=math.floor(attr(p[2])*delta/1000) end
                if p[1]==2 then delta=-delta end
                if p[1]==0 then delta=delta-attr(p[2]) end
                local before=attr(p[2]);change(p[2],delta)
                if e.moment==2 and record then record.attr_id=p[2];record.attr_delta=attr(p[2])-before end
            elseif a==2 then
                for _,h in ipairs(r.heroes) do if h.hp_ratio>0 then
                    local delta=p[1]==0 and math.floor(h.hp_ratio*p[2]/1000) or p[2]*10
                    h.hp_ratio=math.min(10000,math.max(1,h.hp_ratio+delta))
                end end
            elseif a==6 then obtain(p[1],1,(depth or 0)+1)
            elseif a==8 or a==15 then
                local list=effect_pool(p[1])
                if a==8 then for _,item in ipairs(choices(list,p[2] or 1)) do obtain(item,1,(depth or 0)+1) end
                else offer(2,choices(list,3),1) end
            elseif a==14 or a==16 then
                local list=effect_pool(p[1]);local rare=(p[2] or 1)+random((p[3] or p[2] or 1)-(p[2] or 1)+1)-1
                if a==14 then offer(1,choices(list,3),rare)
                else for _,item in ipairs(choices(list,p[4] or 1)) do obtain(item,rare,(depth or 0)+1) end end
            elseif a==9 then
                local ids={};for _,t in ipairs(r.treasures) do ids[#ids+1]=t.id end
                local count=math.min(#ids,p[3] or 1)
                if count>0 then r.queue[#r.queue+1]={event_type=9,param_list=arr({{index=1,param=id,buy_times=count}}),drop_type=0,shop_refresh_times=0} end
            elseif a==10 then
                local ids={};for _,item in ipairs(r.items) do if cfg('RogueTeamItemCfg',item).type==3 then ids[#ids+1]=item end end
                local filtered={};for _,item in ipairs(ids) do if has(p[1],cfg('RogueTeamItemCfg',item).sub_type) then filtered[#filtered+1]=item end end
                local count=math.min(#filtered,p[2] or 1)
                if count>0 then r.queue[#r.queue+1]={event_type=9,param_list=arr({{index=1,param=id,buy_times=count}}),drop_type=0,shop_refresh_times=0} end
            elseif a==12 then
                local ids={};for _,t in ipairs(r.treasures) do if t.rare<4 then ids[#ids+1]=t.id end end
                for _,item in ipairs(choices(ids,p[2] or 1)) do local t=treasure(item);t.rare=math.min(4,t.rare+(p[1] or 1));item_push(item,t.rare,3) end
            elseif a==17 then
                assert(attr(8)>0,'招募次数不足');offer(3,{},1)
            elseif a==19 then r.switch=2
            elseif a==20 then r.switch=3
            elseif a==21 then r.ending=p[1]
            elseif a==22 then r.boss_room=p[1]
                for _,n in ipairs(r.map) do if n.node_type==4 then n.param=cfg('RogueTeamRoomCfg',p[1]).params[1] end end
            elseif a==51 then r.plot_id=p[1];emit(88215,{id=p[1]})
            elseif a==54 then
                change(23,p[1]);if record then record.calc_value=record.calc_value+p[1] end
            elseif a==4 or a==13 or a==52 or a==55 then
                assert(source,'战斗效果缺少来源')
            elseif a==53 then remove_item(p[1])
            elseif a==56 then assert(source,'限时效果缺少来源')
            else error('未实现的虚构推演效果: '..action)
            end
        end
    end
end
-- 触发已持有物品和科技的对应时机效果。
trigger=function(moment)
    for _,item in ipairs(copy(r.items)) do
        if item~=1200004 or moment~=23 then effects(cfg('RogueTeamItemCfg',item).effect_id_list,moment,1,item,0) end
    end
    for _,id in ipairs(s.tree_list) do effects(cfg('RogueTeamSkillTreeCfg',id).effect,moment,1,id,1) end
end
-- 按物品说明推进有限次效果；计数与路线一起存档，重连不会重置。
local function tick_item(id,limit)
    if not has(r.items,id) then return end
    local key=tostring(id);r.effect_ticks[key]=(r.effect_ticks[key] or 0)+1
    if r.effect_ticks[key]==limit then effects(cfg('RogueTeamItemCfg',id).effect_id_list,3,1,id,0) end
end
-- 从当前属性权重抽取战术程序品质，普通程序最高为三级。
local function treasure_rare()
    local total=0;for id=14,17 do total=total+attr(id) end
    local roll=random(total)
    for id=14,17 do roll=roll-attr(id);if roll<=0 then return math.min(3,id-13) end end
end
-- 见闻值达到阈值后排队选择机制，保留超过阈值的余量。
local function mechanism_offer()
    local ids=pool(2);local threshold=attr(20)
    if threshold>0 and attr(1)>=threshold and #ids>0 then
        change(1,-threshold);offer(11,choices(ids,3),1)
    end
end
-- 刷新已达成前置的图鉴条目，图鉴保存物品编号而不是阵营编号。
local function refresh_unlocks()
    for _,item in ipairs(rows('RogueTeamItemCfg')) do
        if item.item_temp==template.item_temp and (item.type==2 or item.type==3 or item.type==4) then
            local c=item.condition~=0 and cfg('RogueTeamConditionCfg',item.condition)
            if (r or not c or c.type>=201) and condition(item.condition) then unique(s.unlock_collection,item.id) end
        end
    end
end
-- 构造事件选项，只下发满足服务端条件的可用选项。
local function event(id)
    local c=cfg('RogueTeamEventCfg',id);r.event_id=id;r.event_plans={};collect(2,id);unique(r.visits,id)
    local options=arr()
    for _,id in ipairs(type(c.option_list)=='table' and c.option_list or {}) do
        local option=cfg('RogueTeamEventOptionCfg',id);local allowed=true
        for _,cond in ipairs(type(option.condition_list)=='table' and option.condition_list or {}) do if not condition(cond) then allowed=false end end
        if allowed then
            local plan=option_plan(id);r.event_plans[tostring(id)]=plan
            options[#options+1]={opt_id=id,item_id_list=plan.items or arr(),stage_id=plan.stage_id or 0,jump_battle_room_id=plan.room_id or 0}
        end
    end
    if #options==0 and type(c.option_list)=='table' and #c.option_list>0 then
        r.event=nil;r.event_id=0;emit(88023,{event_id=0,opt_list=arr(),trigger_type=0});return
    end
    r.event={event_id=id,opt_list=options,trigger_type=0};emit(88023,copy(r.event))
end
-- 创建商店商品；刷新结果和已购买数量随路线保存。
local function shop(refresh)
    local params=arr();local ids={}
    for _,spec in ipairs({{4,13},{3,12}}) do
        local priced={}
        for _,id in ipairs(pool(spec[1])) do
            local price=cfg('RogueTeamItemCfg',id).price
            if type(price)=='table' and #price>0 then priced[#priced+1]=id end
        end
        for _,id in ipairs(choices(priced,attr(spec[2]))) do ids[#ids+1]=id end
    end
    for index,id in ipairs(ids) do
        local discount=100
        if attr(25)>0 then local range=setting(16001);discount=range[1]+random(range[2]-range[1]+1)-1 end
        params[#params+1]={index=index,param=id,rare=1,buy_times=0,discount=discount,is_new=1}
    end
    r.shop={event_type=7,param_list=params,drop_type=0,shop_refresh_times=refresh or 0}
end
-- 所有选择处理后才结束节点，保留未处理的奖励以便断线继续选择。
local function continue_node()
    if not r.pending and #r.queue>0 then r.pending=table.remove(r.queue,1) end
    if not r.pending and not r.event and not r.shop and not r.event_battle and r.selected~=0 then
        if r.finish_after_rewards then r.finish_after_rewards=nil;finish();return end
        trigger(10)
        if node(r.selected).node_type==7 then trigger(11) end
        change(1,math.floor(attr(2)*attr(22)/1000));mechanism_offer()
        if not r.pending and #r.queue>0 then r.pending=table.remove(r.queue,1) end
        if not r.pending then finish() else r.finish_after_rewards=true end
    end
end
'''
